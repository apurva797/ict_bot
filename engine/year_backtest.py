"""ARJUNA one-year historical backtesting add-on.

Uses OHLCV candles only. Signals are computed from information available at candle close
and entries are filled at the next candle open to reduce look-ahead bias.

Dependencies: pandas, numpy, ccxt (Streamlit is only needed for the optional UI module).
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np
import pandas as pd

try:
    import ccxt
except ImportError:  # Allows tests / local CSV usage without ccxt installed.
    ccxt = None

OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
TIMEFRAME_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
    "4h": 14_400_000, "1d": 86_400_000,
}


@dataclass
class BacktestConfig:
    symbol: str = "BTC/USDT"
    timeframe: str = "5m"
    days: int = 365
    starting_capital: float = 100_000.0
    risk_per_trade: float = 0.01
    commission_rate: float = 0.0006  # per side; configure for your venue/account
    slippage_rate: float = 0.0005    # adverse fill assumption per side
    min_rr: float = 1.5
    preferred_rr: float = 2.0
    leverage: float = 1.0
    max_position_fraction: float = 1.0
    allow_short: bool = True
    max_trades: int = 100_000
    exchange_id: str = "binance"
    cache_dir: str = "sample_data/cache"
    data_source: str = "auto"  # auto, binance, coinbase, csv
    csv_path: Optional[str] = None
    end_date: Optional[str] = None  # ISO date; None means now (UTC)
    gap_policy: str = "report"  # report or fail
    strategy: str = "ema_trend"
    fast_ema: int = 20
    slow_ema: int = 50
    rsi_period: int = 14
    rsi_buy_below: float = 30.0
    rsi_sell_above: float = 70.0
    breakout_period: int = 20
    stop_atr_multiple: float = 2.0
    atr_period: int = 14
    target_rr: Optional[float] = None
    warmup_bars: int = 100

    def validate(self) -> None:
        if self.timeframe not in TIMEFRAME_MS:
            raise ValueError(f"Unsupported timeframe: {self.timeframe}. Supported: {', '.join(TIMEFRAME_MS)}")
        if not 1 <= int(self.days) <= 365:
            raise ValueError("days must be between 1 and 365")
        if self.starting_capital <= 0:
            raise ValueError("starting_capital must be positive")
        if not 0 < self.risk_per_trade <= 0.05:
            raise ValueError("risk_per_trade must be > 0 and <= 5%")
        if self.commission_rate < 0 or self.slippage_rate < 0:
            raise ValueError("commission and slippage cannot be negative")
        if self.min_rr < 1 or self.preferred_rr < self.min_rr:
            raise ValueError("preferred_rr must be >= min_rr >= 1")
        if self.leverage < 1:
            raise ValueError("leverage must be >= 1")
        if not 0 < self.max_position_fraction <= 1:
            raise ValueError("max_position_fraction must be in (0, 1]")
        if self.fast_ema >= self.slow_ema:
            raise ValueError("fast_ema must be smaller than slow_ema")
        if self.gap_policy not in {"report", "fail"}:
            raise ValueError("gap_policy must be 'report' or 'fail'")


@dataclass
class Trade:
    strategy: str
    symbol: str
    timeframe: str
    side: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    quantity: float
    stop_price: float
    target_price: float
    gross_pnl: float
    fees: float
    net_pnl: float
    return_pct_on_equity: float
    r_multiple: float
    exit_reason: str
    bars_held: int


@dataclass
class BacktestResult:
    config: dict
    metrics: dict
    trades: list[dict]
    equity_curve: list[dict]
    data_quality: dict
    warnings: list[str] = field(default_factory=list)

    def to_json(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(asdict(self), indent=2, default=str), encoding="utf-8")

    def trades_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.trades)

    def equity_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.equity_curve)


# ------------------------------- Data layer ---------------------------------

def _utc_timestamp(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def normalize_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalize a local or exchange OHLCV frame."""
    if frame is None or frame.empty:
        raise ValueError("No historical candles returned")
    df = frame.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    # Common timestamp aliases
    if "timestamp" not in df.columns:
        for alias in ("time", "datetime", "date", "open_time"):
            if alias in df.columns:
                df = df.rename(columns={alias: "timestamp"})
                break
    missing = set(OHLCV_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"Historical data missing columns: {sorted(missing)}")
    ts = df["timestamp"]
    if pd.api.types.is_numeric_dtype(ts):
        # Treat numeric timestamps as milliseconds when they are epoch-sized.
        unit = "ms" if float(pd.Series(ts).dropna().median()) > 10_000_000_000 else "s"
        df["timestamp"] = pd.to_datetime(ts, unit=unit, utc=True, errors="coerce")
    else:
        df["timestamp"] = pd.to_datetime(ts, utc=True, errors="coerce")
    for col in OHLCV_COLUMNS[1:]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=OHLCV_COLUMNS).sort_values("timestamp")
    df = df.drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)
    if df.empty:
        raise ValueError("No valid OHLCV rows after normalization")
    invalid = (df["open"] <= 0) | (df["high"] <= 0) | (df["low"] <= 0) | (df["close"] <= 0) | (df["volume"] < 0)
    invalid |= df["high"] < df[["open", "close", "low"]].max(axis=1)
    invalid |= df["low"] > df[["open", "close", "high"]].min(axis=1)
    if invalid.any():
        raise ValueError(f"Found {int(invalid.sum())} invalid OHLCV candles")
    return df[OHLCV_COLUMNS].reset_index(drop=True)


