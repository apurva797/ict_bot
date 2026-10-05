"""Command palette, keyboard-shortcut help, and the key accelerator.

Every command performs a real action — navigation, the session theme, or
display density. There are no placeholder entries, and this module never
touches market data, risk, or execution state: it changes where the user is
looking, never what the engines do.

The accelerator script is progressive enhancement. The palette and shortcut
help are plain Streamlit buttons first, so a browser where the injected
key-listener does not run still offers the full feature set by Tab + Enter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import streamlit as st

from ui import components as ui
from ui import navigation

LOGGER = logging.getLogger("ui.commands")

PALETTE_KEY = "ui_palette_open"
SHORTCUTS_KEY = "ui_shortcuts_open"
QUERY_KEY = "ui_palette_query"

# How many results the palette shows at once: enough to cover every
# destination from one query, short enough to stay scannable.
RESULT_LIMIT = 8


@dataclass(frozen=True)
class Command:
    """One palette entry: presentation plus the action id it executes."""

    id: str
    label: str
    kind: str
    keywords: str
    action: str          # "route:<name>" | "theme:<name>" | "density:<name>"


COMMANDS: tuple[Command, ...] = (
    Command("go_home", "Go to Home", "Navigate",
            "dashboard overview greeting summary", "route:Home"),
    Command("go_markets", "Go to Markets", "Navigate",
            "watchlist quotes prices instruments", "route:Markets"),
    Command("go_trade", "Open the Trade terminal", "Navigate",
            "chart arjun ict quant ai strategy strategies order ticket paper",
            "route:Trade"),
    Command("go_portfolio", "Go to Portfolio", "Navigate",
            "equity positions pnl history performance drawdown", "route:Portfolio"),
    Command("go_research", "Open Research · backtesting", "Navigate",
            "backtest optimization walk forward monte carlo robustness journal",
            "route:Research"),
    Command("go_settings", "Go to Settings", "Navigate",
            "configuration display risk limits environment", "route:Settings"),
    Command("theme_dark", "Theme · Dark terminal", "Display",
            "dark night black appearance", "theme:dark"),
    Command("theme_light", "Theme · Light", "Display",
            "day white bright appearance", "theme:light"),
    Command("density_comfortable", "Density · Comfortable", "Display",
            "spacing roomy relaxed", "density:Comfortable"),
    Command("density_compact", "Density · Compact", "Display",
            "tight tighter spacing", "density:Compact"),
    Command("density_dense", "Density · Dense", "Display",
            "maximum information density traders", "density:Dense"),
    Command("shortcuts", "Show keyboard shortcuts", "Help",
            "keys hotkeys accelerator help", f"dialog:{SHORTCUTS_KEY}"),
)


def _subsequence(query: str, text: str) -> bool:
    """True when every query character appears in order inside ``text``."""
    cursor = iter(text)
    return all(character in cursor for character in query)


def search(query: str, commands: tuple[Command, ...] = COMMANDS) -> list[Command]:
    """Rank commands for a query: substring hits first, then subsequence.

    An empty query lists everything in catalogue order, so the palette is
    useful before the first keystroke. A query that matches nothing returns
    an empty list — the palette never suggests an action the user did not ask
    for.
    """
    needle = (query or "").strip().lower()
    if not needle:
        return list(commands)
    substring: list[Command] = []
    loose: list[Command] = []
    for command in commands:
        haystack = f"{command.label} {command.keywords} {command.kind}".lower()
        if needle in haystack:
            substring.append(command)
        elif _subsequence(needle, haystack):
            loose.append(command)
    return substring + loose


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------

def _execute(action: str) -> None:
    """Apply one command action, then leave the fragment that ran it."""
    kind, _, value = action.partition(":")
    if kind == "route":
        navigation.go(value)
    elif kind == "theme":
        st.session_state["ui_theme"] = value
    elif kind == "density":
        st.session_state["ui_density"] = value
    elif kind == "dialog":
        st.session_state[value] = True
    else:  # pragma: no cover - guarded by the command catalogue
        LOGGER.warning("Unknown command action: %s", action)
        return
    st.rerun()


def open_palette() -> None:
    """Request the palette on the next full run (called from a button)."""
    st.session_state[PALETTE_KEY] = True


def open_shortcuts() -> None:
    """Request the shortcut reference on the next full run."""
    st.session_state[SHORTCUTS_KEY] = True


def consume(request_key: str) -> bool:
    """Pop one open-request; ``True`` means render that dialog this run.

    The request is consumed at render time, so a dialog cannot re-open itself
    on a later unrelated rerun after the user dismissed it.
    """
    try:
        return bool(st.session_state.pop(request_key, False))
    except Exception:  # pragma: no cover - state outside a Streamlit run
        return False


def reset_query() -> None:
    """Clear the palette box before it is shown, so every open starts fresh."""
    try:
        st.session_state[QUERY_KEY] = ""
    except Exception:  # pragma: no cover - state outside a Streamlit run
        pass


# --------------------------------------------------------------------------
# Dialogs
# --------------------------------------------------------------------------

@st.dialog("Command palette", width="large")
def palette() -> None:
    """Fuzzy search over real destinations and display controls."""
    st.text_input("Search anything", key=QUERY_KEY,
                  placeholder="Markets, backtest, toggle theme, density…",
                  label_visibility="collapsed")
    results = search(st.session_state.get(QUERY_KEY, ""))
    st.markdown('<div class="cmdk-title">Commands</div>', unsafe_allow_html=True)
    if not results:
        ui.html_block(ui.info_state(
            "No matching command",
            "Try a destination (markets, portfolio), a feature (backtest, "
            "shortcuts), or a display switch (dark, dense)."))
        return
    for index, command in enumerate(results[:RESULT_LIMIT]):
        marker = ('<div class="cmdk-item cmdk-item--active">'
                  if index == 0 else '<div class="cmdk-item">')
        st.markdown(
            f'{marker}<span>{ui.esc(command.label)}</span>'
            f'<span class="cmdk-kind">{ui.esc(command.kind)}</span></div>',
            unsafe_allow_html=True)
        if st.button(command.label, key=f"ui_cmd_{command.id}",
                     width="stretch"):
            _execute(command.action)
    st.caption("Type to filter · Tab to a result · Esc closes this panel.")


def render_launcher() -> None:
    """Backwards-compatible launcher entry: delegates to the palette."""
    render_palette_launcher()


def render_palette_launcher() -> None:
    """Render the launcher and open the palette only after an explicit request."""
    st.button("Search  (Ctrl+K)", key="ui_palette_launch",
              width="stretch", on_click=open_palette)
    if consume(PALETTE_KEY):
        reset_query()
        palette()


def render_shortcuts_card() -> None:
    """Sidebar card linking to the shortcut help dialog."""
    st.caption("Press Ctrl+K for the command palette, ? for shortcuts.")
    if st.button("Shortcuts", key="sidebar_shortcuts_open",
                 width="stretch"):
        st.session_state[SHORTCUTS_KEY] = True
    render_shortcuts_dialog()


def render_shortcuts_dialog() -> None:
    """Open the shortcuts dialog when requested from any surface."""
    if consume(SHORTCUTS_KEY):
        shortcuts()


@st.dialog("Keyboard shortcuts")
def shortcuts() -> None:
    """The key map, stated exactly as implemented — nothing aspirational."""
    ui.html_block(ui.table(
        ["Keys", "Action"],
        [
            ("Ctrl / ⌘ + K", "Open the command palette"),
            ("?", "Show keyboard shortcuts"),
            ("G then D", "Go to Home"),
            ("G then M", "Go to Markets"),
            ("G then S", "Go to Trade · strategies"),
            ("G then P", "Go to Portfolio"),
            ("G then B", "Go to Research · backtests"),
            ("G then ,", "Go to Settings"),
            ("Esc", "Close the palette"),
            ("Tab / Enter", "Move between results and run the focused one"),
        ],
    ))
    st.caption("Shortcuts are inactive while a text field has focus, so typing "
               "never triggers navigation. The palette also works without any "
               "shortcut: use the Search button in the top bar.")


# --------------------------------------------------------------------------
# Key accelerator (progressive enhancement)
# --------------------------------------------------------------------------

_KEYS_HTML = '<div aria-hidden="true" style="width:0;height:0;overflow:hidden"></div>'
_KEYS_CSS = ""

_KEYS_JS = r"""
// In-page key listener, installed once. It only clicks controls the user
// could click themselves — the Search/Shortcuts buttons and the route
// segmented control — so there is no hidden state path into the app.
function clickLabeled(marker) {
  const buttons = Array.from(document.querySelectorAll('[data-testid="stButton"] button'));
  const match = buttons.find((button) => (button.textContent || "").includes(marker));
  if (match) { match.click(); return true; }
  return false;
}
function clickRoute(name) {
  const groups = document.querySelectorAll('[data-testid="stButtonGroup"]');
  for (const group of groups) {
    const options = Array.from(group.querySelectorAll('[role="radio"], button'));
    const target = options.find((option) => (option.textContent || "").trim() === name);
    if (target) { target.click(); return true; }
  }
  return false;
}
export default function(component) {
  if (window.__ictKeymapInstalled) return () => {};
  window.__ictKeymapInstalled = true;
  let pendingG = 0;
  document.addEventListener("keydown", (event) => {
    const el = event.target;
    const typing = !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" ||
      el.tagName === "SELECT" || el.isContentEditable);
    const key = (event.key || "").toLowerCase();
    if ((event.ctrlKey || event.metaKey) && key === "k") {
      event.preventDefault();
      clickLabeled("Search");
      return;
    }
    if (typing) return;
    if (event.key === "?") {
      event.preventDefault();
      clickLabeled("Shortcuts");
      return;
    }
    if (key === "g") { pendingG = Date.now(); return; }
    if (pendingG && Date.now() - pendingG < 1200) {
      pendingG = 0;
      const routes = { d: "Home", m: "Markets", s: "Trade", p: "Portfolio",
                       b: "Research", ",": "Settings" };
      const route = routes[key];
      if (route) { event.preventDefault(); clickRoute(route); }
    }
  });
  return () => {};
}
"""

_COMPONENTS_BY_RUNTIME: dict[int, object] = {}


def mount_accelerator() -> None:
    """Register the in-page key listener once per Streamlit runtime.

    Any failure degrades silently to button-only operation: the accelerator
    is enhancement, never the only path to a feature.
    """
    try:
        from streamlit.components.v2.get_bidi_component_manager import (
            get_bidi_component_manager,
        )

        manager_id = id(get_bidi_component_manager())
        component = _COMPONENTS_BY_RUNTIME.get(manager_id)
        if component is None:
            component = st.components.v2.component(
                "ui_keyboard_accelerator",
                html=_KEYS_HTML, css=_KEYS_CSS, js=_KEYS_JS,
            )
            _COMPONENTS_BY_RUNTIME[manager_id] = component
        component(key="ui_keyboard_accelerator_instance")
    except Exception:  # pragma: no cover - enhancement only
        LOGGER.debug("Keyboard accelerator unavailable", exc_info=True)
