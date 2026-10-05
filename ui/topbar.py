"""Terminal top bar: product identity, market status, and data freshness.

Everything shown here is factual: the configured market, the session windows
the platform already documents (UTC kill-zone hours from settings), whether
the 24/7 crypto market can trade, the simulation mode, and the age of the
last loaded candle snapshot. No indicator here is decorative or invented.
"""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from platform_core.settings import Settings
from ui import components as ui

# Age thresholds for the freshness pill. They describe the snapshot on
# screen, not the market: older than this and the label says so plainly.
FRESH_SECONDS = 180
STALE_SECONDS = 900


def _market_pills() -> str:
    """Selected instrument and interval, straight from the shell widgets."""
    symbol = st.session_state.get("shell_symbol", "BTC/USDT")
    timeframe = st.session_state.get("shell_timeframe", "5m")
    return (ui.pill(f"{symbol} · {timeframe}", "accent")
            + ui.pill("Crypto · 24/7", "info"))


def _session_pills(settings: Settings) -> str:
    """London / New York windows with their real active state (UTC)."""
    now = datetime.now(timezone.utc)
    hour = now.hour + now.minute / 60.0

    def window(label: str, bounds: tuple) -> str:
        try:
            start, end = float(bounds[0]), float(bounds[1])
        except (TypeError, ValueError, IndexError):
            return ""
        active = start <= hour < end
        span = f"{int(start):02d}–{int(end):02d} UTC"
        text = f"{label} {span} · active" if active else f"{label} {span}"
        return ui.pill(text, "accent" if active else "")

    parts = [window("London", settings.london_hours),
             window("New York", settings.new_york_hours)]
    return "".join(part for part in parts if part)


def _freshness_pill() -> str:
    """Age of the loaded candle snapshot — never a fabricated live quote."""
    snapshot = st.session_state.get("chart_snapshot") or {}
    loaded_at = snapshot.get("loaded_at")
    if not loaded_at:
        return ui.status_pill("Data not loaded", "warning", dot=True)
    try:
        moment = datetime.fromisoformat(str(loaded_at))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        age = max(0.0, (datetime.now(timezone.utc) - moment).total_seconds())
    except ValueError:
        return ui.status_pill("Data not loaded", "warning", dot=True)
    if age < 60:
        text = f"Data updated {int(age)}s ago"
    elif age < STALE_SECONDS:
        text = f"Data updated {int(age // 60)}m ago"
    else:
        text = f"Snapshot from {int(age // 60)}m ago"
    tone = "profit" if age < FRESH_SECONDS else "warning"
    return ui.status_pill(text, tone, dot=True)


def render(settings: Settings) -> None:
    """Render the top bar above navigation."""
    ui.html_block(
        '<div class="ui-shell-topbar">'
        '<div class="ui-shell-identity">'
        '<div class="ui-shell-mark" aria-hidden="true">A</div>'
        "<div><div class=\"ui-shell-name\">ARJUN Trading Platform</div>"
        '<div class="ui-shell-tagline">Multi-strategy terminal · session only</div>'
        "</div></div>"
        f'<div class="ui-shell-center">{_market_pills()}{_session_pills(settings)}</div>'
        f'<div class="ui-shell-actions">{_freshness_pill()}'
        f'{ui.status_pill("PAPER · simulation", "warning", dot=True)}</div>'
        "</div>"
    )
