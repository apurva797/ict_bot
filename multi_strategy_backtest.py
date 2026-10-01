"""Historical, next-bar-open simulation over the existing multi-strategy engine."""

from __future__ import annotations

import math
from collections import defaultdict

import pandas as pd

from demo_data import MarketDataError, _validate_ohlcv_frame
from demo_safety import assert_demo_mode, validate_risk_controls
from engine.risk import calculate_atr_levels, calculate_position_size, calculate_atr, validate_trade
from config import ATR_PERIOD, ATR_SL_MULTIPLIER, MAX_DAILY_LOSS_R, MAX_TRADES_PER_DAY, MIN_RR, DEFAULT_RR

WARMUP_CANDLES = 100
DEFAULT_FEE_RATE = 0.0004
DEFAULT_SLIPPAGE_RATE = 0.0001
DEFAULT_COOLDOWN_MINUTES = 30


def run_multi_strategy_backtest(
    frame,
    symbol="BTC/USDT",
    timeframe="1h",
    starting_capital=10_000.0,
    risk_fraction=0.01,
    min_rr=MIN_RR,
    target_rr=DEFAULT_RR,
    max_concurrent_trades=1,
    fee_rate=DEFAULT_FEE_RATE,
    slippage_rate=DEFAULT_SLIPPAGE_RATE,
    strategy_selection=None,
    cooldown_minutes=DEFAULT_COOLDOWN_MINUTES,
    analyzer=None,
):
    """Backtest confirmed aggregate signals without accessing future candles.

    The analyzer sees only the prefix through candle T's close; a resulting
    signal fills at T+1 open. Opposing confirmed signals close positions at
    T+1 open. If both SL and TP are touched in one candle, SL wins.
    """
    assert_demo_mode()
    validate_risk_controls(risk_fraction, target_rr, 1.0)
    if min_rr < MIN_RR:
        raise ValueError(f"Minimum R:R cannot be below {MIN_RR:.1f}R.")
    if target_rr < min_rr:
        raise ValueError("Target R:R must be at least the selected minimum R:R.")
    if starting_capital <= 0 or not math.isfinite(float(starting_capital)):
        raise ValueError("Initial capital must be a positive finite amount.")
    if not isinstance(max_concurrent_trades, int) or max_concurrent_trades < 1:
        raise ValueError("Maximum concurrent trades must be at least one.")
    if not all(math.isfinite(float(value)) and float(value) >= 0 for value in (fee_rate, slippage_rate)):
        raise ValueError("Fee and slippage must be finite, non-negative rates.")

    frame = _validate_ohlcv_frame(frame)
    if len(frame) < WARMUP_CANDLES + 2:
        raise MarketDataError(f"Insufficient historical data: need at least {WARMUP_CANDLES + 2} candles.")
    names = _strategy_names()
    selected = tuple(names if strategy_selection is None else strategy_selection)
    unknown = set(selected) - set(names)
    if not selected or unknown:
        raise ValueError("Choose at least one known strategy." if not selected else
                         f"Unknown strategies: {', '.join(sorted(unknown))}")
    if analyzer is None:
        from bot import analyze_multi_strategy_candles
        analyzer = analyze_multi_strategy_candles

    balance = float(starting_capital)
    open_positions = []
    trades, blocked = [], []
    equity = []
    strategy_stats = {name: {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0, "r_values": []}
                      for name in names}
    last_entry_at = None
    daily_entries = defaultdict(int)
    daily_r = defaultdict(float)
    peak = balance
    max_drawdown = 0.0

    # First evaluation is only after a 100-candle warmup. The signal uses the
    # prefix ending at T; the next row is the first possible fill candle.
    for fill_index in range(WARMUP_CANDLES, len(frame)):
        signal_index = fill_index - 1
        signal_frame = frame.iloc[:signal_index + 1]
        signal_candles = _frame_to_candles(signal_frame)
        evaluation = analyzer(signal_candles, strategy_selection=selected, historical=True)
        final = evaluation["final_signal"]
        current_open = float(frame.open.iloc[fill_index])
        bar = frame.iloc[fill_index]
        timestamp = frame.index[fill_index]
        signal_side = final.get("side")
        confirmed = bool(final.get("confirmation_passed", False))
        active_key = "active_long" if signal_side == "LONG" else "active_short"
        contributors = list(final.get(active_key, []))
        if not contributors:
            contributors = [name for name, signal in evaluation.get("signals", {}).items()
                            if name in selected and signal.get("side") == signal_side]
        contributors = [name for name in contributors if name in strategy_stats]
        reason = str(final.get("reason", ""))

        # A newly confirmed opposite aggregate signal exits existing positions
        # at this next candle's open, using no current-candle close information.
        for position in list(open_positions):
            if confirmed and signal_side in {"LONG", "SHORT"} and signal_side != position["side"]:
                _close_position(position, timestamp, current_open, "Opposing confirmed signal",
                                balance_ref=None, fee_rate=fee_rate, slippage_rate=slippage_rate,
                                trades=trades, strategy_stats=strategy_stats)
                balance += trades[-1]["pnl"]
                _record_daily_result(trades[-1], daily_r)
                open_positions.remove(position)

        # Evaluate each open position against this candle; same-bar SL/TP is
        # always conservative (stop first). Gap-through exits use the open.
        for position in list(open_positions):
            exit_info = _bar_exit(position, bar)
            if exit_info:
                raw_exit, exit_reason = exit_info
                _close_position(position, timestamp, raw_exit, exit_reason,
                                balance_ref=None, fee_rate=fee_rate, slippage_rate=slippage_rate,
                                trades=trades, strategy_stats=strategy_stats)
                balance += trades[-1]["pnl"]
                _record_daily_result(trades[-1], daily_r)
                open_positions.remove(position)

        if signal_side in {"LONG", "SHORT"}:
            blocked_reason = None
            if not confirmed:
                blocked_reason = "Confirmation rule not met (requires 4+ points OR 2 heavy conditions)."
            elif len(open_positions) >= max_concurrent_trades:
                blocked_reason = "Maximum concurrent trades reached."
            elif last_entry_at is not None and (timestamp - last_entry_at).total_seconds() < cooldown_minutes * 60:
                blocked_reason = f"{cooldown_minutes}-minute cooldown is active."
            elif daily_entries[str(timestamp.date())] >= MAX_TRADES_PER_DAY:
                blocked_reason = "Maximum daily trades reached."
            elif daily_r[str(timestamp.date())] <= -abs(MAX_DAILY_LOSS_R):
                blocked_reason = "Maximum daily loss reached."
            if not blocked_reason:
                try:
                    position = _open_position(
                        frame.iloc[:signal_index + 1], current_open, signal_side, symbol,
                        contributors, reason, final, timestamp, balance, open_positions,
                        risk_fraction, target_rr, min_rr, fee_rate, slippage_rate,
                    )
                    if position is None:
                        blocked_reason = "Risk, R:R, or available notional check failed."
                    else:
                        open_positions.append(position)
                        last_entry_at = timestamp
                        daily_entries[str(timestamp.date())] += 1
                        # A new position can hit a stop or target in its entry candle.
                        exit_info = _bar_exit(position, bar)
                        if exit_info:
                            raw_exit, exit_reason = exit_info
                            _close_position(position, timestamp, raw_exit, exit_reason,
                                            balance_ref=None, fee_rate=fee_rate, slippage_rate=slippage_rate,
                                            trades=trades, strategy_stats=strategy_stats)
                            balance += trades[-1]["pnl"]
                            _record_daily_result(trades[-1], daily_r)
                            open_positions.remove(position)
                except (ValueError, KeyError, TypeError) as exc:
                    blocked_reason = str(exc)
            if blocked_reason:
                blocked.append({
                    "timestamp": timestamp, "symbol": symbol, "side": signal_side,
                    "confirmation_points": final.get("confirmation_points", 0),
                    "heavy_conditions": ", ".join(final.get("heavy_conditions", [])),
                    "status": "BLOCKED", "reason": blocked_reason,
                })

        marked_equity = balance
        for position in open_positions:
            marked_equity += _unrealized_pnl(position, float(bar.close), fee_rate)
        peak = max(peak, marked_equity)
        drawdown = (peak - marked_equity) / peak if peak > 0 else 0.0
        max_drawdown = max(max_drawdown, drawdown)
        equity.append({"time": timestamp, "equity": marked_equity,
                       "drawdown_pct": drawdown * 100})

    # Close outstanding positions at the final close; no future candle exists.
    final_timestamp = frame.index[-1]
    final_close = float(frame.close.iloc[-1])
    for position in list(open_positions):
        _close_position(position, final_timestamp, final_close, "End of data",
                        balance_ref=None, fee_rate=fee_rate, slippage_rate=slippage_rate,
                        trades=trades, strategy_stats=strategy_stats)
        balance += trades[-1]["pnl"]
        _record_daily_result(trades[-1], daily_r)
        open_positions.remove(position)
    if equity:
        equity[-1]["equity"] = balance
        final_peak = max(point["equity"] for point in equity)
        final_drawdown = (final_peak - balance) / final_peak if final_peak else 0.0
        equity[-1]["drawdown_pct"] = final_drawdown * 100
        max_drawdown = max(max_drawdown, final_drawdown)

    trades_frame = pd.DataFrame(trades, columns=_TRADE_COLUMNS)
    equity_frame = pd.DataFrame(equity).set_index("time") if equity else pd.DataFrame(
        columns=["equity", "drawdown_pct"], index=pd.DatetimeIndex([], name="time"))
    metrics = _metrics(float(starting_capital), balance, trades_frame, blocked, max_drawdown)
    breakdown = _strategy_breakdown(strategy_stats)
    return {
        "metrics": metrics, "trades": trades_frame, "equity": equity_frame,
        "strategy_breakdown": breakdown, "blocked_signals": pd.DataFrame(blocked),
        "source": None, "symbol": symbol, "timeframe": timeframe,
        "start": frame.index[0], "end": frame.index[-1], "selection": selected,
    }


