"""Paper execution engine.

This is the only place an order can be created, and it is hard-wired to the
PAPER environment. There is no exchange or broker client in this module, and
``reject_live_order`` remains the mandatory guard for any future live path.
"""

from __future__ import annotations

import itertools
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from demo_safety import assert_demo_mode, reject_live_order
from platform_core.errors import PlatformError
from platform_core.risk import RiskDecision, RiskEngine
from platform_core.settings import RiskConfig
from platform_core.signals import Signal
from platform_core.status import DataHealth, ExecutionEnv, Status


LOGGER = logging.getLogger("platform_core.execution")

_ORDER_SEQUENCE = itertools.count(1)
_POSITION_SEQUENCE = itertools.count(1)


def _new_order_id() -> str:
    return f"paper-order-{int(time.time() * 1000)}-{next(_ORDER_SEQUENCE)}"


def _new_position_id() -> str:
    return f"paper-position-{next(_POSITION_SEQUENCE)}"


@dataclass
class PaperOrder:
    """Lifecycle: CREATED -> FILLED (position) -> CLOSED (realized P&L)."""

    order_id: str
    user_id: str
    portfolio_id: str
    strategy_id: str
    symbol: str
    timeframe: str
    side: str
    quantity: float
    entry: float
    stop_loss: float
    take_profit: float
    risk_amount: float
    status: str = "CREATED"
    environment: str = ExecutionEnv.PAPER.value
    created_at: pd.Timestamp = field(default_factory=lambda: pd.Timestamp.now(tz="UTC"))
    filled_at: pd.Timestamp | None = None
    closed_at: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    fees: float = 0.0
    realized_pnl: float = 0.0
    position_id: str | None = None
    risk_decision: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.__dict__,
            "created_at": self.created_at.isoformat(),
            "filled_at": self.filled_at.isoformat() if self.filled_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
        }


class PaperExecutionEngine:
    """Deterministic paper fills with an auditable order trail.

    Fills use the real market price supplied by the caller; the engine never
    invents a price and never claims a fill it could not observe.
    """

    def __init__(self, risk_engine: RiskEngine | None = None, config: RiskConfig | None = None,
                 user_id: str = "local", portfolio_id: str = "default"):
        self.config = config or RiskConfig()
        self.risk_engine = risk_engine or RiskEngine(self.config)
        self.user_id = user_id
        self.portfolio_id = portfolio_id
        self.orders: list[PaperOrder] = []

    def submit(self, signal: Signal, account: dict, *, fill_price: float | None = None,
               data_health: DataHealth | None = None, open_positions: int = 0,
               cooldown_active: bool = False, daily_trades: int = 0,
               daily_r: float = 0.0) -> tuple:
        """Run the risk engine, then create a paper order."""
        assert_demo_mode()
        decision = self.risk_engine.validate(
            signal, account,
            data_health=data_health if data_health is not None else DataHealth.CONNECTED,
            open_positions=open_positions, cooldown_active=cooldown_active,
            daily_trades=daily_trades, daily_r=daily_r,
        )
        if not decision.approved:
            LOGGER.info("Paper order rejected by risk: %s", decision.reason)
            return None, decision

        order = PaperOrder(
            order_id=_new_order_id(),
            user_id=self.user_id,
            portfolio_id=self.portfolio_id,
            strategy_id=signal.strategy_id,
            symbol=signal.symbol,
            timeframe=signal.timeframe,
            side=signal.direction,
            quantity=decision.quantity,
            entry=float(signal.entry),
            stop_loss=float(signal.stop_loss),
            take_profit=float(signal.take_profit),
            risk_amount=decision.risk_amount,
            filled_at=pd.Timestamp.now(tz="UTC") if fill_price is not None else None,
            risk_decision=decision.to_dict(),
        )
        if fill_price is not None:
            order.status = "FILLED"
        self.orders.append(order)
        return order, decision

    def open_position(self, order: PaperOrder) -> PaperOrder:
        if order.status != "FILLED":
            raise PlatformError("Only a filled order can open a paper position.",
                                Status.RISK_REJECTED)
        order.position_id = _new_position_id()
        return order

    def close_position(self, order: PaperOrder, exit_price: float, reason: str,
                       timestamp: pd.Timestamp | None = None) -> PaperOrder:
        """Close a filled order and book fees and realized P&L from real prices."""
        if exit_price is None or exit_price <= 0:
            raise PlatformError("A valid exit price is required to close a paper position.",
                                Status.DATA_UNAVAILABLE)
        timestamp = timestamp or pd.Timestamp.now(tz="UTC")
        direction = 1 if order.side == "LONG" else -1
        gross = (float(exit_price) - order.entry) * order.quantity * direction
        fees = (order.entry + float(exit_price)) * order.quantity * self.config.fee_rate
        order.exit_price = float(exit_price)
        order.exit_reason = reason
        order.closed_at = timestamp
        order.fees = fees
        order.realized_pnl = gross - fees
        order.status = "CLOSED"
        return order

    def open_orders(self) -> list[PaperOrder]:
        return [order for order in self.orders if order.status in {"CREATED", "FILLED"}]

    def to_rows(self) -> list[dict[str, Any]]:
        return [order.to_dict() for order in self.orders]

    def place_live_order(self, *_args, **_kwargs):
        """Any attempt to reach a live broker fails closed."""
        reject_live_order(*_args, **_kwargs)