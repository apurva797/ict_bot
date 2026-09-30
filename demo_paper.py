"""In-memory simulated account; contains no exchange order integration."""

from demo_safety import assert_demo_mode, validate_risk_controls
from demo_strategy import evaluate_conditions


def advance_paper_account(frame, strategy, state, signal_sides=None):
    assert_demo_mode()
    validate_risk_controls(strategy.get("risk_fraction", 0.01), strategy.get("rr", 2), strategy.get("leverage", 1))
    if frame is None or len(frame) < 35:
        raise ValueError("Insufficient market data for paper trading.")
    state.setdefault("balance", 10_000.0)
    state.setdefault("position", None)
    state.setdefault("trades", [])
    state.setdefault("last_action", None)
    timestamp = frame.index[-1]
    # Avoid duplicate fills if Streamlit reruns on the same finalized candle.
    if state["last_action"] == str(timestamp):
        return "No new candle yet; paper account unchanged."
    close = float(frame.close.iloc[-1])
    msg = "No paper trade signal on the latest closed candle."
    position = state["position"]
    side = position["side"] if position else ("LONG" if strategy.get("side", "BUY") == "BUY" else "SHORT")
    if position:
        risk = position["risk_distance"]
        pnl_pct = (close / position["entry"] - 1) * (1 if side == "LONG" else -1) * 100
        if signal_sides is None:
            exit_signal = bool(evaluate_conditions(frame, strategy["exit"], entry_price=position["entry"]).iloc[-1])
        else:
            latest_side = signal_sides.iloc[-1]
            exit_signal = latest_side in {"BUY", "SELL"} and latest_side != ("BUY" if side == "LONG" else "SELL")
        reason = None
        candle = frame.iloc[-1]
        if (side == "LONG" and candle.low <= position["stop"]) or (side == "SHORT" and candle.high >= position["stop"]):
            close = position["stop"]
            reason = "Stop loss"
        elif (side == "LONG" and candle.high >= position["target"]) or (side == "SHORT" and candle.low <= position["target"]):
            close = position["target"]
            reason = "Take profit"
        elif exit_signal:
            reason = "Strategy exit"
        if reason:
            gross = (close - position["entry"]) * position["quantity"] * (1 if side == "LONG" else -1)
            fees = (position["entry"] + close) * position["quantity"] * 0.0004
            trade = {"side": side, "entry": position["entry"], "exit": close, "quantity": position["quantity"], "reason": reason,
                     "gross_pnl": gross, "fees": fees, "net_pnl": gross - fees, "opened_at": position["opened_at"], "closed_at": str(timestamp)}
            state["trades"].append(trade)
            state["balance"] += trade["net_pnl"]
            state["position"] = None
            state["last_closed_at"] = timestamp
            msg = f"Paper position closed: {reason} ({pnl_pct:.2f}% before fees)."
    else:
        latest_side = signal_sides.iloc[-1] if signal_sides is not None else None
        signal = latest_side in {"BUY", "SELL"} if signal_sides is not None else bool(evaluate_conditions(frame, strategy["entry"]).iloc[-1])
        last_close = state.get("last_closed_at")
        cooldown_ok = last_close is None or (timestamp - last_close).total_seconds() >= 30 * 60
        if signal and cooldown_ok:
            if signal_sides is not None:
                side = "LONG" if latest_side == "BUY" else "SHORT"
            entry = close
            risk_distance = entry * 0.01
            risk_amount = state["balance"] * float(strategy.get("risk_fraction", 0.01))
            qty = min(risk_amount / risk_distance, state["balance"] / entry)
            if qty > 0 and qty * entry <= state["balance"]:
                rr = float(strategy.get("rr", 2.0))
                stop = entry - risk_distance if side == "LONG" else entry + risk_distance
                target = entry + rr * risk_distance if side == "LONG" else entry - rr * risk_distance
                state["position"] = {"entry": entry, "stop": stop, "target": target, "risk_distance": risk_distance,
                                      "quantity": qty, "opened_at": str(timestamp), "side": side}
                msg = f"Paper position opened at {entry:.2f}; no broker order was sent."
    state["last_action"] = str(timestamp)
    return msg


def advance_ict_paper_account(frame, state, news_blackout=False):
    """Run one finalized candle through the preserved ICT signal, in memory only."""
    from demo_safety import ict_entry_gate
    from strategies.ict import ict_signal

    assert_demo_mode()
    validate_risk_controls(0.01, 2.0, 1.0)
    if frame is None or len(frame) < 100:
        raise ValueError("Insufficient market data for the ICT strategy.")
    state.setdefault("balance", 10_000.0)
    state.setdefault("position", None)
    state.setdefault("trades", [])
    state.setdefault("last_action", None)
    timestamp = frame.index[-1]
    if state["last_action"] == str(timestamp):
        return "No new finalized candle yet; ICT paper account unchanged."
    candles = [[int(ts.timestamp() * 1000), float(c.open), float(c.high), float(c.low), float(c.close), float(c.volume)]
               for ts, c in frame.iterrows()]
    signal_result = ict_signal(candles)
    signal_side = signal_result.get("side") if isinstance(signal_result, dict) else "NEUTRAL"
    allowed, gate_reason = ict_entry_gate(timestamp, news_blackout=news_blackout, last_trade_at=state.get("last_closed_at"))
    position = state["position"]
    msg = (
        f"No ICT paper entry: {gate_reason}"
        if not allowed
        else "No ICT paper entry: No valid ICT setup."
    )
    close = float(frame.close.iloc[-1])
    if position:
        side = position["side"]
        candle = frame.iloc[-1]
        stop_hit = candle.low <= position["stop"] if side == "LONG" else candle.high >= position["stop"]
        target_hit = candle.high >= position["target"] if side == "LONG" else candle.low <= position["target"]
        reason = "Stop loss" if stop_hit else "Take profit" if target_hit else None
        if not reason and signal_side == ("SHORT" if side == "LONG" else "LONG"):
            reason = "Opposite ICT signal"
        if reason:
            close = position["stop"] if stop_hit else position["target"] if target_hit else close * (0.9999 if side == "LONG" else 1.0001)
            gross = (close - position["entry"]) * position["quantity"] * (1 if side == "LONG" else -1)
            fees = (position["entry"] + close) * position["quantity"] * 0.0004
            trade = {"side": side, "entry": position["entry"], "exit": close, "quantity": position["quantity"], "reason": reason,
                     "gross_pnl": gross, "fees": fees, "net_pnl": gross - fees, "opened_at": position["opened_at"], "closed_at": str(timestamp)}
            state["trades"].append(trade)
            state["balance"] += trade["net_pnl"]
            state["position"] = None
            state["last_closed_at"] = timestamp
            msg = f"ICT paper position closed: {reason}."
    elif allowed and signal_side in {"LONG", "SHORT"}:
        entry = close * (1.0001 if signal_side == "LONG" else 0.9999)
        risk_distance = entry * 0.01
        risk_amount = state["balance"] * 0.01
        quantity = min(risk_amount / risk_distance, state["balance"] / entry)
        if quantity > 0 and quantity * entry <= state["balance"]:
            is_long = signal_side == "LONG"
            state["position"] = {"side": signal_side, "entry": entry,
                                  "stop": entry - risk_distance if is_long else entry + risk_distance,
                                  "target": entry + 2 * risk_distance if is_long else entry - 2 * risk_distance,
                                  "quantity": quantity, "opened_at": str(timestamp)}
            msg = f"ICT paper position opened ({signal_side}) at {entry:.2f}; no order was sent."
    state["last_action"] = str(timestamp)
    return msg

