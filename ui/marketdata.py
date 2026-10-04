"""Cached market-data access for the UI.

All fetching stays in :mod:`demo_data`; this module only adds the caching and
error-shaping the screens need. A failed fetch becomes an explicit status
object so a screen can render "market data unavailable" instead of a blank
panel or a stale price presented as live.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import pandas as pd
import streamlit as st

import demo_data
from demo_data import MarketDataError, MarketDataResult

# Timeframes the platform offers. These map to intervals the underlying
# providers and the bundled samples already support; an unsupported interval
# fails loudly at the provider rather than silently resampling.
TIMEFRAMES = ("5m", "15m", "1h")

TIMEFRAME_LABELS = {"5m": "5 min", "15m": "15 min", "1h": "1 Hour"}

# Candle budget per purpose. The chart only needs enough history to draw a
# usable range, so it never requests the full backtest budget.
CHART_CANDLES = 500
ANALYSIS_CANDLES = 500
BACKTEST_CANDLES = 1000
WATCHLIST_CANDLES = 120

# The watchlist is the configured market list, not a hard-coded symbol table,
# so adding a market to settings surfaces it here automatically.
DEFAULT_WATCHLIST = ("BTC/USDT", "ETH/USDT", "SOL/USDT")


@dataclass(frozen=True)
class LoadResult:
    """Outcome of one market-data request.

    ``ok`` false means every provider failed; ``error`` carries the reason.
    """

    ok: bool
    frame: pd.DataFrame | None = None
    source: str = ""
    used_fallback: bool = False
    error: str = ""
    symbol: str = ""
    timeframe: str = ""

    @property
    def health(self) -> str:
        """Truthful data-health label. Backup data is never called live."""
        if not self.ok:
            return "UNAVAILABLE"
        return "BACKUP" if self.used_fallback else "LIVE"


@st.cache_data(ttl=60, show_spinner=False)
def load_candles(symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    """Cached OHLCV frame. Raises so Streamlit's cache never stores errors."""
    return demo_data.fetch_market_data(symbol, timeframe, limit=limit).frame


def load(symbol: str, timeframe: str, limit: int = ANALYSIS_CANDLES) -> LoadResult:
    """Fetch candles for a screen, converting failures into a status object.

    Wrapping rather than propagating keeps every screen free of try/except
    noise while still surfacing the real provider message to the user. The
    provider is looked up on :mod:`demo_data` at call time rather than bound at
    import, so the module stays the single source of truth and a caller (or a
    test) can substitute the provider without patching this module too.
    """
    try:
        result: MarketDataResult = demo_data.fetch_market_data(
            symbol, timeframe, limit=limit)
    except MarketDataError as exc:
        return LoadResult(ok=False, error=str(exc), symbol=symbol, timeframe=timeframe)
    except (ValueError, TypeError) as exc:
        return LoadResult(ok=False, error=f"{type(exc).__name__}: {exc}",
                          symbol=symbol, timeframe=timeframe)
    if result is None or result.frame is None or result.frame.empty:
        return LoadResult(ok=False, error="The provider returned no candles.",
                          symbol=symbol, timeframe=timeframe)
    return LoadResult(ok=True, frame=result.frame, source=result.source,
                      used_fallback=result.used_fallback,
                      symbol=symbol, timeframe=timeframe)


def is_live(result: LoadResult) -> bool:
    """True only when candles came from a real provider, not bundled data."""
    return bool(result.ok and not result.used_fallback and result.source
                and not result.source.startswith("Bundled"))


def chart_snapshot(result: LoadResult) -> dict:
    """The slice of candles the chart component needs.

    Tail-truncating keeps the component payload proportional to what is
    actually visible rather than to the whole fetched history.
    """
    if not result.ok or result.frame is None:
        return {}
    return {
        "frame": result.frame.tail(CHART_CANDLES).copy(),
        "symbol": result.symbol,
        "timeframe": result.timeframe,
        "source": result.source,
    }


def publish_chart_snapshot(result: LoadResult) -> None:
    """Record the market actually loaded for the stable chart slot."""
    snapshot = chart_snapshot(result)
    if snapshot:
        st.session_state.chart_snapshot = snapshot


def live_snapshot(symbol: str, timeframe: str) -> dict | None:
    """A fresh ticker snapshot, or ``None`` when it cannot be fetched.

    The caller must treat ``None`` as "no live update available" and keep the
    last known state on screen rather than clearing it.
    """
    try:
        return demo_data.fetch_live_market_snapshot(symbol, timeframe)
    except (MarketDataError, ValueError, TypeError):
        return None


def series_changes(frame: pd.DataFrame, *, bars: int = 24) -> dict:
    """Recent close series plus absolute and percentage change for a row."""
    empty = {"history": (), "change_abs": None, "change_pct": None, "price": None}
    if frame is None or frame.empty or "close" not in frame.columns:
        return empty
    closes = frame["close"].dropna()
    if closes.empty:
        return empty
    price = float(closes.iloc[-1])
    change_abs = change_pct = None
    if len(closes) > 1:
        previous = float(closes.iloc[-2])
        change_abs = price - previous
        if previous:
            change_pct = (price - previous) / previous * 100
    return {
        "history": tuple(float(value) for value in closes.tail(bars)),
        "change_abs": change_abs,
        "change_pct": change_pct,
        "price": price,
    }


def watchlist(symbols: Sequence[str] | None = None) -> tuple[str, ...]:
    """The instruments shown in Market Watch."""
    return tuple(symbols) if symbols else DEFAULT_WATCHLIST


def price_decimals(symbol: str) -> int:
    """Display precision appropriate to a symbol's price magnitude."""
    try:
        price = float(demo_data._load_sample_ohlcv(symbol, "5m", 5)["close"].iloc[-1])
    except Exception:
        return 2
    if price >= 1000:
        return 2
    return 4 if price >= 1 else 6