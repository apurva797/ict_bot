"""Top-level UI package for the trading platform.

Everything here is presentation. The quantitative engine stays in
``platform_core``, the strategy implementations in ``strategies``/``bot.py``,
and the risk and execution rules in ``platform_core.risk`` /
``platform_core.execution``. No module under ``ui`` may size a position,
approve a trade, or change a strategy's decision.
"""

from __future__ import annotations

__all__ = [
    "assistant",
    "components",
    "marketdata",
    "navigation",
    "orderpanel",
    "research",
    "screens",
    "state",
    "strategies",
    "theme",
]