_TRADE_COLUMNS = [
    "timestamp", "symbol", "strategies", "side", "entry", "stop_loss", "target",
    "exit", "rr", "result", "pnl", "r_multiple", "duration_minutes",
    "entry_reason", "exit_reason", "fees", "slippage_cost", "risk_amount",
    "opened_at", "closed_at",
]


def _strategy_names():
    from bot import multi_strategy_names
    return multi_strategy_names()


def _frame_to_candles(frame):
    return [[int(ts.timestamp() * 1000), float(row.open), float(row.high), float(row.low),
             float(row.close), float(row.volume)] for ts, row in frame.iterrows()]


def _open_position(prefix, raw_entry, side, symbol, contributors, reason, final, timestamp,
                   balance, open_positions, risk_fraction, target_rr, min_rr,
                   fee_rate, slippage_rate):
    atr = calculate_atr(_frame_to_candles(prefix), ATR_PERIOD)
    if atr is None or atr <= 0:
        return None
    side = "LONG" if side == "LONG" else "SHORT"
    entry = raw_entry * (1 + slippage_rate if side == "LONG" else 1 - slippage_rate)
    levels = calculate_atr_levels(entry, atr, side, sl_multiplier=ATR_SL_MULTIPLIER, rr=target_rr)
    if not levels:
        return None
    valid, actual_rr, _ = validate_trade(entry, levels["stop"], levels["target"], side, min_rr=min_rr)
    if not valid:
        return None
    risk_amount = balance * risk_fraction
    quantity = calculate_position_size(balance, risk_fraction * 100, entry, levels["stop"], max_leverage=1.0)
    if quantity <= 0 or (sum(p["entry"] * p["quantity"] for p in open_positions) + entry * quantity) > balance + 1e-8:
        return None
    validate_risk_controls(risk_fraction, actual_rr, 1.0)
    entry_fee = entry * quantity * fee_rate
    return {
        "opened_at": timestamp, "timestamp": timestamp, "symbol": symbol, "strategies": contributors,
        "side": side, "entry": entry, "raw_entry": raw_entry,
        "stop": levels["stop"], "target": levels["target"], "rr": actual_rr,
        "quantity": quantity, "risk_amount": min(risk_amount, abs(entry - levels["stop"]) * quantity),
        "entry_fee": entry_fee, "entry_slippage_cost": abs(entry - raw_entry) * quantity,
        "entry_reason": reason or f"{final.get('confirmation_points', 0)} points / {len(final.get('heavy_conditions', []))} heavy conditions",
    }


