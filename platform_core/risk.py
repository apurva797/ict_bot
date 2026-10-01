"""Risk engine.

The strategy answers "is there a setup?"; this engine independently answers
"is this trade allowed?". A signal can never reach paper execution without
passing :meth:`RiskEngine.validate`, and sizing is deterministic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from platform_core.errors import PlatformError
from platform_core.settings import RiskConfig
from platform_core.signals import Signal, risk_reward
from platform_core.status import DataHealth, Status


LOGGER = logging.getLogger("platform_core.risk")


@dataclass(frozen=True)
class RiskDecision:
    """Outcome of one risk evaluation, with a user-safe reason."""

    approved: bool
    status: Status
    reason: str
    quantity: float = 0.0
    risk_amount: float = 0.0
    notional: float = 0.0
    risk_distance: float = 0.0
    actual_rr: float = 0.0
    checks: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "status": self.status.value,
            "reason": self.reason,
            "quantity": self.quantity,
            "risk_amount": self.risk_amount,
            "notional": self.notional,
            "risk_distance": self.risk_distance,
            "actual_rr": self.actual_rr,
            "checks": self.checks,
        }


def position_size(balance: float, risk_fraction: float, entry: float, stop_loss: float,
                  max_leverage: float = 1.0) -> tuple[float, float]:
    """Return ``(quantity, risk_amount)`` for a valid stop; never raises.

    Sizing is the smaller of the risk-based size and the leverage-capped notional
    size, so neither risk nor exposure limits can be exceeded.
    """
    if balance <= 0 or risk_fraction <= 0 or entry <= 0 or stop_loss <= 0:
        return 0.0, 0.0
    risk_distance = abs(entry - stop_loss)
    if risk_distance <= 0:
        return 0.0, 0.0
    risk_amount = balance * risk_fraction
    from_risk = risk_amount / risk_distance
    from_leverage = (balance * max_leverage) / entry
    quantity = min(from_risk, from_leverage)
    return max(0.0, float(quantity)), float(risk_amount)


class RiskEngine:
    """Validates every signal against account, exposure, and time limits."""

    def __init__(self, config: RiskConfig | None = None):
        self.config = config or RiskConfig()

    def validate(self, signal: Signal, account: dict, *,
                 data_health: DataHealth = DataHealth.CONNECTED,
                 open_positions: int = 0,
                 cooldown_active: bool = False,
                 daily_trades: int = 0,
                 daily_r: float = 0.0) -> RiskDecision:
        config = self.config
        checks: dict[str, Any] = {}

        if not signal.is_actionable:
            return RiskDecision(False, Status.NO_VALID_SETUP,
                                "The strategy did not produce a complete setup with "
                                "entry, stop loss, and take profit.",
                                checks={"actionable": False})

        entry = float(signal.entry)
        stop = float(signal.stop_loss)
        target = float(signal.take_profit)
        balance = float(account.get("balance", 0.0))

        if entry <= 0 or stop <= 0 or target <= 0:
            return RiskDecision(False, Status.RISK_REJECTED,
                                "Entry, stop loss, and take profit must all be positive prices.",
                                checks=checks)
        if signal.direction == "LONG" and not stop < entry < target:
            return RiskDecision(False, Status.RISK_REJECTED,
                                "A long trade requires stop loss below entry and take profit above entry.",
                                checks=checks)
        if signal.direction == "SHORT" and not stop > entry > target:
            return RiskDecision(False, Status.RISK_REJECTED,
                                "A short trade requires stop loss above entry and take profit below entry.",
                                checks=checks)

        if balance <= 0:
            return RiskDecision(False, Status.RISK_REJECTED,
                                "The paper account has no available balance.",
                                checks=checks)

        actual_rr = risk_reward(entry, stop, target, signal.direction)
        checks["actual_rr"] = round(actual_rr, 4)
        if actual_rr < config.min_rr:
            return RiskDecision(False, Status.RISK_REJECTED,
                                f"Risk/reward {actual_rr:.2f} is below the required minimum "
                                f"{config.min_rr:.2f}R.", actual_rr=actual_rr, checks=checks)

        requested_risk = float(signal.metadata.get("risk_fraction", config.risk_per_trade))
        # A strategy asking for more risk than allowed is rejected, never clamped.
        risk_fraction = requested_risk
        if risk_fraction > config.risk_per_trade or risk_fraction <= 0:
            return RiskDecision(False, Status.RISK_REJECTED,
                                f"Risk per trade must stay at or below "
                                f"{config.risk_per_trade * 100:.2f}% of the balance.",
                                checks=checks)
        checks["risk_fraction"] = risk_fraction

        leverage = float(signal.metadata.get("leverage", 1.0))
        if leverage > config.max_leverage or leverage <= 0:
            return RiskDecision(False, Status.RISK_REJECTED,
                                f"Leverage must stay at or below {config.max_leverage:g}x.",
                                checks=checks)

        if data_health is DataHealth.UNAVAILABLE:
            return RiskDecision(False, Status.RISK_REJECTED,
                                "Market data is unavailable, so no trade can be validated.",
                                checks=checks)
        if data_health is DataHealth.STALE:
            return RiskDecision(False, Status.RISK_REJECTED,
                                "Market data is stale; entries are paused until fresh candles arrive.",
                                checks=checks)

        if open_positions >= config.max_open_positions:
            return RiskDecision(False, Status.RISK_REJECTED,
                                f"The account already holds the maximum of "
                                f"{config.max_open_positions} open position(s).",
                                checks=checks)
        if cooldown_active:
            return RiskDecision(False, Status.RISK_REJECTED,
                                f"The {config.cooldown_minutes} minute cooldown is still active.",
                                checks=checks)
        if daily_trades >= config.max_trades_per_day:
            return RiskDecision(False, Status.RISK_REJECTED,
                                f"The daily limit of {config.max_trades_per_day} trades is reached.",
                                checks=checks)
        if daily_r <= -abs(config.max_daily_loss_r):
            return RiskDecision(False, Status.RISK_REJECTED,
                                f"The daily loss limit of {config.max_daily_loss_r:g}R is reached.",
                                checks=checks)

        quantity, risk_amount = position_size(balance, risk_fraction, entry, stop,
                                              config.max_leverage)
        checks["quantity"] = quantity
        if quantity <= 0:
            return RiskDecision(False, Status.RISK_REJECTED,
                                "Position size resolved to zero; the trade was not sized.",
                                checks=checks)

        notional = quantity * entry
        if notional > balance * config.max_leverage + 1e-9:
            return RiskDecision(False, Status.RISK_REJECTED,
                                "Position notional exceeds the leverage limit for this account.",
                                checks=checks)

        checks.update({"balance": balance, "notional": round(notional, 8),
                       "data_health": data_health.value})
        return RiskDecision(
            approved=True,
            status=Status.SIGNAL_CREATED,
            reason=(f"Approved: risk {risk_fraction * 100:.2f}% ({risk_amount:,.2f}), "
                    f"R:R {actual_rr:.2f}, notional {notional:,.2f}."),
            quantity=quantity,
            risk_amount=risk_amount,
            notional=notional,
            risk_distance=abs(entry - stop),
            actual_rr=actual_rr,
            checks=checks,
        )