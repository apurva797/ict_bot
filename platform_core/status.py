"""Explicit, non-generic outcome codes shared by every layer of the platform."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    """Terminal state of one pipeline step. Never collapse these into one error."""

    OK = "OK"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NO_VALID_SETUP = "NO_VALID_SETUP"
    STRATEGY_ERROR = "STRATEGY_ERROR"
    RISK_REJECTED = "RISK_REJECTED"
    SIGNAL_CREATED = "SIGNAL_CREATED"
    PAPER_ORDER_CREATED = "PAPER_ORDER_CREATED"
    PAPER_ORDER_FILLED = "PAPER_ORDER_FILLED"
    PAPER_POSITION_CLOSED = "PAPER_POSITION_CLOSED"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class DataHealth(str, Enum):
    """Truthful market-data state. Stale data is never presented as live."""

    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class ExecutionEnv(str, Enum):
    """Execution environment. This build only ever trades PAPER."""

    PAPER = "PAPER"
    LIVE = "LIVE"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class Outcome:
    """A machine-readable result envelope used across service boundaries."""

    status: Status
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in {
            Status.OK,
            Status.SIGNAL_CREATED,
            Status.PAPER_ORDER_CREATED,
            Status.PAPER_ORDER_FILLED,
            Status.PAPER_POSITION_CLOSED,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "message": self.message,
            "detail": self.detail,
        }
