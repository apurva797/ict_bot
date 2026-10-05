"""Application navigation.

Routing is session state rather than Streamlit multipage: this app must keep
one mounted chart and one paper account across screens, and a page swap tears
down and rebuilds both.

The bottom bar on mobile is a *mirror* of the real segmented control, not a
second control. A non-interactive HTML affordance cannot be keyboard-navigated
or announced, so the widget above it stays the single source of truth for both
layout and accessibility.
"""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from . import components as ui

ROUTE_KEY = "route"

HOME = "Home"
MARKETS = "Markets"
TRADE = "Trade"
PORTFOLIO = "Portfolio"
RESEARCH = "Research"
SETTINGS = "Settings"

ROUTES = (HOME, MARKETS, TRADE, PORTFOLIO, RESEARCH, SETTINGS)


@dataclass(frozen=True)
class NavItem:
    """One destination: a label, an icon path, and its route key."""

    label: str
    icon: str
    route: str

    def svg(self) -> str:
        """Inline 24x24 stroke icon. Paths are original to this project."""
        return (f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" '
                f'aria-hidden="true">{self.icon}</svg>')


# Icon geometry is hand-written rather than pulled from an icon font, so the
# app ships no extra network request for navigation.
NAV_ITEMS = (
    NavItem(HOME, '<path d="M3 10.5 12 3l9 7.5"/><path d="M5.5 9.5V20h13V9.5"/>'
                  '<path d="M9.5 20v-5.5h5V20"/>', HOME),
    NavItem(MARKETS, '<path d="M3 17.5 9 11l4 4 3-3 5 5.5"/><path d="M3 20.5h18"/>', MARKETS),
    NavItem(TRADE, '<path d="M4 19V9"/><path d="M10 19V5"/><path d="M16 19v-7"/>'
                   '<path d="M22 19H2"/>', TRADE),
    NavItem(PORTFOLIO, '<rect x="3" y="7.5" width="18" height="13" rx="2.5"/>'
                       '<path d="M8.5 7.5V6a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v1.5"/>'
                       '<path d="M3 12.5h18"/>', PORTFOLIO),
    NavItem(RESEARCH, '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>'
                      '<path d="M8.5 11h5"/>', RESEARCH),
    NavItem(SETTINGS, '<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z"/>', SETTINGS),
)

_BY_ROUTE = {item.route: item for item in NAV_ITEMS}


def current_route() -> str:
    """The active route, defaulting to Home.

    Reading through ``session_state`` rather than binding it to a widget means
    any screen can navigate (for example the strategy picker jumping straight
    to Trade) without owning a navigation widget of its own.
    """
    route = st.session_state.get(ROUTE_KEY, HOME)
    return route if route in ROUTES else HOME


def go(route: str) -> None:
    """Navigate programmatically from a callback."""
    if route in ROUTES:
        st.session_state[ROUTE_KEY] = route


def render(active: str | None = None) -> str:
    """Render navigation and return the active route.

    The segmented control owns ``ROUTE_KEY``. The mobile bar below it is
    presentational only and is hidden above 768px by the stylesheet.
    """
    choice = st.segmented_control(
        "Section", list(ROUTES), key=ROUTE_KEY, label_visibility="collapsed",
        width="stretch",
    )
    route = choice if choice in ROUTES else current_route()
    ui.html_block(bottom_nav(route))
    return route


def bottom_nav(active: str) -> str:
    """The mobile bottom bar: a presentational mirror of the active route."""
    items = []
    for item in NAV_ITEMS:
        modifier = " ui-nav-item--active" if item.route == active else ""
        current = ' aria-current="page"' if item.route == active else ""
        items.append(
            f'<div class="ui-nav-item{modifier}"{current}>'
            f"{item.svg()}<span>{ui.esc(item.label)}</span></div>"
        )
    return (f'<nav class="ui-bottom-nav" aria-label="Primary sections">'
            f'{"".join(items)}</nav>')


def sidebar_nav(active: str) -> None:
    """Desktop sidebar navigation summary.

    The control itself lives in the main body so it is one element on every
    viewport; this block only names the sections and where the account state
    lives, which is what the sidebar is for.
    """
    labels = " · ".join(item.label for item in NAV_ITEMS)
    ui.html_block(
        ui.card(
            f'<div class="ui-label">Sections</div>'
            f'<div class="ui-sub" style="margin-top:.25rem">{ui.esc(labels)}</div>'
            f'<div class="ui-sub">Currently in <strong>{ui.esc(active)}</strong></div>',
            variant="flat",
        )
    )