"""Next-bar-open, long-only-or-short-only strategy simulation."""

import math

import pandas as pd

from demo_safety import assert_demo_mode, validate_risk_controls
from demo_strategy import evaluate_conditions


def run_backtest(frame, strategy, starting_capital=10_000.0, fee_rate=0.0004, slippage=0.0001, signal_sides=None):
    assert_demo_mode()
    validate_risk_controls(strategy.get("risk_fraction", 0.01), strategy.get("rr", 2), strategy.get("leverage", 1))
    if frame is None or len(frame) < 35 or starting_capital <= 0:
        raise ValueError("Insufficient market data for this strategy.")
    entries = evaluate_conditions(frame, strategy["entry"]).fillna(False) if signal_sides is None else signal_sides.isin(["BUY", "SELL"])
    exits = evaluate_conditions(frame, strategy["exit"]).fillna(False) if signal_sides is None else pd.Series(False, index=frame.index)
    balance = float(starting_capital)
    peak = balance
    max_dd = 0.0
    equity = []
    trades = []
    position = None
    last_closed = None
    risk_fraction = float(strategy.get("risk_fraction", 0.01))
    rr = float(strategy.get("rr", 2.0))
    side = "LONG" if strategy.get("side", "BUY") == "BUY" else "SHORT"
    index = frame.index

    for i in range(1, len(frame)):
        candle = frame.iloc[i]
        timestamp = index[i]
        previous_entry = bool(entries.iloc[i - 1])
        if signal_sides is not None and position is not None:
            wanted_side = "BUY" if position["side"] == "LONG" else "SELL"
            previous_exit = signal_sides.iloc[i - 1] in {"BUY", "SELL"} and signal_sides.iloc[i - 1] != wanted_side
        else:
            previous_exit = bool(evaluate_conditions(frame.iloc[:i], strategy["exit"], entry_price=position["entry"]).iloc[-1]) if position is not None and signal_sides is None else bool(exits.iloc[i - 1])

        if position is not None:
            active_side = position["side"]
            stop_hit = candle.low <= position["stop"] if active_side == "LONG" else candle.high >= position["stop"]
            target_hit = candle.high >= position["target"] if active_side == "LONG" else candle.low <= position["target"]
            if stop_hit or target_hit:
                # Conservative fill when both boundaries occur within one candle.
                raw_exit = position["stop"] if stop_hit else position["target"]
                reason = "Stop loss" if stop_hit else "Take profit"
                exit_price = raw_exit * (1 - slippage if active_side == "LONG" else 1 + slippage)
                gross = (exit_price - position["entry"]) * position["quantity"] * (1 if active_side == "LONG" else -1)
                fees = position["entry"] * position["quantity"] * fee_rate + exit_price * position["quantity"] * fee_rate
                pnl = gross - fees
                balance += pnl
                trades.append(_trade(position, timestamp, exit_price, reason, gross, fees, pnl))
                position = None
                last_closed = timestamp
            elif previous_exit:
                exit_price = float(candle.open) * (1 - slippage if active_side == "LONG" else 1 + slippage)
                gross = (exit_price - position["entry"]) * position["quantity"] * (1 if active_side == "LONG" else -1)
                fees = position["entry"] * position["quantity"] * fee_rate + exit_price * position["quantity"] * fee_rate
                pnl = gross - fees
                balance += pnl
                trades.append(_trade(position, timestamp, exit_price, "Strategy exit", gross, fees, pnl))
                position = None
                last_closed = timestamp

        if position is None and previous_entry:
            if last_closed is not None and (timestamp - last_closed).total_seconds() < 30 * 60:
                pass
            else:
                trade_side = side
                if signal_sides is not None:
                    trade_side = "LONG" if signal_sides.iloc[i - 1] == "BUY" else "SHORT"
                entry = float(candle.open) * (1 + slippage if trade_side == "LONG" else 1 - slippage)
                risk_distance = entry * 0.01
                stop = entry - risk_distance if trade_side == "LONG" else entry + risk_distance
                target = entry + risk_distance * rr if trade_side == "LONG" else entry - risk_distance * rr
                if risk_distance > 0 and math.isfinite(entry) and balance > 0:
                    risk_amount = balance * risk_fraction
                    quantity = min(risk_amount / risk_distance, balance / entry)  # max 1x notional
                    if quantity > 0 and quantity * entry <= balance * 1.000001:
                        position = {"entry": entry, "stop": stop, "target": target, "quantity": quantity,
                                    "risk_amount": risk_amount, "opened_at": timestamp, "side": trade_side}
                        # Handle stop/target against the entry candle, with stop priority.
                        stop_hit = candle.low <= stop if trade_side == "LONG" else candle.high >= stop
                        target_hit = candle.high >= target if trade_side == "LONG" else candle.low <= target
                        if stop_hit or target_hit:
                            raw_exit = stop if stop_hit else target
                            reason = "Stop loss" if stop_hit else "Take profit"
                            exit_price = raw_exit * (1 - slippage if trade_side == "LONG" else 1 + slippage)
                            gross = (exit_price - entry) * quantity * (1 if trade_side == "LONG" else -1)
                            fees = entry * quantity * fee_rate + exit_price * quantity * fee_rate
                            pnl = gross - fees
                            balance += pnl
                            trades.append(_trade(position, timestamp, exit_price, reason, gross, fees, pnl))
                            position = None
                            last_closed = timestamp

        marked = balance
        if position is not None:
            marked += (float(candle.close) - position["entry"]) * position["quantity"] * (1 if position["side"] == "LONG" else -1)
        peak = max(peak, marked)
        max_dd = max(max_dd, (peak - marked) / peak if peak else 0)
        equity.append({"time": timestamp, "equity": marked})

    if position is not None:
        timestamp = index[-1]
        active_side = position["side"]
        exit_price = float(frame.close.iloc[-1]) * (1 - slippage if active_side == "LONG" else 1 + slippage)
        gross = (exit_price - position["entry"]) * position["quantity"] * (1 if active_side == "LONG" else -1)
        fees = position["entry"] * position["quantity"] * fee_rate + exit_price * position["quantity"] * fee_rate
        pnl = gross - fees
        balance += pnl
        trades.append(_trade(position, timestamp, exit_price, "End of data", gross, fees, pnl))
        if equity:
            equity[-1]["equity"] = balance

    wins = sum(t["net_pnl"] > 0 for t in trades)
    losses = sum(t["net_pnl"] < 0 for t in trades)
    gains = sum(t["net_pnl"] for t in trades if t["net_pnl"] > 0)
    loss_total = -sum(t["net_pnl"] for t in trades if t["net_pnl"] < 0)
    pnl = balance - starting_capital
    metrics = {
        "Starting capital": starting_capital, "Ending capital": balance,
        "Total return %": pnl / starting_capital * 100, "Total P&L": pnl,
        "Maximum drawdown %": max_dd * 100,
        "Win rate %": wins / len(trades) * 100 if trades else 0.0,
        "Number of trades": len(trades), "Wins": wins, "Losses": losses,
        "Average trade": pnl / len(trades) if trades else 0.0,
        "Profit factor": gains / loss_total if loss_total else (float("inf") if gains else 0.0),
    }
    trade_columns = ["side", "opened_at", "closed_at", "entry", "exit", "stop", "target", "quantity", "risk_amount", "reason", "gross_pnl", "fees", "net_pnl"]
    return metrics, pd.DataFrame(equity).set_index("time"), pd.DataFrame(trades, columns=trade_columns)