def _cache_name(config: BacktestConfig, start: pd.Timestamp, end: pd.Timestamp, exchange_id: str) -> str:
    safe_symbol = config.symbol.replace("/", "_").replace(":", "_")
    return f"{safe_symbol}_{config.timeframe}_{start.strftime('%Y%m%dT%H%M')}_{end.strftime('%Y%m%dT%H%M')}_{exchange_id}.csv"


def _exchange_ohlcv(exchange_id: str, symbol: str, timeframe: str,
                    start_ms: int, end_ms: int, rate_limit: bool = True) -> pd.DataFrame:
    if ccxt is None:
        raise RuntimeError("ccxt is not installed; install it with: python -m pip install ccxt")
    exchange_cls = getattr(ccxt, exchange_id, None)
    if exchange_cls is None:
        raise ValueError(f"ccxt exchange '{exchange_id}' is not supported")
    exchange = exchange_cls({"enableRateLimit": rate_limit, "timeout": 30_000})
    if not exchange.has.get("fetchOHLCV"):
        raise RuntimeError(f"{exchange_id} does not support fetchOHLCV")
    if exchange.timeframes and timeframe not in exchange.timeframes:
        raise ValueError(f"{exchange_id} does not support timeframe {timeframe}")
    rows: list[list] = []
    cursor = start_ms
    step = TIMEFRAME_MS[timeframe]
    limit = min(int(getattr(exchange, "rateLimit", 1000) or 1000), 1000)
    # Most CCXT exchanges accept limit=1000; paginate using the last candle timestamp.
    while cursor < end_ms:
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=1000)
        if not batch:
            break
        fresh = [r for r in batch if start_ms <= int(r[0]) < end_ms]
        rows.extend(fresh)
        last_ms = int(batch[-1][0])
        if last_ms < cursor or last_ms + step <= cursor:
            break
        next_cursor = last_ms + step
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if rate_limit:
            time.sleep(max(0, float(getattr(exchange, "rateLimit", 0) or 0)) / 1000.0)
        # Safety against broken exchange responses / runaway loops.
        if len(rows) > 2_000_000:
            raise RuntimeError("Aborted download: unexpectedly large candle count")
    if not rows:
        raise RuntimeError(f"No OHLCV candles received from {exchange_id} for {symbol} {timeframe}")
    frame = pd.DataFrame(rows, columns=["timestamp_ms", "open", "high", "low", "close", "volume"])
    frame["timestamp"] = pd.to_datetime(frame.pop("timestamp_ms"), unit="ms", utc=True)
    return normalize_ohlcv(frame)


