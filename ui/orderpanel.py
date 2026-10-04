"""Order panel.

Every sizing and approval decision here is delegated to
:class:`platform_core.risk.RiskEngine`. This module computes *nothing* about
position size, notional, or approval: it collects intent from the user, asks
the existing engine for a decision, and renders that decision verbatim.

That is the whole point of the panel. A user sees exactly what the risk engine
approved, including the reason when it refuses, and there is no path from the
UI to a paper position that skips validation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

import pandas as pd
import streamlit as st

from demo_safety import SafetyError, assert_demo_mode
from platform_core.errors import PlatformError
from platform_core.risk import RiskDecision, RiskEngine
from platform_core.settings import load_settings
from platform_core.signals import Signal
from platform_core.status import DataHealth
from ui import components as ui

LOGGER = logging.getLogger("ui.orderpanel")


@dataclass(frozen=True)
class OrderIntent:
    """What the user asked for, before any validation has happened."""

    symbol: str
    timeframe: str
    direction: str            # LONG / SHORT
    entry: float
    stop_loss: float
    take_profit: float
    strategy_id: str
    strategy_name: str
    reason: str
    timestamp: pd.Timestamp


def engine() -> RiskEngine:
    """The shared risk engine, built from the platform's configured limits."""
    return RiskEngine(load_settings().risk)


def evaluate(intent: OrderIntent, account: dict, *,
             data_health: DataHealth = DataHealth.CONNECTED,
             open_positions: int = 0, cooldown_active: bool = False,
             daily_trades: int = 0, daily_r: float = 0.0) -> RiskDecision:
    """Ask the existing risk engine whether this intent is allowed.

    ``assert_demo_mode`` runs first so a safety-lock failure can never be
    reported as an ordinary risk rejection.
    """
    assert_demo_mode()
    signal = Signal(
        strategy_id=intent.strategy_id,
        strategy_name=intent.strategy_name,
        symbol=intent.symbol,
        timeframe=intent.timeframe,
        direction=intent.direction,
        timestamp=intent.timestamp,
        reason=intent.reason,
        entry=intent.entry,
        stop_loss=intent.stop_loss,
        take_profit=intent.take_profit,
    )
    return engine().validate(
        signal, account, data_health=data_health, open_positions=open_positions,
        cooldown_active=cooldown_active, daily_trades=daily_trades, daily_r=daily_r,
    )


def levels_from_signal(direction: str, entry: float, stop: float,
                       target: float) -> tuple[float, float]:
    """Check level geometry before the levels reach the risk engine.

    Only ordering and positivity are checked here. Whether the trade is
    *allowed* remains entirely the engine's decision.
    """
    if direction == "LONG":
        valid = stop < entry < target
    elif direction == "SHORT":
        valid = target < entry < stop
    else:
        return 0.0, 0.0
    return (float(entry), float(stop)) if valid else (0.0, 0.0)


def blocked_preview(decision: RiskDecision) -> str:
    """Render a refusal with the engine's own reason and the limits in force."""
    risk = load_settings().risk
    return ui.card(
        f'<div class="ui-row">{ui.status_pill("Risk blocked", "loss", dot=True)}</div>'
        f'<div style="margin-top:.55rem">{ui.esc(decision.reason)}</div>'
        f'<div class="ui-sub" style="margin-top:.5rem">No position was sized. '
        f"Limits in force: {risk.risk_per_trade * 100:.2f}% risk per trade, "
        f"{risk.min_rr:g}R minimum reward, {risk.max_leverage:g}x maximum leverage."
        "</div>",
        variant="loss",
    )