def run_ict_backtest(frame, starting_capital=10_000.0, news_blackout=False):
    """Backtest the preserved ICT signal at each finalized candle close."""
    from demo_safety import ict_entry_gate
    from strategies.ict import ict_signal

    if frame is None or len(frame) < 100:
        raise ValueError("Insufficient market data for the ICT strategy.")
    sides = []
    for i, (timestamp, row) in enumerate(frame.iterrows()):
        if i < 99 or not ict_entry_gate(timestamp, news_blackout=news_blackout)[0]:
            sides.append(None)
            continue
        candles = [[int(ts.timestamp() * 1000), float(c.open), float(c.high), float(c.low), float(c.close), float(c.volume)]
                   for ts, c in frame.iloc[:i + 1].iterrows()]
        result = ict_signal(candles)
        side = result.get("side") if isinstance(result, dict) else None
        sides.append("BUY" if side == "LONG" else "SELL" if side == "SHORT" else None)
    signals = pd.Series(sides, index=frame.index)
    strategy = {"side": "BUY", "entry": [{"indicator": "price", "operator": ">", "value": 0}],
                "exit": [{"indicator": "price", "operator": ">", "value": 0}]}
    return run_backtest(frame, strategy, starting_capital=starting_capital, signal_sides=signals)


def _trade(position, closed_at, exit_price, reason, gross, fees, pnl):
    return {"side": position["side"], "opened_at": position["opened_at"], "closed_at": closed_at,
            "entry": position["entry"], "exit": exit_price, "stop": position["stop"], "target": position["target"],
            "quantity": position["quantity"], "risk_amount": position["risk_amount"], "reason": reason,
            "gross_pnl": gross, "fees": fees, "net_pnl": pnl}
