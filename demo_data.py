"""Public OHLCV retrieval with validated live-provider and bundled-data fallbacks."""

from dataclasses import dataclass
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
import time

import ccxt
import pandas as pd
import requests


LOGGER = logging.getLogger("ict_demo.market_data")
MIN_CANDLES = 100  # Existing ICT signal requires at least 100 finalized candles.
SUPPORTED_INTERVALS = {"5m": 5 * 60_000, "15m": 15 * 60_000, "1h": 60 * 60_000}
COINBASE_PRODUCT_IDS = {
    "BTC/USDT": "BTC-USDT",
    "ETH/USDT": "ETH-USDT",
    "SOL/USDT": "SOL-USDT",
}
SAMPLE_DATA_DIR = Path(__file__).resolve().parent / "sample_data"


class MarketDataError(ValueError):
    """A provider or local sample did not return usable candle data."""


@dataclass(frozen=True)
class MarketDataResult:
    frame: pd.DataFrame
    source: str
    used_fallback: bool


def validate_ohlcv(rows, minimum=35):
    """Validate CCXT-shaped rows and return a UTC-indexed OHLCV DataFrame."""
    if rows is None:
        raise MarketDataError("The market data provider returned no candles.")
    try:
        rows = list(rows)
    except TypeError as exc:
        raise MarketDataError("Market data rows are malformed.") from exc
    if not rows:
        raise MarketDataError("The market data provider returned no candles.")
    if len(rows) < minimum:
        raise MarketDataError(f"Insufficient market data: received {len(rows)} candles; need {minimum}.")
    try:
        frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    except (TypeError, ValueError) as exc:
        raise MarketDataError("Market data rows are malformed.") from exc
    for column in ("timestamp", "open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame.isna().any().any():
        raise MarketDataError("Market data contains missing or non-numeric values.")
    if not all(math.isfinite(float(value)) for value in frame.to_numpy().ravel()):
        raise MarketDataError("Market data contains invalid numeric values.")
    if (frame.timestamp <= 0).any() or any(not float(value).is_integer() for value in frame.timestamp):
        raise MarketDataError("Market data contains invalid timestamps.")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any() or (frame.volume < 0).any():
        raise MarketDataError("Market data contains invalid prices or volume.")
    if (frame.high < frame[["open", "low", "close"]].max(axis=1)).any():
        raise MarketDataError("Market data contains malformed candle highs.")
    if (frame.low > frame[["open", "high", "close"]].min(axis=1)).any():
        raise MarketDataError("Market data contains malformed candle lows.")
    if frame.timestamp.duplicated().any():
        raise MarketDataError("Market data contains duplicate candles.")
    if not frame.timestamp.is_monotonic_increasing:
        raise MarketDataError("Market data timestamps are out of order.")
    try:
        frame["time"] = pd.to_datetime(frame.timestamp, unit="ms", utc=True, errors="raise")
    except (ValueError, TypeError, OverflowError) as exc:
        raise MarketDataError("Market data contains invalid timestamps.") from exc
    frame = frame.set_index("time")
    return frame[["open", "high", "low", "close", "volume"]].astype(float)


def _drop_open_candles(rows, timeframe, now_ms=None):
    """Drop still-forming candles consistently across providers and snapshots."""
    interval_ms = SUPPORTED_INTERVALS[timeframe]
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    return [row for row in rows if int(row[0]) + interval_ms <= now_ms]


def _fetch_binance_ohlcv(symbol, timeframe, limit):
    exchange = ccxt.binance({
        "enableRateLimit": True,
        "timeout": 12_000,
        "options": {"defaultType": "spot"},
    })
    last_error = None
    for attempt in range(2):
        try:
            rows = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            rows = _drop_open_candles(rows, timeframe)
            return validate_ohlcv(rows, minimum=MIN_CANDLES).tail(limit)
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.35)
    raise MarketDataError("Binance did not return valid finalized candles.") from last_error


