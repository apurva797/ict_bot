"""
Vectorized pandas/numpy technical analysis indicators for the Arjun dashboard.
Guaranteed row-loop free architecture for optimized runtime scaling.
"""
from typing import Tuple
import numpy as np
import pandas as pd

def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = (delta.where(delta > 0, 0.0)).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)

def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"].values
    low = df["low"].values
    close_prev = df["close"].shift(1).values
    tr1 = high - low
    tr2 = np.abs(high - close_prev)
    tr3 = np.abs(low - close_prev)
    tr = np.maximum(tr1, np.maximum(tr2, tr3))
    tr_series = pd.Series(tr, index=df.index)
    return tr_series.ewm(alpha=1/period, adjust=False).mean()

def calculate_bollinger_bands(series: pd.Series, period: int = 20, num_std: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    middle = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = middle + (num_std * std)
    lower = middle - (num_std * std)
    return upper, middle, lower

def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = typical_price * df["volume"]
    dates = df["timestamp"].dt.date
    cum_pv = pv.groupby(dates).cumsum()
    cum_vol = df["volume"].groupby(dates).cumsum()
    vwap = cum_pv / cum_vol.replace(0.0, np.nan)
    return vwap.fillna(typical_price)

def calculate_realized_volatility(series: pd.Series, lookback: int = 24) -> float:
    log_returns = np.log(series / series.shift(1))
    vol = log_returns.tail(lookback).std() * np.sqrt(288)
    return float(np.nan_to_num(vol))

def calculate_max_drawdown(series: pd.Series) -> float:
    cum_max = series.cummax()
    drawdowns = (series - cum_max) / cum_max
    return float(drawdowns.min() * 100.0)
