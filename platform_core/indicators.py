"""Reusable indicator library. Every value is computed from real candles.

Each indicator is a pure function ``(DataFrame) -> Series`` and is registered in
``INDICATOR_REGISTRY`` so the UI can offer add/remove/configure without the
calculation code knowing anything about Streamlit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd


def _series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        raise KeyError("Indicator input column missing: " + column)
    return pd.to_numeric(frame[column], errors="coerce").astype(float)


def sma(frame: pd.DataFrame, period: int = 20) -> pd.Series:
    return _series(frame, "close").rolling(period, min_periods=period).mean()


def ema(frame: pd.DataFrame, period: int = 20) -> pd.Series:
    return _series(frame, "close").ewm(span=period, adjust=False, min_periods=period).mean()


def wma(frame: pd.DataFrame, period: int = 20) -> pd.Series:
    close = _series(frame, "close")
    weights = np.arange(1, period + 1, dtype=float)
    weights_sum = weights.sum()
    return close.rolling(period, min_periods=period).apply(
        lambda values: float(np.dot(values, weights) / weights_sum), raw=True
    )


def hma(frame: pd.DataFrame, period: int = 20) -> pd.Series:
    half = max(1, period // 2)
    sqrt_period = max(1, int(np.sqrt(period)))
    raw = 2 * wma(frame, half) - wma(frame, period)
    return raw.rolling(sqrt_period, min_periods=sqrt_period).mean()


def rma(frame: pd.DataFrame, period: int = 20) -> pd.Series:
    return _series(frame, "close").ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def rsi(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    close = _series(frame, "close")
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    value = 100 - (100 / (1 + rs))
    # No losing bars means an all-gain window; a completely flat window is neutral.
    value = value.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    value = value.mask((avg_loss == 0) & (avg_gain == 0), 50.0)
    return value.where(close.notna())


def macd(frame: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    close = _series(frame, "close")
    macd_line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({
        "macd": macd_line,
        "signal": signal_line,
        "histogram": macd_line - signal_line,
    })


def stochastic(frame: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> pd.DataFrame:
    high = _series(frame, "high")
    low = _series(frame, "low")
    close = _series(frame, "close")
    highest = high.rolling(k_period, min_periods=k_period).max()
    lowest = low.rolling(k_period, min_periods=k_period).min()
    span = (highest - lowest).replace(0, np.nan)
    percent_k = 100 * (close - lowest) / span
    return pd.DataFrame({
        "k": percent_k,
        "d": percent_k.rolling(d_period, min_periods=d_period).mean(),
    })


def stochastic_rsi(frame: pd.DataFrame, period: int = 14, k_period: int = 3,
                    d_period: int = 3) -> pd.DataFrame:
    values = rsi(frame, period)
    lowest = values.rolling(period, min_periods=period).min()
    highest = values.rolling(period, min_periods=period).max()
    span = (highest - lowest).replace(0, np.nan)
    raw = 100 * (values - lowest) / span
    smoothed = raw.rolling(k_period, min_periods=k_period).mean()
    return pd.DataFrame({
        "k": smoothed,
        "d": smoothed.rolling(d_period, min_periods=d_period).mean(),
    })


def true_range(frame: pd.DataFrame) -> pd.Series:
    high = _series(frame, "high")
    low = _series(frame, "low")
    close = _series(frame, "close")
    previous_close = close.shift(1)
    ranges = pd.concat([
        high - low,
        (high - previous_close).abs(),
        (low - previous_close).abs(),
    ], axis=1)
    return ranges.max(axis=1)


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(frame).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def bollinger(frame: pd.DataFrame, period: int = 20, deviations: float = 2.0) -> pd.DataFrame:
    close = _series(frame, "close")
    middle = close.rolling(period, min_periods=period).mean()
    deviation = close.rolling(period, min_periods=period).std(ddof=0)
    return pd.DataFrame({
        "middle": middle,
        "upper": middle + deviations * deviation,
        "lower": middle - deviations * deviation,
    })


def keltner(frame: pd.DataFrame, period: int = 20, atr_period: int = 10,
            multiplier: float = 2.0) -> pd.DataFrame:
    middle = ema(frame, period)
    band = atr(frame, atr_period) * multiplier
    return pd.DataFrame({"middle": middle, "upper": middle + band, "lower": middle - band})


def vwap(frame: pd.DataFrame) -> pd.Series:
    typical = (_series(frame, "high") + _series(frame, "low") + _series(frame, "close")) / 3
    volume = _series(frame, "volume")
    cumulative_volume = volume.cumsum().replace(0, np.nan)
    return (typical * volume).cumsum() / cumulative_volume


def volume_ma(frame: pd.DataFrame, period: int = 20) -> pd.Series:
    return _series(frame, "volume").rolling(period, min_periods=period).mean()


def obv(frame: pd.DataFrame) -> pd.Series:
    close = _series(frame, "close")
    volume = _series(frame, "volume").fillna(0.0)
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume).cumsum()


def supertrend(frame: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """ATR-banded trend line. direction is 1 for an uptrend and -1 for a downtrend."""
    average_range = atr(frame, period)
    mid = (_series(frame, "high") + _series(frame, "low")) / 2
    close = _series(frame, "close")
    upper_basic = mid + multiplier * average_range
    lower_basic = mid - multiplier * average_range

    final_upper = upper_basic.copy()
    final_lower = lower_basic.copy()
    direction = pd.Series(np.nan, index=frame.index, dtype=float)
    for i in range(len(frame)):
        if pd.isna(upper_basic.iloc[i]):
            continue
        previous_ready = i > 0 and not pd.isna(final_upper.iloc[i - 1])
        if not previous_ready:
            final_upper.iloc[i] = upper_basic.iloc[i]
            final_lower.iloc[i] = lower_basic.iloc[i]
            direction.iloc[i] = 1.0 if close.iloc[i] > upper_basic.iloc[i] else -1.0
            continue
        previous_close = close.iloc[i - 1]
        broke_upper = (upper_basic.iloc[i] < final_upper.iloc[i - 1]
                       or previous_close > final_upper.iloc[i - 1])
        broke_lower = (lower_basic.iloc[i] > final_lower.iloc[i - 1]
                       or previous_close < final_lower.iloc[i - 1])
        final_upper.iloc[i] = upper_basic.iloc[i] if broke_upper else final_upper.iloc[i - 1]
        final_lower.iloc[i] = lower_basic.iloc[i] if broke_lower else final_lower.iloc[i - 1]
        if direction.iloc[i - 1] == 1.0:
            direction.iloc[i] = 1.0 if close.iloc[i] > final_upper.iloc[i] else -1.0
        else:
            direction.iloc[i] = -1.0 if close.iloc[i] < final_lower.iloc[i] else 1.0

    line = pd.Series(np.where(direction == 1.0, final_lower, final_upper), index=frame.index)
    return pd.DataFrame({"line": line, "direction": direction})


def swing_highs(frame: pd.DataFrame, strength: int = 2) -> pd.Series:
    high = _series(frame, "high")
    centered = high.rolling(strength * 2 + 1, center=True).max()
    mask = high.eq(centered) & high.shift(1).lt(high) & high.shift(-1).lt(high)
    return high.where(mask)


def swing_lows(frame: pd.DataFrame, strength: int = 2) -> pd.Series:
    low = _series(frame, "low")
    centered = low.rolling(strength * 2 + 1, center=True).min()
    mask = low.eq(centered) & low.shift(1).gt(low) & low.shift(-1).gt(low)
    return low.where(mask)


def support_resistance(frame: pd.DataFrame, lookback: int = 100,
                       tolerance: float = 0.0015) -> pd.DataFrame:
    """Cluster recent swing points into practical support/resistance levels."""
    window = frame.tail(lookback)
    levels: list[float] = []
    for value in [*swing_highs(window).dropna(), *swing_lows(window).dropna()]:
        clustered = any(abs(float(value) - level) / max(level, 1e-12) <= tolerance
                        for level in levels)
        if not clustered:
            levels.append(float(value))
    return pd.DataFrame({"level": sorted(set(round(level, 10) for level in levels), reverse=True)})


def previous_high_low(frame: pd.DataFrame) -> pd.DataFrame:
    """Previous UTC day high/low, computed only from completed sessions."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["previous_high", "previous_low"])
    index = pd.to_datetime(frame.index, utc=True)
    daily = frame.groupby(index.floor("D")).agg(high=("high", "max"), low=("low", "min"))
    if len(daily) < 2:
        return pd.DataFrame({"previous_high": [np.nan], "previous_low": [np.nan]})
    previous = daily.iloc[-2]
    return pd.DataFrame({"previous_high": [float(previous["high"])],
                         "previous_low": [float(previous["low"])]})


