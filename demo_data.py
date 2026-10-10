"""Public OHLCV retrieval with validated live-provider and bundled-data fallbacks."""

from dataclasses import dataclass
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from io import BytesIO, StringIO
import time

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
MAX_HISTORICAL_CANDLES = 50_000
HISTORICAL_CACHE_DIR = SAMPLE_DATA_DIR / "cache"



def __getattr__(name):
    """Import the heavy ``ccxt`` SDK only when a Binance call is actually made.

    ``ccxt`` costs a few hundred milliseconds to import but is only needed by the
    primary provider, so it is resolved lazily. The module attribute stays
    available (and patchable) for tests.
    """
    if name == "ccxt":
        import ccxt as _ccxt

        globals()["ccxt"] = _ccxt
        return _ccxt
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    # Local import keeps the heavy SDK off the app startup path. The object is
    # the same module instance, so tests can still patch ``demo_data.ccxt``.
    import ccxt

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


def load_uploaded_ohlcv(uploaded_file):
    """Parse and validate a user supplied timestamp/open/high/low/close/volume CSV."""
    if uploaded_file is None:
        raise MarketDataError("Choose an OHLCV CSV file first.")
    try:
        content = uploaded_file.getvalue() if hasattr(uploaded_file, "getvalue") else uploaded_file
        if isinstance(content, bytes):
            frame = pd.read_csv(BytesIO(content))
        elif isinstance(content, str):
            frame = pd.read_csv(StringIO(content))
        else:
            frame = pd.read_csv(content)
    except (OSError, pd.errors.ParserError, TypeError, ValueError) as exc:
        raise MarketDataError("The uploaded historical CSV could not be read.") from exc

    frame.columns = [str(column).strip().lower() for column in frame.columns]
    required = ["timestamp", "open", "high", "low", "close", "volume"]
    if not set(required).issubset(frame.columns):
        raise MarketDataError("CSV must contain timestamp, open, high, low, close, and volume columns.")
    try:
        timestamps = frame["timestamp"]
        if pd.api.types.is_numeric_dtype(timestamps):
            numeric = pd.to_numeric(timestamps, errors="coerce")
            # Accept Unix seconds or milliseconds, as commonly exported by exchanges.
            unit = "ms" if numeric.dropna().abs().median() > 10**11 else "s"
            parsed = pd.to_datetime(numeric, unit=unit, utc=True, errors="coerce")
        else:
            parsed = pd.to_datetime(timestamps, utc=True, errors="coerce")
        if parsed.isna().any():
            raise ValueError("invalid timestamp")
        frame["timestamp"] = parsed.map(lambda value: int(value.timestamp() * 1000))
        rows = frame[required].values.tolist()
        return validate_ohlcv(rows, minimum=1)
    except MarketDataError:
        raise
    except (TypeError, ValueError, OverflowError) as exc:
        raise MarketDataError("The uploaded CSV contains invalid timestamps or OHLCV values.") from exc


def _historical_binance(symbol, timeframe, start_ms, end_ms, max_candles):
    import ccxt

    exchange = ccxt.binance({"enableRateLimit": True, "timeout": 12_000,
                             "options": {"defaultType": "spot"}})
    interval_ms = SUPPORTED_INTERVALS[timeframe]
    rows, since = [], start_ms
    while since < end_ms and len(rows) < max_candles:
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since,
                                     limit=min(1000, max_candles - len(rows)))
        if not batch:
            break
        rows.extend(row for row in batch if start_ms <= int(row[0]) < end_ms)
        last_ms = int(batch[-1][0])
        if last_ms < since:
            break
        since = last_ms + interval_ms
        if len(batch) < 1000:
            break
    rows_by_timestamp = {int(row[0]): row for row in rows}
    finalized = [rows_by_timestamp[key] for key in sorted(rows_by_timestamp)
                 if key + interval_ms <= end_ms]
    return validate_ohlcv(finalized, minimum=100)