def _fetch_coinbase_ohlcv(symbol, timeframe, limit, session=None):
    """Fetch public Coinbase Exchange candles in <=300-candle time windows."""
    if symbol not in COINBASE_PRODUCT_IDS:
        raise MarketDataError("The backup provider does not support this market.")
    interval_ms = SUPPORTED_INTERVALS[timeframe]
    now_ms = int(time.time() * 1000)
    end_ms = now_ms // interval_ms * interval_ms
    start_ms = end_ms - (limit + 1) * interval_ms
    client = session or requests.Session()
    product = COINBASE_PRODUCT_IDS[symbol]
    rows_by_time = {}
    cursor = start_ms
    while cursor < end_ms and len(rows_by_time) < limit + 1:
        chunk_end = min(cursor + 299 * interval_ms, end_ms)
        params = {
            "granularity": interval_ms // 1000,
            "start": datetime.fromtimestamp(cursor / 1000, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "end": datetime.fromtimestamp(chunk_end / 1000, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        }
        response = client.get(
            f"https://api.exchange.coinbase.com/products/{product}/candles",
            params=params,
            timeout=12,
            headers={"Accept": "application/json", "User-Agent": "ict-demo-public-market-data/1.0"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise MarketDataError("Coinbase returned a malformed candle response.")
        for candle in payload:
            if not isinstance(candle, (list, tuple)) or len(candle) != 6:
                raise MarketDataError("Coinbase returned a malformed candle row.")
            try:
                timestamp, low, high, open_, close, volume = candle
                timestamp = int(timestamp) * 1000
                row = [timestamp, float(open_), float(high), float(low), float(close), float(volume)]
            except (TypeError, ValueError, OverflowError) as exc:
                raise MarketDataError("Coinbase returned invalid candle values.") from exc
            if cursor <= timestamp <= chunk_end and timestamp + interval_ms <= now_ms:
                rows_by_time[timestamp] = row
        cursor = chunk_end + interval_ms

    rows = [rows_by_time[timestamp] for timestamp in sorted(rows_by_time)][-limit:]
    return validate_ohlcv(rows, minimum=MIN_CANDLES)


def _load_sample_ohlcv(symbol, timeframe, limit):
    """Load only a matching, bundled, clearly non-live public-data snapshot."""
    if symbol not in COINBASE_PRODUCT_IDS or timeframe not in SUPPORTED_INTERVALS:
        raise MarketDataError("No bundled sample is available for the selected market and interval.")
    filename = f"{symbol.split('/')[0]}_USDT_{timeframe}.csv"
    path = SAMPLE_DATA_DIR / filename
    try:
        sample = pd.read_csv(path)
        required = ["timestamp", "open", "high", "low", "close", "volume"]
        if list(sample.columns) != required:
            raise MarketDataError("The bundled sample has an unexpected schema.")
        rows = sample[required].values.tolist()
        rows = _drop_open_candles(rows, timeframe)
        frame = validate_ohlcv(rows, minimum=MIN_CANDLES)
        return frame.tail(limit)
    except MarketDataError:
        raise
    except (OSError, pd.errors.ParserError, ValueError, TypeError) as exc:
        raise MarketDataError("The bundled historical sample could not be loaded.") from exc


def fetch_market_data(symbol="BTC/USDT", timeframe="1h", limit=1000):
    """Try Binance, then Coinbase Exchange, then a matching static CSV snapshot."""
    if timeframe not in SUPPORTED_INTERVALS:
        raise MarketDataError("Unsupported candle interval.")
    if not isinstance(limit, int) or limit < MIN_CANDLES or limit > 1000:
        raise MarketDataError("Requested history must be between 100 and 1,000 candles.")

    providers = (
        ("Binance public OHLCV", _fetch_binance_ohlcv),
        ("Coinbase Exchange public OHLCV", _fetch_coinbase_ohlcv),
        ("Bundled historical sample (not live)", _load_sample_ohlcv),
    )
    failures = []
    for index, (name, provider) in enumerate(providers):
        try:
            frame = provider(symbol, timeframe, limit)
            if len(frame) < MIN_CANDLES:
                raise MarketDataError("Provider returned too few finalized candles.")
            return MarketDataResult(frame=frame, source=name, used_fallback=index > 0)
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}")
            LOGGER.exception("Market-data source %s failed for %s %s", name, symbol, timeframe)

    summary = "; ".join(failures)
    raise MarketDataError(
        "No valid market candles are available for this selection. "
        "The app tried Binance, Coinbase Exchange, and the bundled historical sample. "
        f"Provider diagnostics: {summary}"
    )


def fetch_ohlcv(symbol="BTC/USDT", timeframe="1h", limit=1000):
    """Compatibility helper for older modules that only need the DataFrame."""
    return fetch_market_data(symbol, timeframe, limit).frame