def preview(decision: RiskDecision, account_balance: float) -> str:
    """Render the ticket: what the engine approved, and the loss if SL is hit.

    The "if SL is hit" figure is stated explicitly in currency terms before a
    user can confirm, because that single number decides whether a trade is
    acceptable to them.
    """
    if not decision.approved:
        return blocked_preview(decision)
    currency = "$"
    planned_loss = decision.risk_amount
    planned_profit = decision.risk_amount * decision.actual_rr
    balance_after_loss = account_balance - planned_loss
    pairs = ui.rows([
        ("Lot size", ui.esc(f"{decision.quantity:.8g}")),
        ("Notional", f"{currency}{decision.notional:,.2f}"),
        ("Risk distance", ui.esc(f"{decision.risk_distance:,.6g}")),
        ("Balance after loss", f"{currency}{balance_after_loss:,.2f}"),
    ])
    body = (
        f'<div class="ui-row">{ui.status_pill("Approved by risk engine", "profit", dot=True)}'
        f"{ui.pill(f'{decision.actual_rr:.2f}R')}</div>"
        f'<div style="margin-top:.6rem">{pairs}</div>'
        f'<div style="margin-top:.7rem">'
        f'<div class="ui-label">If the stop loss is hit</div>'
        f'<div class="ui-value ui-value--sm ui-neg" style="margin-top:.15rem">'
        f"estimated loss = {currency}{planned_loss:,.2f}</div></div>"
        f'<div style="margin-top:.5rem">'
        f'<div class="ui-label">If the take profit is hit</div>'
        f'<div class="ui-value ui-value--sm ui-pos" style="margin-top:.15rem">'
        f"estimated profit = {currency}{planned_profit:,.2f}</div></div>"
        f'<div class="ui-sub" style="margin-top:.55rem">{ui.esc(decision.reason)}</div>'
    )
    return ui.card(body, variant="profit")


def risk_preview(decision: RiskDecision) -> str:
    """Rejected-preview markup for reuse in compact layouts."""
    return blocked_preview(decision)


def decide(intent: OrderIntent, account: dict, **context) -> RiskDecision | None:
    """Run the risk engine, reporting failures instead of raising.

    Returns ``None`` only when the safety lock or the engine itself failed;
    an ordinary refusal is a valid :class:`RiskDecision` with ``approved``
    false, and must render as a refusal rather than as an error.
    """
    try:
        return evaluate(intent, account, **context)
    except SafetyError as exc:
        LOGGER.warning("Demo safety lock rejected an order: %s", exc)
        return None
    except (PlatformError, ValueError, TypeError) as exc:
        LOGGER.exception("Risk evaluation failed")
        return None


def render_intent(intent: OrderIntent, account: dict, *, key_prefix: str,
                  health: DataHealth = DataHealth.CONNECTED,
                  open_positions: int = 0, daily_trades: int = 0,
                  daily_r: float = 0.0) -> RiskDecision | None:
    """Show the ticket for a proposed trade and let the user confirm it.

    Returns the approved decision when the user confirms, so the caller can
    hand exactly those engine-approved levels to the paper engine. Returns
    ``None`` when nothing was confirmed or the trade was refused.
    """
    risk = load_settings().risk
    direction = intent.direction if intent.direction in {"LONG", "SHORT"} else "LONG"

    ui.html_block(ui.section_head("Order", f"{intent.symbol} · {intent.timeframe}"))
    checked_entry, checked_stop = levels_from_signal(
        direction, intent.entry, intent.stop_loss, intent.take_profit)
    if not checked_entry:
        ui.html_block(ui.error_state(
            "Invalid trade levels",
            f"A {direction.lower()} needs stop < entry < target. Received entry "
            f"{intent.entry}, stop {intent.stop_loss}, target {intent.take_profit}.",
        ))
        return None

    checked = replace(intent, entry=checked_entry, stop_loss=checked_stop)
    context = {
        "data_health": health, "open_positions": open_positions,
        "daily_trades": daily_trades, "daily_r": daily_r,
    }
    decision = decide(checked, account, **context)
    if decision is None:
        ui.html_block(ui.error_state(
            "Risk check unavailable",
            "The risk engine could not evaluate this order, so no position was "
            "sized. Nothing was sent anywhere.",
        ))
        return None

    balance = float(account.get("balance", 0.0) or 0.0)
    ui.html_block(preview(decision, balance))

    if not decision.approved:
        st.caption(
            f"The risk engine will not size this trade. Fixed demo limits: "
            f"{risk.risk_per_trade * 100:.2f}% risk, {risk.min_rr:g}R minimum, "
            f"{risk.max_leverage:g}x maximum leverage."
        )
        return None

    if st.button(f"Confirm paper {direction.title()}", key=f"{key_prefix}_confirm",
                 type="primary", width="stretch"):
        # Re-validate at confirmation time so a balance change between preview
        # and click cannot slip past the engine's limits.
        fresh = decide(checked, account, **context)
        if fresh is not None and fresh.approved:
            return fresh
        reason = fresh.reason if fresh is not None else "Risk engine unavailable."
        ui.html_block(ui.error_state("Rejected on confirmation", reason))
    return None