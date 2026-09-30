"""Public, read-only OHLCV data access and strict validation."""

import math
import time

import ccxt
import pandas as pd


class MarketDataError(ValueError):
    pass


def validate_ohlcv(rows, minimum=35):
    if not rows:
        raise MarketDataError("The market data provider returned no candles.")
    if len(rows) < minimum:
        raise MarketDataError("Insufficient market data for this strategy.")
    try:
        frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    except (TypeError, ValueError) as exc:
        raise MarketDataError("Market data rows are malformed.") from exc
    if frame.isna().any().any():
        raise MarketDataError("Market data contains missing values.")
    for column in ("timestamp", "open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame.isna().any().any() or not all(math.isfinite(float(v)) for v in frame.select_dtypes("number").to_numpy().ravel()):
        raise MarketDataError("Market data contains invalid numeric values.")
    if (frame.timestamp <= 0).any() or any(not float(v).is_integer() for v in frame.timestamp):
        raise MarketDataError("Market data contains invalid timestamps.")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any() or (frame.volume < 0).any():
        raise MarketDataError("Market data contains invalid prices or volume.")
    if (frame.high < frame[["open", "low", "close"]].max(axis=1)).any() or (frame.low > frame[["open", "high", "close"]].min(axis=1)).any():
        raise MarketDataError("Market data contains malformed candles.")
    if frame.timestamp.duplicated().any():
        raise MarketDataError("Market data contains duplicate candles.")
    if not frame.timestamp.is_monotonic_increasing:
        raise MarketDataError("Market data timestamps are out of order.")
    try:
        frame["time"] = pd.to_datetime(frame.timestamp, unit="ms", utc=True, errors="raise")
    except Exception as exc:
        raise MarketDataError("Market data contains invalid timestamps.") from exc
    frame = frame.set_index("time")
    return frame[["open", "high", "low", "close", "volume"]].astype(float)


def fetch_ohlcv(symbol="BTC/USDT", timeframe="1h", limit=1000):
    if limit < 100 or limit > 1000:
        raise MarketDataError("Requested history must be between 100 and 1,000 candles.")
    exchange = ccxt.binance({"enableRateLimit": True, "timeout": 8000, "options": {"defaultType": "spot"}})
    last_error = None
    for attempt in range(2):
        try:
            candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            # Binance may include the currently forming candle; exclude it.
            return validate_ohlcv(candles[:-1], minimum=35)
        except MarketDataError:
            raise
        except (ccxt.NetworkError, ccxt.ExchangeError, TimeoutError, ValueError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.5)
    raise MarketDataError("Market data is temporarily unavailable. Please try again shortly.") from last_error
