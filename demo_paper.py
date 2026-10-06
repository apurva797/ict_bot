"""In-memory simulated account; contains no exchange order integration."""

import pandas as pd

from demo_safety import assert_demo_mode, validate_risk_controls
from demo_strategy import evaluate_conditions
from config import MIN_RR
from engine.risk import validate_trade


def advance_paper_account(frame, strategy, state, signal_sides=None, journal_context=None, entry_levels=None):
    assert_demo_mode()
    validate_risk_controls(strategy.get("risk_fraction", 0.01), strategy.get("rr", 2), strategy.get("leverage", 1))
    if (not isinstance(frame, pd.DataFrame)
            or len(frame) < 35
            or any(column not in frame.columns for column in ("open", "high", "low", "close"))):
        raise ValueError("Valid OHLCV market data is required for paper trading.")
    if frame[["open", "high", "low", "close"]].isna().any().any():
        raise ValueError("Market data contains missing OHLCV values.")
    if not frame.index.is_monotonic_increasing:
        raise ValueError("Market data timestamps must be ordered.")
    if len(frame) < 35:
        raise ValueError("Insufficient market data for paper trading.")
    state.setdefault("balance", 10_000.0)
    state.setdefault("position", None)
    state.setdefault("trades", [])
    state.setdefault("last_action", None)
    timestamp = frame.index[-1]
    # Avoid duplicate or stale fills when a provider repeats/reorders candles.
    if state["last_action"] is not None:
        try:
            if pd.Timestamp(timestamp) <= pd.Timestamp(state["last_action"]):
                return "No new candle yet; paper account unchanged."
        except (TypeError, ValueError):
            raise ValueError("Paper account has an invalid last-action timestamp.")
    close = float(frame.close.iloc[-1])
    state["last_mark"] = close
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
            net = gross - fees
            reason_code = "SL" if reason == "Stop loss" else "TP" if reason == "Take profit" else "STRATEGY_EXIT"
            entry = float(position["entry"])
            exit_price = float(close)
            stop = float(position["stop"]) if "stop" in position else (entry * 0.99 if side == "LONG" else entry * 1.01)
            target = float(position["target"]) if "target" in position else (entry * 1.02 if side == "LONG" else entry * 0.98)
            quantity = float(position["quantity"])
            risk_distance = float(position.get("risk_distance", abs(entry - stop)))
            risk_amount = float(position.get("risk_amount", risk_distance * quantity if risk_distance > 0 else 0.0))
            r_multiple = net / risk_amount if risk_amount > 0 else 0.0
            opened_at_str = str(position["opened_at"])
            closed_at_str = str(timestamp)
            opened_at_ts = pd.Timestamp(position["opened_at"])
            if opened_at_ts.tzinfo is None:
                opened_at_ts = opened_at_ts.tz_localize("UTC")
            closed_at_ts = pd.Timestamp(timestamp)
            if closed_at_ts.tzinfo is None:
                closed_at_ts = closed_at_ts.tz_localize("UTC")
            duration_minutes = max(0.0, (closed_at_ts - opened_at_ts).total_seconds() / 60)
            trade = {
                "side": side, "entry": entry, "entry_price": entry,
                "exit": exit_price, "exit_price": exit_price,
                "stop": stop, "target": target, "quantity": quantity,
                "reason": reason, "exit_reason": reason_code,
                "risk_distance": risk_distance, "risk_amount": risk_amount,
                "gross_pnl": gross, "fees": fees, "net_pnl": net, "realized_pnl": net,
                "r_multiple": r_multiple,
                "opened_at": opened_at_str, "entry_time": opened_at_str,
                "closed_at": closed_at_str, "exit_time": closed_at_str,
                "duration_minutes": duration_minutes,
            }
            trade.update(position.get("journal_context", {}))
            trade["trade_id"] = len(state["trades"]) + 1
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
            rr = float(strategy.get("rr", 2.0))
            stop = entry - risk_distance if side == "LONG" else entry + risk_distance
            target = entry + rr * risk_distance if side == "LONG" else entry - rr * risk_distance
            if entry_levels is not None:
                try:
                    entry = float(entry_levels["entry"])
                    stop = float(entry_levels["stop"])
                    target = float(entry_levels["target"])
                    risk_distance = abs(entry - stop)
                    valid, rr, validation_reason = validate_trade(
                        entry, stop, target, side, min_rr=MIN_RR
                    )
                except (KeyError, TypeError, ValueError):
                    valid, validation_reason = False, "Invalid ATR trade levels"
                if not valid:
                    signal = False
                    msg = f"Paper entry blocked: {validation_reason}."
            if signal:
                risk_amount = state["balance"] * float(strategy.get("risk_fraction", 0.01))
                qty = min(risk_amount / risk_distance, state["balance"] / entry) if risk_distance > 0 else 0
                if qty > 0 and qty * entry <= state["balance"]:
                    state["position"] = {"entry": entry, "stop": stop, "target": target, "risk_distance": risk_distance,
                                          "risk_amount": risk_amount, "quantity": qty, "opened_at": str(timestamp), "side": side,
                                          "journal_context": dict(journal_context or {})}
                    msg = f"Paper position opened at {entry:.2f}; no broker order was sent."
    state["last_action"] = str(timestamp)
    return msg


