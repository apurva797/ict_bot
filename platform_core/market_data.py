"""Market data service.

Provider specific code lives inside :class:`MarketDataProvider` implementations
only. Strategies and the UI depend on :class:`MarketDataService`, which
normalizes every dataset, reports data health, and never returns stale or
malformed candles as if they were live.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

import numpy as np
import pandas as pd

from platform_core.errors import PlatformError
from platform_core.settings import MarketConfig
from platform_core.status import DataHealth, Status


LOGGER = logging.getLogger("platform_core.market_data")

REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")
TIMEFRAME_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "12h": 720, "1D": 1440,
}


def timeframe_to_ms(timeframe: str) -> int:
    try:
        return TIMEFRAME_MINUTES[timeframe] * 60_000
    except KeyError as exc:
        raise PlatformError(
            "Unsupported candle interval: " + str(timeframe), Status.DATA_UNAVAILABLE
        ) from exc


def _extract_index(frame: pd.DataFrame) -> pd.DatetimeIndex:
    """Return a UTC DatetimeIndex for a raw provider frame.

    Timestamps may arrive as an existing index, a named column, or Unix
    seconds/milliseconds. Anything unparseable becomes NaT and is dropped later.
    """
    if isinstance(frame.index, pd.DatetimeIndex):
        index = frame.index
    else:
        index = None
        for candidate in ("timestamp", "time", "date", "datetime", "open_time"):
            if candidate in frame.columns:
                index = _coerce_timestamps(frame[candidate])
                frame.drop(columns=[candidate], inplace=True)
                break
        if index is None:
            index = _coerce_timestamps(pd.Index(frame.index))
    return index.tz_convert("UTC")


def _coerce_timestamps(values) -> pd.DatetimeIndex:
    """Parse timestamps, detecting Unix seconds vs milliseconds vs datetimes."""
    numeric = pd.to_numeric(pd.Series(list(values)), errors="coerce")
    if len(numeric) and numeric.notna().all():
        magnitude = float(numeric.abs().median())
        if magnitude > 1e17:
            unit = "ns"
        elif magnitude > 1e11:
            unit = "ms"
        else:
            unit = "s"
        return pd.DatetimeIndex(
            pd.to_datetime(numeric, unit=unit, utc=True, errors="coerce")
        )
    return pd.DatetimeIndex(pd.to_datetime(pd.Series(list(values)), utc=True, errors="coerce"))


class MarketDataProvider(Protocol):
    """A single source of OHLCV candles. Implementations own provider quirks."""

    name: str
    live: bool

    def fetch(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        """Return normalized UTC OHLCV or raise :class:`PlatformError`."""


@dataclass(frozen=True)
class MarketDataSnapshot:
    """A validated dataset plus its truthful health state."""

    frame: pd.DataFrame
    symbol: str
    timeframe: str
    source: str
    health: DataHealth
    is_live: bool
    fetched_at: pd.Timestamp
    age_seconds: float
    last_candle_at: pd.Timestamp
    rows_dropped: int = 0
    notes: tuple[str, ...] = ()

    @property
    def candles(self) -> int:
        return int(len(self.frame))

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "source": self.source,
            "health": self.health.value,
            "is_live": self.is_live,
            "candles": self.candles,
            "fetched_at": self.fetched_at.isoformat(),
            "age_seconds": round(self.age_seconds, 3),
            "last_candle_at": self.last_candle_at.isoformat() if self.last_candle_at is not None else None,
            "rows_dropped": self.rows_dropped,
            "notes": list(self.notes),
        }


def normalize_ohlcv(rows: Any, *, symbol: str = "", timeframe: str = "") -> tuple[pd.DataFrame, int]:
    """Coerce arbitrary provider output into sorted, de-duplicated UTC OHLCV.

    Returns the frame and the number of discarded rows. Invalid rows are removed
    rather than propagated; an empty result is an error, never a fabricated
    candle.
    """
    if rows is None:
        raise PlatformError("The market data provider returned no candles.", Status.DATA_UNAVAILABLE)
    if isinstance(rows, pd.DataFrame):
        frame = rows.copy()
    else:
        try:
            records = list(rows)
        except TypeError as exc:
            raise PlatformError("Market data rows are malformed.", Status.DATA_UNAVAILABLE) from exc
        if not records:
            raise PlatformError("The market data provider returned no candles.",
                                Status.DATA_UNAVAILABLE)
        # CCXT-style rows are positional: [timestamp, open, high, low, close, volume].
        if all(isinstance(row, (list, tuple)) for row in records):
            widths = {len(row) for row in records}
            if widths != {6}:
                raise PlatformError(
                    "Market data rows have an unexpected shape.", Status.DATA_UNAVAILABLE
                )
            frame = pd.DataFrame(
                records, columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
        else:
            frame = pd.DataFrame(records)

    frame.columns = [str(column).strip().lower() for column in frame.columns]
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise PlatformError(
            "Market data is missing required candle fields: " + ", ".join(missing),
            Status.DATA_UNAVAILABLE,
        )

    index = _extract_index(frame)

    frame = frame.loc[:, list(REQUIRED_COLUMNS)].copy()
    frame.index = index
    total_rows = len(frame)
    for column in REQUIRED_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna()
    frame = frame.loc[frame.index.notna()]

    if (frame[["open", "high", "low", "close"]] <= 0).any().any() or (frame["volume"] < 0).any():
        frame = frame.loc[(frame[["open", "high", "low", "close"]] > 0).all(axis=1)
                          & (frame["volume"] >= 0)]
    if (frame["high"] < frame[["open", "low", "close"]].max(axis=1)).any():
        frame = frame.loc[frame["high"] >= frame[["open", "low", "close"]].max(axis=1)]
    if (frame["low"] > frame[["open", "high", "close"]].min(axis=1)).any():
        frame = frame.loc[frame["low"] <= frame[["open", "high", "close"]].min(axis=1)]

    if frame.empty:
        raise PlatformError("Market data contained no valid candles after validation.",
                            Status.DATA_UNAVAILABLE)
    frame = frame.sort_index(kind="stable")
    frame = frame.loc[~frame.index.duplicated(keep="last")]
    frame.index.name = "timestamp"
    return frame.astype(float), int(total_rows - len(frame))


def assess_health(frame: pd.DataFrame, timeframe: str, is_live: bool,
                  config: MarketConfig | None = None) -> tuple[DataHealth, float]:
    """Classify freshness without ever treating stale data as live."""
    config = config or MarketConfig()
    if frame is None or frame.empty:
        return DataHealth.UNAVAILABLE, float("inf")
    age = (pd.Timestamp.now(tz="UTC") - frame.index[-1]).total_seconds()
    threshold = timeframe_to_ms(timeframe) / 1000 * config.stale_interval_multiple + \
        config.stale_grace_seconds
    if age > threshold:
        return DataHealth.STALE, float(age)
    return (DataHealth.CONNECTED if is_live else DataHealth.DEGRADED), float(age)


@dataclass
class ProviderAttempt:
    name: str
    ok: bool
    detail: str = ""
    live: bool = True


@dataclass
class ServiceStats:
    """Observability counters; useful for /health style endpoints."""

    attempts: list[ProviderAttempt] = field(default_factory=list)

    def record(self, attempt: ProviderAttempt) -> None:
        self.attempts.append(attempt)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempts": [vars(attempt) for attempt in self.attempts],
            "fallback_used": any(not attempt.ok for attempt in self.attempts[:1]),
        }


class MarketDataService:
    """Primary provider -> backup provider chain with explicit degradation.

    ``bundled`` providers are accepted only as an explicitly non-live last
    resort; the resulting snapshot is never reported as live market data.
    """

    def __init__(self, providers: list[MarketDataProvider], config: MarketConfig | None = None,
                 min_candles: int | None = None):
        if not providers:
            raise ValueError("At least one market data provider is required")
        self.providers = list(providers)
        self.config = config or MarketConfig()
        self.min_candles = int(min_candles or self.config.min_candles)
        self.stats = ServiceStats()

    def get_snapshot(self, symbol: str, timeframe: str, limit: int | None = None,
                     min_candles: int | None = None) -> MarketDataSnapshot:
        limit = int(limit or self.config.default_candles)
        required = int(min_candles or self.min_candles)
        if limit < required:
            raise PlatformError(
                f"At least {required} finalized candles are required for analysis.",
                Status.INSUFFICIENT_DATA,
            )
        failures: list[str] = []
        saw_insufficient = False
        for provider in self.providers:
            try:
                raw = provider.fetch(symbol, timeframe, limit)
                frame, dropped = normalize_ohlcv(raw, symbol=symbol, timeframe=timeframe)
                if len(frame) < required:
                    saw_insufficient = True
                    raise PlatformError(
                        f"{provider.name} returned {len(frame)} candles; {required} are required.",
                        Status.INSUFFICIENT_DATA,
                    )
                frame = frame.tail(limit)
                health, age = assess_health(frame, timeframe, provider.live, self.config)
                if health is DataHealth.UNAVAILABLE:
                    raise PlatformError("No usable candles were returned.", Status.DATA_UNAVAILABLE)
                self.stats.record(ProviderAttempt(provider.name, True, live=provider.live))
                notes = []
                if dropped:
                    notes.append(f"{dropped} invalid rows were removed")
                if not provider.live:
                    notes.append("Source is not live market data")
                if health is DataHealth.STALE:
                    notes.append("Latest candle is older than the expected interval")
                return MarketDataSnapshot(
                    frame=frame,
                    symbol=symbol,
                    timeframe=timeframe,
                    source=provider.name,
                    health=health,
                    is_live=bool(provider.live),
                    fetched_at=pd.Timestamp.now(tz="UTC"),
                    age_seconds=age,
                    last_candle_at=frame.index[-1],
                    rows_dropped=dropped,
                    notes=tuple(notes),
                )
            except PlatformError as exc:
                failures.append(f"{provider.name}: {exc.message}")
                saw_insufficient = saw_insufficient or exc.status is Status.INSUFFICIENT_DATA
                self.stats.record(ProviderAttempt(provider.name, False, exc.message,
                                                  live=provider.live))
                LOGGER.warning("Market data provider %s failed: %s", provider.name, exc.message)
            except Exception as exc:  # provider SDK raised something unexpected
                failures.append(f"{provider.name}: {type(exc).__name__}")
                self.stats.record(ProviderAttempt(provider.name, False, type(exc).__name__,
                                                  live=provider.live))
                LOGGER.exception("Unexpected market data failure in %s", provider.name)

        # Distinguish "no candles at all" from "not enough history" so the UI
        # can tell the user which problem actually occurred.
        if saw_insufficient and not any(
            "returned no candles" in failure for failure in failures
        ):
            raise PlatformError(
                f"Insufficient market data: fewer than {required} finalized candles "
                "were available from any provider. " + " | ".join(failures),
                Status.INSUFFICIENT_DATA,
                {"providers": [provider.name for provider in self.providers]},
            )
        raise PlatformError(
            "Market data is unavailable from every provider. " + " | ".join(failures),
            Status.DATA_UNAVAILABLE,
            {"providers": [provider.name for provider in self.providers]},
        )

    def get_ohlcv(self, symbol: str, timeframe: str, limit: int | None = None,
                  min_candles: int | None = None) -> pd.DataFrame:
        return self.get_snapshot(symbol, timeframe, limit, min_candles).frame

    def latest_price(self, snapshot: MarketDataSnapshot) -> float:
        return float(snapshot.frame["close"].iloc[-1])

    def price_change_pct(self, snapshot: MarketDataSnapshot, lookback: int = 1) -> float:
        closes = snapshot.frame["close"]
        if len(closes) < lookback + 1:
            return 0.0
        previous = float(closes.iloc[-(lookback + 1)])
        if previous == 0:
            return 0.0
        return (float(closes.iloc[-1]) - previous) / previous * 100


class LiveTickerProvider:
    """Fetches candles from a callable that returns normalized rows.

    This keeps the existing, already working fetchers in ``demo_data`` as the
    provider implementations without duplicating their request logic here.
    """

    def __init__(self, name: str, fetcher, live: bool = True):
        self.name = name
        self.fetcher = fetcher
        self.live = live

    def fetch(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        result = self.fetcher(symbol, timeframe, limit)
        frame = getattr(result, "frame", result)
        if frame is None or len(frame) == 0:
            raise PlatformError(f"{self.name} returned no candles.", Status.DATA_UNAVAILABLE)
        return frame


def default_service(min_candles: int | None = None) -> MarketDataService:
    """Wire the real public providers: Binance -> Coinbase -> bundled sample."""
    import demo_data

    return MarketDataService(
        providers=[
            LiveTickerProvider("Binance public OHLCV", demo_data._fetch_binance_ohlcv),
            LiveTickerProvider("Coinbase Exchange public OHLCV", demo_data._fetch_coinbase_ohlcv),
            LiveTickerProvider("Bundled historical sample (not live)",
                               demo_data._load_sample_ohlcv, live=False),
        ],
        min_candles=min_candles,
    )


def to_candle_rows(frame: pd.DataFrame) -> list[list]:
    """Convert a validated frame into the CCXT-style rows existing strategies use."""
    return [
        [int(timestamp.value // 1_000_000), float(row.open), float(row.high),
         float(row.low), float(row.close), float(row.volume)]
        for timestamp, row in frame.iterrows()
    ]