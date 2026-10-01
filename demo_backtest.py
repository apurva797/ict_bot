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
                trades.append(_trade(position, timestamp, exit_price, reason, gross, fees, pnl,
                                     position.get("entry_slippage_cost", 0.0) + abs(exit_price - raw_exit) * position["quantity"]))
                position = None
                last_closed = timestamp
            elif previous_exit:
                exit_price = float(candle.open) * (1 - slippage if active_side == "LONG" else 1 + slippage)
                gross = (exit_price - position["entry"]) * position["quantity"] * (1 if active_side == "LONG" else -1)
                fees = position["entry"] * position["quantity"] * fee_rate + exit_price * position["quantity"] * fee_rate
                pnl = gross - fees
                balance += pnl
                trades.append(_trade(position, timestamp, exit_price, "Strategy exit", gross, fees, pnl,
                                     position.get("entry_slippage_cost", 0.0) + abs(exit_price - float(candle.open)) * position["quantity"]))
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
                        position["entry_slippage_cost"] = abs(entry - float(candle.open)) * quantity
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
                            trades.append(_trade(position, timestamp, exit_price, reason, gross, fees, pnl,
                                                 position["entry_slippage_cost"] + abs(exit_price - raw_exit) * quantity))
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
        trades.append(_trade(position, timestamp, exit_price, "End of data", gross, fees, pnl,
                             position.get("entry_slippage_cost", 0.0) + abs(exit_price - float(frame.close.iloc[-1])) * position["quantity"]))
        if equity:
            equity[-1]["equity"] = balance

    wins = sum(t["net_pnl"] > 0 for t in trades)
    losses = sum(t["net_pnl"] < 0 for t in trades)
    gains = sum(t["net_pnl"] for t in trades if t["net_pnl"] > 0)
    loss_total = -sum(t["net_pnl"] for t in trades if t["net_pnl"] < 0)
    gross_profit = sum(max(0.0, t["gross_pnl"]) for t in trades)
    gross_loss = -sum(min(0.0, t["gross_pnl"]) for t in trades)
    wins_list = [t["net_pnl"] for t in trades if t["net_pnl"] > 0]
    losses_list = [t["net_pnl"] for t in trades if t["net_pnl"] < 0]
    streak = max_wins = max_losses = 0
    previous_outcome = None
    for trade in trades:
        outcome = "win" if trade["net_pnl"] > 0 else "loss" if trade["net_pnl"] < 0 else "flat"
        streak = streak + 1 if outcome == previous_outcome else 1
        if outcome == "win":
            max_wins = max(max_wins, streak)
        elif outcome == "loss":
            max_losses = max(max_losses, streak)
        previous_outcome = outcome
    duration_minutes = []
    for trade in trades:
        try:
            duration_minutes.append((trade["closed_at"] - trade["opened_at"]).total_seconds() / 60)
        except (AttributeError, TypeError):
            pass
    bar_returns = pd.Series([point["equity"] for point in equity], dtype="float64").pct_change().dropna()
    sharpe = sortino = None
    if len(bar_returns) >= 2 and float(bar_returns.std(ddof=1)) > 0:
        bar_intervals = index.to_series().diff().dropna().dt.total_seconds()
        seconds_per_bar = float(bar_intervals.median()) if not bar_intervals.empty else 0.0
        if seconds_per_bar > 0:
            annualization = math.sqrt((365.25 * 24 * 60 * 60) / seconds_per_bar)
            sharpe = float(bar_returns.mean() / bar_returns.std(ddof=1) * annualization)
            downside = bar_returns[bar_returns < 0]
            if len(downside) and float(downside.std(ddof=0)) > 0:
                sortino = float(bar_returns.mean() / downside.std(ddof=0) * annualization)
    pnl = balance - starting_capital
    metrics = {
        "Starting capital": starting_capital, "Ending capital": balance,
        "Total return %": pnl / starting_capital * 100, "Total P&L": pnl,
        "Maximum drawdown %": max_dd * 100,
        "Win rate %": wins / len(trades) * 100 if trades else 0.0,
        "Number of trades": len(trades), "Wins": wins, "Losses": losses,
        "Average trade": pnl / len(trades) if trades else 0.0,
        "Profit factor": gains / loss_total if loss_total else (float("inf") if gains else 0.0),
        "Gross profit": gross_profit, "Gross loss": gross_loss,
        "Expectancy": sum(t["net_pnl"] for t in trades) / len(trades) if trades else None,
        "Average win": sum(wins_list) / len(wins_list) if wins_list else None,
        "Average loss": sum(losses_list) / len(losses_list) if losses_list else None,
        "Average R": sum(t["net_pnl"] / t["risk_amount"] for t in trades if t["risk_amount"] > 0) / len(trades) if trades else None,
        "Maximum consecutive wins": max_wins, "Maximum consecutive losses": max_losses,
        "Average trade duration minutes": sum(duration_minutes) / len(duration_minutes) if duration_minutes else None,
        "Fees paid": sum(t["fees"] for t in trades), "Estimated slippage paid": sum(t.get("slippage_cost", 0.0) for t in trades),
        "Sharpe ratio": sharpe, "Sortino ratio": sortino,
    }
    trade_columns = ["side", "opened_at", "closed_at", "entry", "exit", "stop", "target", "quantity", "risk_amount", "reason", "gross_pnl", "fees", "slippage_cost", "net_pnl"]
    return metrics, pd.DataFrame(equity).set_index("time"), pd.DataFrame(trades, columns=trade_columns)


def run_ict_backtest(frame, starting_capital=10_000.0, news_blackout=False):
    """Backtest the preserved ICT signal at each finalized candle close."""
    from demo_safety import ict_entry_gate
    from strategies.ict import ict_signal

    if frame is None or len(frame) < 100:
        raise ValueError("Insufficient market data for the ARJUNA strategy.")
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


def _trade(position, closed_at, exit_price, reason, gross, fees, pnl, slippage_cost=0.0):
    return {"side": position["side"], "opened_at": position["opened_at"], "closed_at": closed_at,
            "entry": position["entry"], "exit": exit_price, "stop": position["stop"], "target": position["target"],
            "quantity": position["quantity"], "risk_amount": position["risk_amount"], "reason": reason,
            "gross_pnl": gross, "fees": fees, "slippage_cost": slippage_cost, "net_pnl": pnl}

