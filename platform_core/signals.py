"""Structured signal objects shared by every strategy and every consumer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from platform_core.status import Status


@dataclass(frozen=True)
class Signal:
    """A strategy decision with enough detail for risk, execution, and UI.

    ``confidence`` is only populated when a strategy actually calculates it; it
    is never synthesized.
    """

    strategy_id: str
    strategy_name: str
    symbol: str
    timeframe: str
    direction: str                      # LONG / SHORT / NEUTRAL
    timestamp: pd.Timestamp
    reason: str
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    risk_reward: float | None = None
    confidence: float | None = None
    metadata: dict = field(default_factory=dict)
    status: Status = Status.SIGNAL_CREATED

    @property
    def is_actionable(self) -> bool:
        return (self.direction in {"LONG", "SHORT"}
                and self.entry is not None
                and self.stop_loss is not None
                and self.take_profit is not None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "timestamp": self.timestamp.isoformat() if self.timestamp is not None else None,
            "reason": self.reason,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "risk_reward": self.risk_reward,
            "confidence": self.confidence,
            "actionable": self.is_actionable,
            "status": self.status.value,
            "metadata": self.metadata,
        }


def risk_reward(entry: float, stop_loss: float, take_profit: float, direction: str) -> float:
    """Geometric reward-to-risk; 0.0 when levels are invalid."""
    if direction == "LONG":
        risk = entry - stop_loss
        reward = take_profit - entry
    elif direction == "SHORT":
        risk = stop_loss - entry
        reward = entry - take_profit
    else:
        return 0.0
    if risk <= 0:
        return 0.0
    return reward / risk