def session_high_low(frame: pd.DataFrame, start_hour: int = 0,
                     end_hour: int = 24) -> pd.DataFrame:
    index = pd.to_datetime(frame.index, utc=True)
    mask = (index.hour >= start_hour) & (index.hour < end_hour)
    if not mask.any():
        return pd.DataFrame(columns=["session_high", "session_low"])
    session = frame.loc[mask]
    return pd.DataFrame({"session_high": [float(session["high"].max())],
                         "session_low": [float(session["low"].min())]})


@dataclass(frozen=True)
class IndicatorSpec:
    """Declarative description used by the UI and the API layer."""

    id: str
    name: str
    category: str
    func: Callable[..., Any]
    defaults: dict
    outputs: tuple
    overlay: bool = False
    pane: str = "price"
    description: str = ""

    def compute(self, frame: pd.DataFrame, **params) -> pd.DataFrame:
        merged = dict(self.defaults)
        # Unknown parameters are ignored so a UI typo degrades to the default
        # configuration instead of silently removing the indicator.
        merged.update({key: value for key, value in params.items()
                        if value is not None and key in self.defaults})
        result = self.func(frame, **merged)
        if isinstance(result, pd.Series):
            result = result.rename(self.outputs[0]).to_frame()
        for name in self.outputs:
            if name not in result.columns:
                result[name] = np.nan
        return result.loc[:, list(self.outputs)]