def _historical_coinbase(symbol, timeframe, start_ms, end_ms):
    if symbol not in COINBASE_PRODUCT_IDS:
        raise MarketDataError("Coinbase does not support the selected market.")
    interval_ms = SUPPORTED_INTERVALS[timeframe]
    client = requests.Session()
    rows_by_timestamp = {}
    cursor = start_ms
    while cursor < end_ms:
        chunk_end = min(cursor + 299 * interval_ms, end_ms)
        params = {
            "granularity": interval_ms // 1000,
            "start": datetime.fromtimestamp(cursor / 1000, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "end": datetime.fromtimestamp(chunk_end / 1000, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        }
        response = client.get(
            f"https://api.exchange.coinbase.com/products/{COINBASE_PRODUCT_IDS[symbol]}/candles",
            params=params, timeout=12,
            headers={"Accept": "application/json", "User-Agent": "ict-demo-public-market-data/1.0"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise MarketDataError("Coinbase returned a malformed candle response.")
        for candle in payload:
            if not isinstance(candle, (list, tuple)) or len(candle) != 6:
                raise MarketDataError("Coinbase returned a malformed candle row.")
            timestamp, low, high, open_, close, volume = candle
            timestamp_ms = int(timestamp) * 1000
            if start_ms <= timestamp_ms < end_ms and timestamp_ms + interval_ms <= end_ms:
                rows_by_timestamp[timestamp_ms] = [timestamp_ms, open_, high, low, close, volume]
        cursor = chunk_end
    return validate_ohlcv([rows_by_timestamp[key] for key in sorted(rows_by_timestamp)], minimum=100)


def fetch_historical_market_data(symbol, timeframe, start, end, csv_data=None,
                                max_candles=MAX_HISTORICAL_CANDLES):
    """Fetch a UTC date range from public OHLCV history, or filter an uploaded CSV.

    `end` is exclusive. CSV input takes priority when supplied. No live quote or
    paper-account state is used by this historical data interface.
    """
    if timeframe not in SUPPORTED_INTERVALS:
        raise MarketDataError("Unsupported historical candle interval.")
    try:
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        start_ts = start_ts.tz_localize("UTC") if start_ts.tzinfo is None else start_ts.tz_convert("UTC")
        end_ts = end_ts.tz_localize("UTC") if end_ts.tzinfo is None else end_ts.tz_convert("UTC")
    except (TypeError, ValueError) as exc:
        raise MarketDataError("Choose a valid historical start and end date.") from exc
    if start_ts >= end_ts:
        raise MarketDataError("Historical start must be earlier than end.")
    interval_ms = SUPPORTED_INTERVALS[timeframe]
    expected = int((end_ts - start_ts).total_seconds() * 1000 // interval_ms)
    if expected > max_candles:
        raise MarketDataError(f"Date range exceeds {max_candles:,} candles; shorten the range or upload a CSV.")
    start_ms, end_ms = int(start_ts.timestamp() * 1000), int(end_ts.timestamp() * 1000)

    if csv_data is not None:
        frame = load_uploaded_ohlcv(csv_data) if not isinstance(csv_data, pd.DataFrame) else _validate_ohlcv_frame(csv_data)
        latest_finalized = pd.Timestamp.now(tz="UTC") - pd.Timedelta(milliseconds=interval_ms)
        frame = frame.loc[frame.index <= latest_finalized]
        frame = frame.loc[(frame.index >= start_ts) & (frame.index < end_ts)]
        if len(frame) < 100:
            raise MarketDataError("The uploaded CSV has fewer than 100 valid candles in the selected date range.")
        return MarketDataResult(frame=frame, source="Uploaded OHLCV CSV", used_fallback=False)

    cache_file = None
    try:
        HISTORICAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        clean_sym = symbol.replace("/", "_").replace("-", "_")
        cache_file = HISTORICAL_CACHE_DIR / f"{clean_sym}_{timeframe}_{start_ts.strftime('%Y%m%d')}_{end_ts.strftime('%Y%m%d')}.csv"
        for existing in sorted(HISTORICAL_CACHE_DIR.glob(f"{clean_sym}_{timeframe}_*.csv"), reverse=True):
            try:
                cached_frame = load_uploaded_ohlcv(existing)
                if cached_frame.index[0] <= start_ts + pd.Timedelta(hours=3) and cached_frame.index[-1] >= end_ts - pd.Timedelta(hours=3):
                    sub = cached_frame.loc[(cached_frame.index >= start_ts) & (cached_frame.index < end_ts)]
                    if len(sub) >= 100:
                        return MarketDataResult(frame=sub, source=f"Cached historical OHLCV ({existing.name})", used_fallback=False)
            except Exception:
                continue
    except Exception as exc:
        LOGGER.debug("Historical cache lookup failed: %s", exc)

    finalized_end_ms = min(end_ms, int(time.time() * 1000))
    providers = (
        ("Binance public historical OHLCV", lambda: _historical_binance(symbol, timeframe, start_ms, finalized_end_ms, max_candles)),
        ("Coinbase Exchange public historical OHLCV", lambda: _historical_coinbase(symbol, timeframe, start_ms, finalized_end_ms)),
    )
    failures = []
    for index, (name, provider) in enumerate(providers):
        try:
            frame = provider()
            frame = frame.loc[(frame.index >= start_ts) & (frame.index < end_ts)]
            if len(frame) < 100:
                raise MarketDataError("Provider returned fewer than 100 historical candles in range.")
            if cache_file is not None and index == 0:
                try:
                    out = frame.reset_index()
                    out.rename(columns={"index": "timestamp", "time": "timestamp"}, inplace=True)
                    out.to_csv(cache_file, index=False)
                except Exception as save_exc:
                    LOGGER.debug("Historical cache save failed: %s", save_exc)
            return MarketDataResult(frame=frame, source=name, used_fallback=index > 0)
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}")
            LOGGER.warning("Historical source %s failed: %s", name, type(exc).__name__)

    try:
        sample = _load_sample_ohlcv(symbol, timeframe, min(max_candles, 1000))
        sample = sample.loc[(sample.index >= start_ts) & (sample.index < end_ts)]
        if len(sample) >= 100:
            return MarketDataResult(sample, "Bundled historical sample CSV (not live)", True)
    except Exception as exc:
        failures.append(f"Bundled sample: {type(exc).__name__}")
    raise MarketDataError("No historical candles are available for that range. Upload an OHLCV CSV. " + "; ".join(failures))


_SNAPSHOT_CACHE = {}
_SNAPSHOT_CACHE_TTL_SECONDS = 15
# The UI monitor must not freeze the page while a public endpoint is slow.
_SNAPSHOT_REQUEST_TIMEOUT = 5


def fetch_live_market_snapshot(symbol="BTC/USDT", timeframe="5m", session=None):
    """Fetch a fresh Coinbase ticker and current candle for paper-position monitoring.

    The UI monitor re-runs on a timer, so an uncached call here would block the
    page on two sequential network round-trips every few seconds. Results are
    memoised for a short TTL (and failures are remembered briefly) to keep the
    UI responsive and to avoid hammering the public endpoint. A caller-supplied
    ``session`` is never cached, so tests and diagnostics stay exact.
    """
    if symbol not in COINBASE_PRODUCT_IDS or timeframe not in SUPPORTED_INTERVALS:
        raise MarketDataError("Coinbase live monitoring does not support this market or timeframe.")
    cache_key = (symbol, timeframe)
    if session is None:
        cached = _SNAPSHOT_CACHE.get(cache_key)
        if cached is not None:
            value, stored_at = cached
            if (time.time() - stored_at) < _SNAPSHOT_CACHE_TTL_SECONDS:
                if isinstance(value, MarketDataError):
                    raise value
                return dict(value, candle=dict(value["candle"]))
    client = session or requests.Session()
    product = COINBASE_PRODUCT_IDS[symbol]
    try:
        response = client.get(f"https://api.exchange.coinbase.com/products/{product}/ticker",
                              timeout=_SNAPSHOT_REQUEST_TIMEOUT, headers={"Accept": "application/json"})
        response.raise_for_status()
        ticker = response.json()
        price = float(ticker["price"])
        updated_at = pd.Timestamp(ticker["time"])
        updated_at = updated_at.tz_localize("UTC") if updated_at.tzinfo is None else updated_at.tz_convert("UTC")
        now = pd.Timestamp.now(tz="UTC")
        age_seconds = (now - updated_at).total_seconds()
        if age_seconds < -30 or age_seconds > 180:
            raise MarketDataError(f"Coinbase ticker is stale ({age_seconds:.0f} seconds old).")
        interval_ms = SUPPORTED_INTERVALS[timeframe]
        now_ms = int(now.timestamp() * 1000)
        end_ms = now_ms // interval_ms * interval_ms + interval_ms
        start_ms = end_ms - 2 * interval_ms
        candle_response = client.get(
            f"https://api.exchange.coinbase.com/products/{product}/candles",
            params={
                "granularity": interval_ms // 1000,
                "start": datetime.fromtimestamp(start_ms / 1000, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                "end": datetime.fromtimestamp(end_ms / 1000, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            },
            timeout=_SNAPSHOT_REQUEST_TIMEOUT, headers={"Accept": "application/json"},
        )
        candle_response.raise_for_status()
        payload = candle_response.json()
        candidates = [row for row in payload if isinstance(row, (list, tuple)) and len(row) == 6]
        if not candidates:
            raise MarketDataError("Coinbase returned no current OHLC candle for monitoring.")
        current_bucket = now_ms // interval_ms * interval_ms // 1000
        row = next((item for item in candidates if int(item[0]) == current_bucket), None)
        if row is None:
            raise MarketDataError("Coinbase returned no candle for the current monitoring interval.")
        timestamp, low, high, open_, close, volume = row
        candle = {"timestamp": pd.to_datetime(int(timestamp), unit="s", utc=True),
                  "open": float(open_), "high": float(high), "low": float(low),
                  "close": float(close), "volume": float(volume)}
        values = [price, candle["open"], candle["high"], candle["low"], candle["close"], candle["volume"]]
        if not all(math.isfinite(value) for value in values) or min(values[:5]) <= 0 or candle["volume"] < 0:
            raise MarketDataError("Coinbase returned invalid current market values.")
        if candle["high"] < max(candle["open"], candle["low"], candle["close"]) or candle["low"] > min(candle["open"], candle["high"], candle["close"]):
            raise MarketDataError("Coinbase returned a malformed current OHLC candle.")
        snapshot = {"symbol": symbol, "timeframe": timeframe, "price": price,
                    "updated_at": updated_at, "source": "Coinbase Exchange public market data",
                    "candle": candle}
    except MarketDataError as exc:
        if session is None:
            _SNAPSHOT_CACHE[cache_key] = (exc, time.time())
        raise
    except Exception as exc:
        if session is None:
            failure = MarketDataError(
                f"Coinbase live market data unavailable: {type(exc).__name__}: {exc}")
            _SNAPSHOT_CACHE[cache_key] = (failure, time.time())
            raise failure from exc
        raise MarketDataError(f"Coinbase live market data unavailable: {type(exc).__name__}: {exc}") from exc
    if session is None:
        _SNAPSHOT_CACHE[cache_key] = (snapshot, time.time())
    return snapshot


def _validate_ohlcv_frame(frame):
    if not isinstance(frame, pd.DataFrame):
        raise MarketDataError("Historical CSV data must be a table.")
    required = ["open", "high", "low", "close", "volume"]
    if not set(required).issubset(frame.columns):
        raise MarketDataError("CSV must contain timestamp, open, high, low, close, and volume columns.")
    try:
        copy = frame[required].copy()
        index = pd.to_datetime(frame.index, utc=True, errors="raise")
        rows = [[int(ts.timestamp() * 1000), *[float(value) for value in row]]
                for ts, row in zip(index, copy.itertuples(index=False, name=None))]
        return validate_ohlcv(rows, minimum=1)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MarketDataError("The uploaded CSV contains invalid timestamps or OHLCV values.") from exc


def fetch_ohlcv(symbol="BTC/USDT", timeframe="1h", limit=1000):
    """Compatibility helper for older modules that only need the DataFrame."""
    return fetch_market_data(symbol, timeframe, limit).frame