def load_historical_data(config: BacktestConfig,
                         start: Optional[pd.Timestamp] = None,
                         end: Optional[pd.Timestamp] = None) -> tuple[pd.DataFrame, dict, list[str]]:
    """Load historical candles and validate exact timestamp coverage."""
    config.validate()

    end_ts = _utc_timestamp(config.end_date) if config.end_date else pd.Timestamp.now(tz="UTC")
    end_ts = end_ts.floor("min")
    start_ts = _utc_timestamp(start if start is not None else end_ts - pd.Timedelta(days=config.days))
    end_ts = _utc_timestamp(end if end is not None else end_ts)

    if start_ts >= end_ts:
        raise ValueError("start date must be earlier than end date")

    step_ms = TIMEFRAME_MS[config.timeframe]
    step = pd.Timedelta(milliseconds=step_ms)
    start_ms = int(start_ts.timestamp() * 1000)
    end_ms = int(end_ts.timestamp() * 1000)
    first_ms = ((start_ms + step_ms - 1) // step_ms) * step_ms

    expected_index = pd.date_range(
        start=pd.to_datetime(first_ms, unit="ms", utc=True),
        end=end_ts - pd.Timedelta(nanoseconds=1),
        freq=step,
    ) if first_ms < end_ms else pd.DatetimeIndex([], tz="UTC")

    expected = len(expected_index)
    if expected == 0:
        raise ValueError("Requested interval contains no complete timeframe candles")

    cache_dir = Path(config.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    warnings = []
    errors = []
    best_partial = None

    if config.data_source == "csv":
        candidates = ["csv"]
    elif config.data_source in {"binance", "coinbase", "bybit"}:
        candidates = [config.data_source]
    else:
        candidates = list(dict.fromkeys([
            config.exchange_id or "binance", "bybit", "coinbase"
        ]))

    if config.csv_path:
        candidates = ["csv"] + [x for x in candidates if x != "csv"]

    def assess(raw):
        frame = normalize_ohlcv(raw)
        frame = frame[
            (frame.timestamp >= start_ts) & (frame.timestamp < end_ts)
        ].copy()
        frame = normalize_ohlcv(frame)

        actual_index = pd.DatetimeIndex(frame["timestamp"])
        missing = expected_index.difference(actual_index)
        unexpected = actual_index.difference(expected_index)
        matched = len(expected_index.intersection(actual_index))

        quality = {
            "symbol": config.symbol,
            "timeframe": config.timeframe,
            "requested_start_utc": start_ts.isoformat(),
            "requested_end_utc": end_ts.isoformat(),
            "actual_start_utc": frame.timestamp.min().isoformat(),
            "actual_end_utc": frame.timestamp.max().isoformat(),
            "expected_candles_estimate": expected,
            "actual_candles": len(frame),
            "missing_candles_estimate": len(missing),
            "unexpected_candles": len(unexpected),
            "coverage_pct_estimate": round(100.0 * matched / expected, 3),
        }
        complete = len(missing) == 0 and len(unexpected) == 0
        return frame, quality, complete

    for source in candidates:
        cache_path = cache_dir / _cache_name(config, start_ts, end_ts, source)

        try:
            label = source
            frame = None

            if source == "csv":
                if not config.csv_path:
                    raise ValueError("CSV source selected but csv_path was not provided")
                frame = normalize_ohlcv(pd.read_csv(config.csv_path))
                label = f"csv:{config.csv_path}"
            else:
                if cache_path.exists():
                    try:
                        cached = pd.read_csv(cache_path)
                        cached, _, cached_complete = assess(cached)
                        if cached_complete:
                            frame = cached
                            label = f"cache:{source}"
                        else:
                            warnings.append(f"Ignoring incomplete cache for {source}")
                    except Exception as exc:
                        warnings.append(f"Ignoring invalid cache for {source}: {exc}")

                if frame is None:
                    frame = _exchange_ohlcv(
                        source, config.symbol, config.timeframe, start_ms, end_ms
                    )

            frame, quality, complete = assess(frame)
            quality["source"] = label

            if complete:
                if source != "csv" and not label.startswith("cache:"):
                    frame.to_csv(cache_path, index=False)
                return frame, quality, warnings

            warning = (
                f"{source} returned incomplete coverage: "
                f"{quality['coverage_pct_estimate']:.3f}% "
                f"({quality['actual_candles']}/{expected} rows), "
                f"{quality['missing_candles_estimate']} missing timestamps and "
                f"{quality['unexpected_candles']} unexpected timestamps. "
                "Missing candles were not fabricated."
            )
            warnings.append(warning)

            if best_partial is None or (
                quality["coverage_pct_estimate"] >
                best_partial[1]["coverage_pct_estimate"]
            ):
                best_partial = (frame.copy(), quality.copy(), source)

        except Exception as exc:
            errors.append(f"{source}: {exc}")
            warnings.append(f"Data source {source} failed: {exc}")

    if best_partial is not None and config.gap_policy == "report":
        frame, quality, source = best_partial
        quality["source"] = source
        return frame, quality, warnings

    details = " | ".join(errors) if errors else "No source returned complete coverage."
    raise RuntimeError(
        "Could not load complete historical data. "
        "Missing candles were not fabricated. " + details
    )


# ---------------------------- Indicator layer ------------------------------
def add_indicators(frame: pd.DataFrame, config: BacktestConfig) -> pd.DataFrame:
    df = normalize_ohlcv(frame)
    close = df.close
    df["ema_fast"] = close.ewm(span=config.fast_ema, adjust=False).mean()
    df["ema_slow"] = close.ewm(span=config.slow_ema, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / config.rsi_period, adjust=False, min_periods=config.rsi_period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / config.rsi_period, adjust=False, min_periods=config.rsi_period).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    df["rsi"] = df["rsi"].fillna(50)
    prev_close = close.shift(1)
    tr = pd.concat([(df.high - df.low), (df.high - prev_close).abs(), (df.low - prev_close).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / config.atr_period, adjust=False, min_periods=config.atr_period).mean()
    df["breakout_high"] = df.high.shift(1).rolling(config.breakout_period, min_periods=config.breakout_period).max()
    df["breakout_low"] = df.low.shift(1).rolling(config.breakout_period, min_periods=config.breakout_period).min()
    return df


def generate_signals(frame: pd.DataFrame, config: BacktestConfig, strategy: Optional[str] = None) -> pd.DataFrame:
    """Create close-of-candle signals; execution is deliberately deferred to next open."""
    df = add_indicators(frame, config)
    name = (strategy or config.strategy).lower().strip()
    df["signal"] = 0
    if name in {"ema_trend", "trend", "ict", "quant"}:
        # EMA trend baseline. 'ict'/'quant' aliases are compatibility fallbacks, not
        # claims to reproduce the project's full proprietary strategy logic.
        long = (df.ema_fast > df.ema_slow) & (df.ema_fast.shift(1) <= df.ema_slow.shift(1))
        short = (df.ema_fast < df.ema_slow) & (df.ema_fast.shift(1) >= df.ema_slow.shift(1))
        df.loc[long, "signal"] = 1
        df.loc[short, "signal"] = -1
        if name in {"ict", "quant"}:
            # Never silently claim this is the native strategy: baseline adapter only.
            df.attrs["strategy_note"] = f"'{name}' uses EMA crossover fallback; connect the native strategy signal function for faithful results."
    elif name in {"rsi_mean_reversion", "mean_reversion"}:
        df.loc[df.rsi < config.rsi_buy_below, "signal"] = 1
        df.loc[df.rsi > config.rsi_sell_above, "signal"] = -1
    elif name in {"breakout", "momentum"}:
        df.loc[df.close > df.breakout_high, "signal"] = 1
        df.loc[df.close < df.breakout_low, "signal"] = -1
    elif name in {"multi_strategy", "multi"}:
        # Simple consensus: EMA direction plus breakout confirmation.
        long = (df.ema_fast > df.ema_slow) & (df.close > df.breakout_high)
        short = (df.ema_fast < df.ema_slow) & (df.close < df.breakout_low)
        df.loc[long, "signal"] = 1
        df.loc[short, "signal"] = -1
    else:
        raise ValueError(f"Unknown built-in strategy '{name}'")
    if not config.allow_short:
        df.loc[df.signal < 0, "signal"] = 0
    return df


# ------------------------------ Backtest core -------------------------------
def _adverse_fill(price: float, side: str, is_entry: bool, slippage: float) -> float:
    # For a long, entry buys higher and exit sells lower; for a short, inverse.
    if side == "long":
        return price * (1 + slippage if is_entry else 1 - slippage)
    return price * (1 - slippage if is_entry else 1 + slippage)


def _safe_float(value, default=0.0) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _metrics(trades: list[Trade], equity: pd.DataFrame, starting_capital: float, quality: dict) -> dict:
    end_equity = float(equity.equity.iloc[-1]) if not equity.empty else starting_capital
    net_profit = end_equity - starting_capital
    returns = equity.equity.pct_change().replace([np.inf, -np.inf], np.nan).dropna() if not equity.empty else pd.Series(dtype=float)
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl < 0]
    gross_wins = sum(t.net_pnl for t in wins)
    gross_losses = abs(sum(t.net_pnl for t in losses))
    peak = equity.equity.cummax() if not equity.empty else pd.Series(dtype=float)
    dd = (equity.equity / peak - 1.0) if not equity.empty else pd.Series(dtype=float)
    max_dd = abs(float(dd.min())) if not dd.empty else 0.0
    bars_per_year = 365 * 24 * 60 / 5  # annualization is illustrative; actual timeframe set below
    if not equity.empty and len(equity) > 2:
        # Estimate annualization from observed timestamp spacing, instead of assuming 5m.
        ts = pd.to_datetime(equity.timestamp, utc=True)
        median_seconds = ts.diff().dt.total_seconds().dropna().median()
        if median_seconds and median_seconds > 0:
            bars_per_year = 365.25 * 24 * 3600 / median_seconds
    sharpe = 0.0
    if len(returns) > 1 and returns.std(ddof=1) > 0:
        sharpe = float(returns.mean() / returns.std(ddof=1) * math.sqrt(max(1.0, bars_per_year)))
    durations = [t.bars_held for t in trades]
    return {
        "starting_capital": round(starting_capital, 2),
        "ending_equity": round(end_equity, 2),
        "net_profit": round(net_profit, 2),
        "total_return_pct": round(net_profit / starting_capital * 100, 4),
        "max_drawdown_pct": round(max_dd * 100, 4),
        "sharpe_ratio_approx": round(sharpe, 4),
        "total_trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "breakeven_trades": len(trades) - len(wins) - len(losses),
        "win_rate_pct": round(100 * len(wins) / len(trades), 2) if trades else 0.0,
        "profit_factor": round(gross_wins / gross_losses, 4) if gross_losses else (float("inf") if gross_wins else 0.0),
        "average_net_pnl": round(float(np.mean([t.net_pnl for t in trades])), 2) if trades else 0.0,
        "average_r_multiple": round(float(np.mean([t.r_multiple for t in trades])), 4) if trades else 0.0,
        "best_trade_net_pnl": round(max((t.net_pnl for t in trades), default=0.0), 2),
        "worst_trade_net_pnl": round(min((t.net_pnl for t in trades), default=0.0), 2),
        "average_bars_held": round(float(np.mean(durations)), 2) if durations else 0.0,
        "data_coverage_pct_estimate": quality.get("coverage_pct_estimate", 0.0),
        "data_source": quality.get("source", "unknown"),
    }


def run_backtest(frame: pd.DataFrame, config: BacktestConfig,
                 strategy: Optional[str] = None,
                 signal_fn: Optional[Callable[[pd.DataFrame], pd.Series]] = None) -> BacktestResult:
    """Run a single-position, risk-sized historical simulation.

    `signal_fn`, if supplied, must return -1/0/+1 values aligned to input rows and
    may only use current/past data. The engine executes at the following candle open.
    Stops/targets are checked against OHLC. If both are touched in one candle, the
    conservative assumption is that the stop is hit first.
    """
    config.validate()
    raw = normalize_ohlcv(frame)
    if len(raw) < max(config.warmup_bars, config.slow_ema + 5):
        raise ValueError(f"Not enough candles ({len(raw)}) for warm-up; need at least {max(config.warmup_bars, config.slow_ema + 5)}")
    df = generate_signals(raw, config, strategy)
    strategy_name = (strategy or config.strategy).lower()
    if signal_fn is not None:
        custom = signal_fn(df.copy())
        if not isinstance(custom, pd.Series) or len(custom) != len(df):
            raise ValueError("signal_fn must return a pandas Series with one value per candle")
        df["signal"] = pd.to_numeric(custom, errors="coerce").fillna(0).clip(-1, 1).round().astype(int).values
        if not config.allow_short:
            df.loc[df.signal < 0, "signal"] = 0
    note = df.attrs.get("strategy_note")
    warnings: list[str] = [note] if note else []
    equity = float(config.starting_capital)
    peak_equity = equity
    position = None
    trades: list[Trade] = []
    curve: list[dict] = []
    # Record mark-to-cash equity at each candle close. Open positions are marked to market.
    for i in range(len(df)):
        row = df.iloc[i]
        ts = pd.Timestamp(row.timestamp)
        exit_now = None
        if position is not None:
            side = position["side"]
            stop = position["stop_price"]
            target = position["target_price"]
            # Conservative OHLC ordering: stop takes precedence when both are touched.
            if side == "long" and row.low <= stop:
                exit_now = (stop, "stop_loss")
            elif side == "short" and row.high >= stop:
                exit_now = (stop, "stop_loss")
            elif side == "long" and row.high >= target:
                exit_now = (target, "take_profit")
            elif side == "short" and row.low <= target:
                exit_now = (target, "take_profit")
            elif position["max_bars"] and i - position["entry_index"] >= position["max_bars"]:
                exit_now = (float(row.close), "max_holding_period")
            # Optional opposite signal closes at candle close, not same-candle lookahead.
            elif int(row.signal) == (-1 if side == "long" else 1):
                exit_now = (float(row.close), "opposite_signal")
            if exit_now:
                raw_exit, reason = exit_now
                exit_fill = _adverse_fill(float(raw_exit), side, False, config.slippage_rate)
                qty = position["quantity"]
                direction = 1 if side == "long" else -1
                gross = (exit_fill - position["entry_price"]) * qty * direction
                exit_fee = abs(exit_fill * qty) * config.commission_rate
                total_fees = position["entry_fee"] + exit_fee
                net = gross - total_fees
                equity += gross - exit_fee
                initial_risk_cash = position["risk_cash"]
                r_multiple = net / initial_risk_cash if initial_risk_cash > 0 else 0.0
                trades.append(Trade(
                    strategy=strategy_name, symbol=config.symbol, timeframe=config.timeframe,
                    side=side, entry_time=position["entry_time"], exit_time=ts.isoformat(),
                    entry_price=round(position["entry_price"], 10), exit_price=round(exit_fill, 10),
                    quantity=round(qty, 10), stop_price=round(stop, 10), target_price=round(target, 10),
                    gross_pnl=round(gross, 4), fees=round(total_fees, 4), net_pnl=round(net, 4),
                    return_pct_on_equity=round(100 * net / max(position["equity_at_entry"], 1e-9), 4),
                    r_multiple=round(r_multiple, 4), exit_reason=reason,
                    bars_held=max(1, i - position["entry_index"]),
                ))
                position = None
        # Entry uses previous candle's signal and this candle's open.
        if position is None and i > 0 and len(trades) < config.max_trades:
            signal = int(df.iloc[i - 1].signal)
            if signal != 0 and i >= config.warmup_bars:
                side = "long" if signal > 0 else "short"
                if side == "short" and not config.allow_short:
                    pass
                else:
                    entry_raw = float(row.open)
                    entry = _adverse_fill(entry_raw, side, True, config.slippage_rate)
                    atr = _safe_float(df.iloc[i - 1].atr, entry * 0.01)
                    if atr <= 0:
                        atr = entry * 0.01
                    stop_distance = max(atr * config.stop_atr_multiple, entry * 0.001)
                    rr = config.target_rr or config.preferred_rr
                    rr = max(config.min_rr, rr)
                    stop = entry - stop_distance if side == "long" else entry + stop_distance
                    target = entry + stop_distance * rr if side == "long" else entry - stop_distance * rr
                    risk_cash = max(0.0, equity * config.risk_per_trade)
                    qty_by_risk = risk_cash / max(stop_distance, 1e-12)
                    max_notional = max(0.0, equity * config.max_position_fraction * config.leverage)
                    qty_by_notional = max_notional / max(entry, 1e-12)
                    qty = min(qty_by_risk, qty_by_notional)
                    if qty > 0 and equity > 0:
                        entry_fee = abs(entry * qty) * config.commission_rate
                        # Entry fee is charged immediately; PnL on exit subtracts both fees.
                        equity -= entry_fee
                        position = {
                            "side": side, "entry_price": entry, "entry_time": ts.isoformat(),
                            "entry_index": i, "quantity": qty, "stop_price": stop,
                            "target_price": target, "risk_cash": risk_cash,
                            "entry_fee": entry_fee, "equity_at_entry": equity + entry_fee,
                            "max_bars": 0,
                        }
        mark_equity = equity
        if position is not None:
            direction = 1 if position["side"] == "long" else -1
            mark_equity += (float(row.close) - position["entry_price"]) * position["quantity"] * direction
        peak_equity = max(peak_equity, mark_equity)
        curve.append({"timestamp": ts.isoformat(), "equity": round(mark_equity, 4), "cash_equity": round(equity, 4), "drawdown_pct": round((mark_equity / peak_equity - 1) * 100, 4)})
    # Liquidate open position at the final candle close, so reported results include it.
    if position is not None:
        row = df.iloc[-1]
        side = position["side"]
        exit_fill = _adverse_fill(float(row.close), side, False, config.slippage_rate)
        qty = position["quantity"]
        direction = 1 if side == "long" else -1
        gross = (exit_fill - position["entry_price"]) * qty * direction
        exit_fee = abs(exit_fill * qty) * config.commission_rate
        total_fees = position["entry_fee"] + exit_fee
        net = gross - total_fees
        equity += gross - exit_fee
        trades.append(Trade(
            strategy=strategy_name, symbol=config.symbol, timeframe=config.timeframe,
            side=side, entry_time=position["entry_time"], exit_time=pd.Timestamp(row.timestamp).isoformat(),
            entry_price=round(position["entry_price"], 10), exit_price=round(exit_fill, 10),
            quantity=round(qty, 10), stop_price=round(position["stop_price"], 10), target_price=round(position["target_price"], 10),
            gross_pnl=round(gross, 4), fees=round(total_fees, 4), net_pnl=round(net, 4),
            return_pct_on_equity=round(100 * net / max(position["equity_at_entry"], 1e-9), 4),
            r_multiple=round(net / position["risk_cash"], 4) if position["risk_cash"] > 0 else 0.0,
            exit_reason="end_of_test", bars_held=max(1, len(df) - 1 - position["entry_index"]),
        ))
        if curve:
            curve[-1]["equity"] = round(equity, 4)
            curve[-1]["cash_equity"] = round(equity, 4)
    curve_df = pd.DataFrame(curve)
    trade_dicts = [asdict(t) for t in trades]
    metrics = _metrics(trades, curve_df, config.starting_capital, {"coverage_pct_estimate": 100.0, "source": "provided_frame"})
    metrics["strategy"] = strategy_name
    metrics["symbol"] = config.symbol
    metrics["timeframe"] = config.timeframe
    metrics["period_start_utc"] = pd.Timestamp(df.timestamp.iloc[0]).isoformat()
    metrics["period_end_utc"] = pd.Timestamp(df.timestamp.iloc[-1]).isoformat()
    metrics["fees_assumption_per_side_pct"] = config.commission_rate * 100
    metrics["slippage_assumption_per_side_pct"] = config.slippage_rate * 100
    return BacktestResult(config=asdict(config), metrics=metrics, trades=trade_dicts,
                          equity_curve=curve, data_quality={"source": "provided_frame", "actual_candles": len(df), "actual_start_utc": metrics["period_start_utc"], "actual_end_utc": metrics["period_end_utc"]}, warnings=warnings)


def run_one_year(config: BacktestConfig, strategies: Optional[Iterable[str]] = None) -> dict[str, BacktestResult]:
    """Load data once and run each requested strategy against the same candles."""
    candles, quality, data_warnings = load_historical_data(config)
    names = list(strategies or [config.strategy])
    results: dict[str, BacktestResult] = {}
    for name in names:
        result = run_backtest(candles, config, strategy=name)
        result.data_quality = quality
        result.warnings = data_warnings + result.warnings
        results[name] = result
    return results


def save_results(results: dict[str, BacktestResult], output_dir: str | Path = "backtest_results") -> dict[str, str]:
    """Save metrics, trades, equity curve, and full JSON per strategy."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    saved: dict[str, str] = {}
    summary = []
    for name, result in results.items():
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        trade_path = root / f"{safe_name}_trades.csv"
        equity_path = root / f"{safe_name}_equity.csv"
        json_path = root / f"{safe_name}_result.json"
        result.trades_frame().to_csv(trade_path, index=False)
        result.equity_frame().to_csv(equity_path, index=False)
        result.to_json(json_path)
        summary.append(result.metrics)
        saved[f"{name}_trades"] = str(trade_path)
        saved[f"{name}_equity"] = str(equity_path)
        saved[f"{name}_json"] = str(json_path)
    summary_path = root / "strategy_comparison.csv"
    pd.DataFrame(summary).to_csv(summary_path, index=False)
    saved["comparison"] = str(summary_path)
    return saved


def main() -> int:
    """CLI entry point: python -m engine.year_backtest"""
    import argparse
    parser = argparse.ArgumentParser(description="ARJUNA one-year historical backtest")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe", default="5m", choices=list(TIMEFRAME_MS))
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--capital", type=float, default=100_000)
    parser.add_argument("--strategy", default="all", choices=["all", "ema_trend", "rsi_mean_reversion", "breakout", "multi_strategy"])
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--csv", default=None)
    parser.add_argument("--output", default="backtest_results")
    parser.add_argument("--commission", type=float, default=0.0006)
    parser.add_argument("--slippage", type=float, default=0.0005)
    parser.add_argument("--risk", type=float, default=0.01)
    args = parser.parse_args()
    cfg = BacktestConfig(symbol=args.symbol, timeframe=args.timeframe, days=args.days,
                         starting_capital=args.capital, strategy="ema_trend",
                         exchange_id=args.exchange, csv_path=args.csv,
                         data_source="csv" if args.csv else "auto",
                         commission_rate=args.commission, slippage_rate=args.slippage,
                         risk_per_trade=args.risk)
    names = ["ema_trend", "rsi_mean_reversion", "breakout", "multi_strategy"] if args.strategy == "all" else [args.strategy]
    results = run_one_year(cfg, names)
    saved = save_results(results, args.output)
    print("\nARJUNA HISTORICAL BACKTEST COMPLETE")
    print(pd.DataFrame([r.metrics for r in results.values()]).to_string(index=False))
    print("\nSaved files:")
    for label, path in saved.items():
        print(f"- {label}: {path}")
    print("\nNote: Built-in strategies are transparent baseline examples. Connect native Arjuna strategy signals before interpreting them as ICT/Quant production results.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