def _bar_exit(position, bar):
    op, high, low = float(bar.open), float(bar.high), float(bar.low)
    if position["side"] == "LONG":
        stop_hit, target_hit = low <= position["stop"], high >= position["target"]
        if stop_hit:
            return (op if op <= position["stop"] else position["stop"]), "Stop loss"
        if target_hit:
            return (op if op >= position["target"] else position["target"]), "Take profit"
    else:
        stop_hit, target_hit = high >= position["stop"], low <= position["target"]
        if stop_hit:
            return (op if op >= position["stop"] else position["stop"]), "Stop loss"
        if target_hit:
            return (op if op <= position["target"] else position["target"]), "Take profit"
    return None


def _close_position(position, timestamp, raw_exit, reason, balance_ref, fee_rate,
                    slippage_rate, trades, strategy_stats):
    side = position["side"]
    exit_price = raw_exit * (1 - slippage_rate if side == "LONG" else 1 + slippage_rate)
    gross = (exit_price - position["entry"]) * position["quantity"] * (1 if side == "LONG" else -1)
    exit_fee = exit_price * position["quantity"] * fee_rate
    fees = position["entry_fee"] + exit_fee
    pnl = gross - fees
    planned_risk = position["risk_amount"]
    r_multiple = pnl / planned_risk if planned_risk else 0.0
    duration = max(0.0, (timestamp - position["opened_at"]).total_seconds() / 60)
    trade = {
        "timestamp": position["opened_at"], "symbol": position["symbol"],
        "strategies": ", ".join("ARJUNA" if name == "ICT" else name for name in position["strategies"]),
        "side": side, "entry": position["entry"], "stop_loss": position["stop"],
        "target": position["target"], "exit": exit_price, "rr": position["rr"],
        "result": "Win" if pnl > 0 else "Loss" if pnl < 0 else "Flat", "pnl": pnl,
        "r_multiple": r_multiple, "duration_minutes": duration,
        "entry_reason": position["entry_reason"], "exit_reason": reason,
        "fees": fees, "slippage_cost": position["entry_slippage_cost"] + abs(exit_price - raw_exit) * position["quantity"],
        "risk_amount": planned_risk, "opened_at": position["opened_at"], "closed_at": timestamp,
    }
    trades.append(trade)
    for name in position["strategies"]:
        if name in strategy_stats:
            stats = strategy_stats[name]
            stats["trades"] += 1
            stats["wins"] += int(pnl > 0)
            stats["losses"] += int(pnl < 0)
            stats["pnl"] += pnl
            stats["r_values"].append(r_multiple)


