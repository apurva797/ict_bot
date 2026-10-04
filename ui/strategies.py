"""Strategy catalogue for the product surface.

The registry in :mod:`platform_strategies` is the source of truth for what can
actually produce a signal. This module only *presents* that registry, plus a
clearly-labelled roadmap of strategies that are not implemented yet. A planned
strategy is never given a price, a P&L, or a status that implies it runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from platform_strategies import strategy_registry

ARJUN_DISPLAY_NAME = "ARJUN"
ARJUN_SUBTITLE = "ICT Strategy"
# The internal strategy key stays ``ict`` so the existing engine, paper
# accounts, and journal records keep resolving unchanged.
ARJUN_KEY = "ict"

# Strategies the product is designed for but that have no signal
# implementation yet. They are rendered as "Coming soon" and are never
# selectable, so the roadmap is visible without implying live capability.
ROADMAP = (
    ("Momentum", "Trend continuation"),
    ("Mean Reversion", "Range reversion"),
    ("Trend Following", "Multi-timeframe trend"),
    ("Custom Strategy", "Your own rules"),
)


@dataclass(frozen=True)
class StrategyEntry:
    """One selectable strategy and its presentation metadata."""

    key: str
    name: str
    subtitle: str
    category: str
    version: str
    description: str

    @property
    def status_label(self) -> str:
        """Short human label for the strategy's implementation status."""
        return "Built-in" if self.category == "Built-in" else self.category


def load_catalogue() -> tuple[StrategyEntry, ...]:
    """Build the user-facing strategy list from the live registry.

    Reading the registry rather than hard-coding names means a newly
    registered plugin appears automatically, and the display layer cannot
    drift from what the engine will actually run.
    """
    entries: list[StrategyEntry] = []
    for metadata in strategy_registry.list():
        is_arjun = metadata.id == ARJUN_KEY
        entries.append(StrategyEntry(
            key=metadata.id,
            name=ARJUN_DISPLAY_NAME if is_arjun else metadata.name,
            subtitle=ARJUN_SUBTITLE if is_arjun else metadata.category,
            category=metadata.category,
            version=metadata.version,
            description=metadata.description,
        ))
    return tuple(entries)


def find(key: str) -> StrategyEntry | None:
    """One catalogue entry by internal key."""
    for entry in load_catalogue():
        if entry.key == key:
            return entry
    return None


def display_name(key: str) -> str:
    """User-facing name for an internal strategy key.

    The engine keeps ``ict`` everywhere internally; only the UI says Arjun.
    """
    if key == ARJUN_KEY:
        return ARJUN_DISPLAY_NAME
    entry = find(key)
    return entry.name if entry else str(key).replace(".", " ").title()


def roadmap_cards() -> list[str]:
    """Cards for strategies that are planned but not yet implemented."""
    cards = []
    for name, subtitle in ROADMAP:
        cards.append(
            '<div class="ui-card ui-card--flat" style="opacity:.72">'
            '<div class="ui-row"><div>'
            f'<div style="font-weight:640">{name}</div>'
            f'<div class="ui-sub">{subtitle}</div></div>'
            '<span class="ui-pill">Coming soon</span></div>'
            '<div class="ui-sub" style="margin-top:.45rem">'
            "No signal implementation yet. Not selectable, and it never produces a trade.</div>"
            "</div>"
        )
    return cards