def monitor_paper_position(state, market_snapshot):
    """Mark one paper account from a fresh quote/candle and close TP/SL once."""
    assert_demo_mode()
    if not isinstance(market_snapshot, dict):
        raise ValueError("Fresh market snapshot is required.")
    price = float(market_snapshot["price"])
    quote_time = market_snapshot["updated_at"]
    candle = market_snapshot["candle"]
    candle_time = candle["timestamp"]
    if price <= 0:
        raise ValueError("Market price must be positive.")
    state.setdefault("balance", 10_000.0)
    state.setdefault("starting_capital", 10_000.0)
    state.setdefault("position", None)
    state.setdefault("trades", [])
    state["last_mark"] = price
    state["live_price"] = price
    state["last_mark_updated_at"] = quote_time.isoformat()
    position = state.get("position")
    if position is None:
        return {"status": "FLAT", "message": "No open paper position.", "closed": False}

    side = position["side"]
    stop_hit = (price >= position["stop"] or float(candle["high"]) >= position["stop"]) if side == "SHORT" else (
        price <= position["stop"] or float(candle["low"]) <= position["stop"])
    target_hit = (price <= position["target"] or float(candle["low"]) <= position["target"]) if side == "SHORT" else (
        price >= position["target"] or float(candle["high"]) >= position["target"])
    # A candle that touches both levels is resolved conservatively as a stop.
    if not stop_hit and not target_hit:
        return {"status": "OPEN — TARGET NOT HIT", "message": "Open", "closed": False}

    reason_code = "SL" if stop_hit else "TP"
    reason = "Stop loss" if stop_hit else "Take profit"
    exit_price = float(position["stop"] if stop_hit else position["target"])
    quantity = float(position["quantity"])
    entry = float(position["entry"])
    direction = 1 if side == "LONG" else -1
    gross = (exit_price - entry) * quantity * direction
    fees = (entry + exit_price) * quantity * 0.0004
    net = gross - fees
    risk_distance = float(position.get("risk_distance", abs(entry - float(position["stop"]))))
    risk_amount = float(position.get("risk_amount", risk_distance * quantity))
    r_multiple = net / risk_amount if risk_amount > 0 else 0.0
    opened_at = pd.Timestamp(position["opened_at"])
    if opened_at.tzinfo is None:
        opened_at = opened_at.tz_localize("UTC")
    closed_at = quote_time
    trade = {
        "side": side, "entry": entry, "entry_price": entry,
        "exit": exit_price, "exit_price": exit_price,
        "stop": float(position["stop"]), "target": float(position["target"]),
        "quantity": quantity, "reason": reason, "exit_reason": reason_code,
        "risk_distance": risk_distance, "risk_amount": risk_amount,
        "gross_pnl": gross, "fees": fees, "net_pnl": net, "realized_pnl": net,
        "r_multiple": r_multiple,
        "opened_at": position["opened_at"], "entry_time": position["opened_at"],
        "closed_at": closed_at.isoformat(), "exit_time": closed_at.isoformat(),
        "duration_minutes": max(0.0, (closed_at - opened_at).total_seconds() / 60),
    }
    trade.update(position.get("journal_context", {}))
    trade["trade_id"] = len(state["trades"]) + 1
    state["trades"].append(trade)
    state["balance"] = float(state["balance"]) + net
    state["position"] = None
    state["last_closed_at"] = candle_time
    # This also prevents a manual paper-entry action from replaying this same candle.
    state["last_action"] = str(candle_time)
    return {"status": f"{reason_code} HIT — PAPER POSITION CLOSED", "message": reason, "closed": True, "trade": trade}