INDICATOR_REGISTRY = {
    spec.id: spec
    for spec in (
        IndicatorSpec("SMA", "Simple Moving Average", "Trend", sma, {"period": 20}, ("sma",), True,
                      description="Arithmetic mean of the last N closes."),
        IndicatorSpec("EMA", "Exponential Moving Average", "Trend", ema, {"period": 20}, ("ema",), True,
                      description="Exponentially weighted mean of closes."),
        IndicatorSpec("WMA", "Weighted Moving Average", "Trend", wma, {"period": 20}, ("wma",), True,
                      description="Linearly weighted mean of closes."),
        IndicatorSpec("HMA", "Hull Moving Average", "Trend", hma, {"period": 20}, ("hma",), True,
                      description="Low-lag moving average built from WMA combinations."),
        IndicatorSpec("VWAP", "VWAP", "Trend", vwap, {}, ("vwap",), True,
                      description="Volume weighted average price from the start of the series."),
        IndicatorSpec("SUPERTREND", "Supertrend", "Trend", supertrend,
                      {"period": 10, "multiplier": 3.0}, ("line", "direction"), True,
                      description="ATR-banded trend line with a direction flag."),
        IndicatorSpec("RSI", "Relative Strength Index", "Momentum", rsi, {"period": 14}, ("rsi",), False,
                      "momentum", description="Wilder momentum oscillator bounded 0-100."),
        IndicatorSpec("MACD", "MACD", "Momentum", macd, {"fast": 12, "slow": 26, "signal": 9},
                      ("macd", "signal", "histogram"), False, "momentum",
                      description="Fast and slow EMA spread with a signal line and histogram."),
        IndicatorSpec("STOCHASTIC", "Stochastic", "Momentum", stochastic,
                      {"k_period": 14, "d_period": 3}, ("k", "d"), False, "momentum",
                      description="Close position inside the recent high and low range."),
        IndicatorSpec("STOCHASTIC_RSI", "Stochastic RSI", "Momentum", stochastic_rsi,
                      {"period": 14, "k_period": 3, "d_period": 3}, ("k", "d"), False, "momentum",
                      description="Stochastic oscillator applied to the RSI series."),
        IndicatorSpec("BB", "Bollinger Bands", "Volatility", bollinger,
                      {"period": 20, "deviations": 2.0}, ("upper", "middle", "lower"), True,
                      description="Moving average plus and minus standard deviation bands."),
        IndicatorSpec("KC", "Keltner Channel", "Volatility", keltner,
                      {"period": 20, "atr_period": 10, "multiplier": 2.0},
                      ("upper", "middle", "lower"), True, description="EMA plus and minus ATR bands."),
        IndicatorSpec("ATR", "Average True Range", "Volatility", atr, {"period": 14}, ("atr",), False,
                      "volatility", description="Average true range used for risk sizing."),
        IndicatorSpec("VOLUME_MA", "Volume MA", "Volume", volume_ma, {"period": 20}, ("volume_ma",), True,
                      "volume", description="Rolling mean of traded volume."),
        IndicatorSpec("OBV", "On-Balance Volume", "Volume", obv, {}, ("obv",), True, "volume",
                      description="Signed volume accumulation line."),
        IndicatorSpec("SWING_HIGH", "Swing highs", "Structure", swing_highs, {"strength": 2},
                      ("swing_high",), True, description="Confirmed local price highs."),
        IndicatorSpec("SWING_LOW", "Swing lows", "Structure", swing_lows, {"strength": 2},
                      ("swing_low",), True, description="Confirmed local price lows."),
        IndicatorSpec("SUPPORT_RESISTANCE", "Support and resistance", "Structure", support_resistance,
                      {"lookback": 100, "tolerance": 0.0015}, ("level",), True,
                      description="Clustered swing levels used as S/R references."),
        IndicatorSpec("PREVIOUS_HIGH_LOW", "Previous day high and low", "Structure", previous_high_low,
                      {}, ("previous_high", "previous_low"), True,
                      description="High and low of the previous completed UTC session."),
        IndicatorSpec("SESSION_HIGH_LOW", "Session high and low", "Structure", session_high_low,
                      {"start_hour": 0, "end_hour": 24}, ("session_high", "session_low"), True,
                      description="High and low of the current UTC window."),
    )
}


def available_indicators() -> list:
    """JSON-friendly catalogue for the UI and the API layer."""
    return [
        {
            "id": spec.id,
            "name": spec.name,
            "category": spec.category,
            "outputs": list(spec.outputs),
            "overlay": spec.overlay,
            "pane": spec.pane,
            "defaults": dict(spec.defaults),
            "description": spec.description,
        }
        for spec in INDICATOR_REGISTRY.values()
    ]


def compute_indicator(frame: pd.DataFrame, indicator_id: str, **params) -> pd.DataFrame:
    try:
        spec = INDICATOR_REGISTRY[indicator_id]
    except KeyError as exc:
        raise KeyError("Unknown indicator: " + str(indicator_id)) from exc
    return spec.compute(frame, **params)