def _unrealized_pnl(position, close, fee_rate):
    gross = (close - position["entry"]) * position["quantity"] * (1 if position["side"] == "LONG" else -1)
    estimated_exit_fee = close * position["quantity"] * fee_rate
    return gross - position["entry_fee"] - estimated_exit_fee


def _record_daily_result(trade, daily_r):
    daily_r[str(pd.Timestamp(trade["closed_at"]).date())] += trade["r_multiple"]


def _metrics(starting, ending, trades, blocked, max_drawdown):
    count = len(trades)
    wins = int((trades["pnl"] > 0).sum()) if count else 0
    losses = int((trades["pnl"] < 0).sum()) if count else 0
    positive = float(trades.loc[trades.pnl > 0, "pnl"].sum()) if count else 0.0
    negative = abs(float(trades.loc[trades.pnl < 0, "pnl"].sum())) if count else 0.0
    streak_wins = streak_losses = current_wins = current_losses = 0
    for pnl in trades["pnl"] if count else []:
        if pnl > 0:
            current_wins += 1; current_losses = 0
            streak_wins = max(streak_wins, current_wins)
        elif pnl < 0:
            current_losses += 1; current_wins = 0
            streak_losses = max(streak_losses, current_losses)
        else:
            current_wins = current_losses = 0
    return {
        "Initial capital": starting, "Final capital": ending, "Total P&L": ending - starting,
        "Return %": (ending - starting) / starting * 100, "Total trades": count,
        "Winning trades": wins, "Losing trades": losses,
        "Win rate %": wins / count * 100 if count else 0.0,
        "Profit factor": positive / negative if negative else (math.inf if positive else 0.0),
        "Average R": float(trades["r_multiple"].mean()) if count else 0.0,
        "Max drawdown %": max_drawdown * 100, "Maximum consecutive wins": streak_wins,
        "Maximum consecutive losses": streak_losses,
        "Average trade duration minutes": float(trades["duration_minutes"].mean()) if count else 0.0,
        "Long trades": int((trades["side"] == "LONG").sum()) if count else 0,
        "Short trades": int((trades["side"] == "SHORT").sum()) if count else 0,
        "Blocked signals": len(blocked), "Executed signals": count,
    }


def _strategy_breakdown(stats):
    rows = []
    for name, values in stats.items():
        count = values["trades"]
        rows.append({
            "Strategy": "ARJUNA" if name == "ICT" else name,
            "Trades": count, "Wins": values["wins"], "Losses": values["losses"],
            "Win Rate %": values["wins"] / count * 100 if count else 0.0,
            "P&L": values["pnl"], "Avg R": sum(values["r_values"]) / count if count else 0.0,
        })
    return pd.DataFrame(rows)