def advance_ict_paper_account(frame, state, news_blackout=False, journal_context=None):
    """Run one finalized candle through the preserved ARJUNA signal, in memory only.

    The flow is Signal -> Risk Engine -> Paper Position. Levels come from real
    ATR volatility, sizing and every limit come from configuration, and no
    price is fabricated. Time of day never blocks an entry.
    """
    from config import (
        DEFAULT_RR,
        MAX_DAILY_LOSS_R,
        MAX_OPEN_POSITIONS,
        MAX_TRADES_PER_DAY,
        RISK_PER_TRADE,
    )
    from demo_safety import ict_entry_gate
    from platform_core.errors import PlatformError
    from platform_core.ict import analyze_ict
    from platform_core.risk import RiskEngine
    from platform_core.settings import RiskConfig
    from platform_core.signals import Signal

    assert_demo_mode()
    validate_risk_controls(RISK_PER_TRADE, DEFAULT_RR, 1.0)
    if frame is None or len(frame) < 100:
        # Insufficient data is a normal market state, not a crash: report it the
        # same way as every other no-entry outcome so the UI can explain it.
        available = 0 if frame is None else len(frame)
        return (f"No ARJUNA paper entry: Insufficient market data "
                f"({available} candles; 100 required).")
    state.setdefault("balance", 10_000.0)
    state.setdefault("position", None)
    state.setdefault("trades", [])
    state.setdefault("last_action", None)
    timestamp = frame.index[-1]
    if state["last_action"] == str(timestamp):
        return "No new finalized candle yet; ARJUNA paper account unchanged."

    try:
        analysis = analyze_ict(frame, rr=DEFAULT_RR)
    except PlatformError as exc:
        state["last_action"] = str(timestamp)
        return f"No ARJUNA paper entry: {exc.message}"
    signal_side = analysis.side
    allowed, gate_reason = ict_entry_gate(timestamp, news_blackout=news_blackout, last_trade_at=state.get("last_closed_at"))
    position = state["position"]
    msg = (
        f"No ARJUNA paper entry: {gate_reason}"
        if not allowed
        else "No ARJUNA paper entry: No valid ARJUNA setup."
    )
    close = float(frame.close.iloc[-1])
    state["last_mark"] = close
    if position:
        side = position["side"]
        candle = frame.iloc[-1]
        stop_hit = candle.low <= position["stop"] if side == "LONG" else candle.high >= position["stop"]
        target_hit = candle.high >= position["target"] if side == "LONG" else candle.low <= position["target"]
        reason = "Stop loss" if stop_hit else "Take profit" if target_hit else None
        if not reason and signal_side == ("SHORT" if side == "LONG" else "LONG"):
            reason = "Opposite ARJUNA signal"
        if reason:
            close = position["stop"] if stop_hit else position["target"] if target_hit else close * (0.9999 if side == "LONG" else 1.0001)
            gross = (close - position["entry"]) * position["quantity"] * (1 if side == "LONG" else -1)
            fees = (position["entry"] + close) * position["quantity"] * 0.0004
            net = gross - fees
            reason_code = "SL" if stop_hit else "TP" if target_hit else "OPPOSITE_SIGNAL"
            entry = float(position["entry"])
            exit_price = float(close)
            stop = float(position["stop"]) if "stop" in position else (entry * 0.99 if side == "LONG" else entry * 1.01)
            target = float(position["target"]) if "target" in position else (entry * 1.02 if side == "LONG" else entry * 0.98)
            quantity = float(position["quantity"])
            risk_distance = float(position.get("risk_distance", abs(entry - stop)))
            risk_amount = float(position.get("risk_amount", risk_distance * quantity if risk_distance > 0 else 0.0))
            r_multiple = net / risk_amount if risk_amount > 0 else 0.0
            opened_at_str = str(position["opened_at"])
            closed_at_str = str(timestamp)
            opened_at_ts = pd.Timestamp(position["opened_at"])
            if opened_at_ts.tzinfo is None:
                opened_at_ts = opened_at_ts.tz_localize("UTC")
            closed_at_ts = pd.Timestamp(timestamp)
            if closed_at_ts.tzinfo is None:
                closed_at_ts = closed_at_ts.tz_localize("UTC")
            duration_minutes = max(0.0, (closed_at_ts - opened_at_ts).total_seconds() / 60)
            trade = {
                "side": side, "entry": entry, "entry_price": entry,
                "exit": exit_price, "exit_price": exit_price,
                "stop": stop, "target": target, "quantity": quantity,
                "reason": reason, "exit_reason": reason_code,
                "risk_distance": risk_distance, "risk_amount": risk_amount,
                "gross_pnl": gross, "fees": fees, "net_pnl": net, "realized_pnl": net,
                "r_multiple": r_multiple,
                "opened_at": opened_at_str, "entry_time": opened_at_str,
                "closed_at": closed_at_str, "exit_time": closed_at_str,
                "duration_minutes": duration_minutes,
            }
            trade.update(position.get("journal_context", {}))
            trade["trade_id"] = len(state["trades"]) + 1
            state["trades"].append(trade)
            state["balance"] += trade["net_pnl"]
            state["position"] = None
            state["last_closed_at"] = timestamp
            msg = f"ARJUNA paper position closed: {reason}."
    elif allowed and signal_side in {"LONG", "SHORT"}:
        # ATR-derived levels and the shared risk engine decide the entry; the
        # strategy never bypasses risk management.
        signal = Signal(
            strategy_id="ict", strategy_name="ARJUNA", symbol=str(state.get("market", "BTC/USDT")),
            timeframe=str(state.get("timeframe", "5m")), direction=signal_side,
            timestamp=timestamp, reason=analysis.reason, entry=analysis.entry,
            stop_loss=analysis.stop, take_profit=analysis.target,
            metadata={"risk_fraction": RISK_PER_TRADE, "leverage": 1.0},
        )
        engine = RiskEngine(RiskConfig(risk_per_trade=RISK_PER_TRADE, default_rr=DEFAULT_RR))
        decision = engine.validate(
            signal, state,
            open_positions=1 if position else 0,
            daily_trades=int(state.get("daily_entry_count", 0)),
            daily_r=float(state.get("daily_r", 0.0)),
        )
        if not decision.approved:
            msg = f"No ARJUNA paper entry: {decision.reason}"
        else:
            entry = float(signal.entry)
            state["position"] = {
                "side": signal_side, "entry": entry,
                "stop": float(signal.stop_loss), "target": float(signal.take_profit),
                "quantity": decision.quantity,
                "risk_distance": decision.risk_distance,
                "risk_amount": decision.risk_amount,
                "opened_at": str(timestamp),
                "journal_context": dict(journal_context or {}),
            }
            msg = (f"ARJUNA paper position opened ({signal_side}) at {entry:.2f} "
                   f"with {decision.actual_rr:.2f}R; no order was sent.")
    state["last_action"] = str(timestamp)
    return msg
