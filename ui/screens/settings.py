"""Read-only platform settings surface.

Configuration remains environment-driven and centralized. This screen makes
the active paper safeguards visible without exposing controls that could imply
live execution or silently mutate risk policy during a session.
"""

from __future__ import annotations

import streamlit as st

from platform_core.settings import Settings
from ui import components as ui


def render(settings: Settings) -> None:
    """Render paper configuration and provider/display status."""
    ui.html_block(ui.section_head("Settings", "Environment-backed configuration"))
    ui.html_block(ui.card(
        f'<div class="ui-row">{ui.status_pill("PAPER ONLY", "warning", dot=True)}'
        f'{ui.pill("Live execution disabled", "loss")}</div>'
        '<div class="ui-sub" style="margin-top:.45rem">'
        "Risk rules and provider settings are loaded from the server environment. "
        "No live broker integration is enabled by this application.</div>",
        variant="flat",
    ))

    ui.html_block(ui.section_head("Risk controls", "Applied to every strategy"))
    risk = settings.risk
    ui.html_block(ui.grid(3,
        ui.card(ui.stat("Risk per trade", f"{risk.risk_per_trade * 100:.2f}%")),
        ui.card(ui.stat("Minimum R:R", f"{risk.min_rr:g}R")),
        ui.card(ui.stat("Default target", f"{risk.default_rr:g}R")),
        ui.card(ui.stat("Max leverage", f"{risk.max_leverage:g}x")),
        ui.card(ui.stat("Cooldown", f"{risk.cooldown_minutes} min")),
        ui.card(ui.stat("Max trades / day", str(risk.max_trades_per_day))),
    ))

    ui.html_block(ui.section_head("Market data", "Validated provider chain"))
    ui.html_block(ui.card(ui.rows([
        ("Environment", ui.status_pill(settings.environment, "warning", dot=False)),
        ("Symbols", ui.esc(", ".join(settings.market.symbols))),
        ("Default market", ui.esc(settings.market.default_symbol)),
        ("Default timeframe", ui.esc(settings.market.default_timeframe)),
        ("Fallback policy", ui.esc("Provider fallback is labelled BACKUP, never LIVE")),
    ]), variant="flat"))

    ui.html_block(ui.section_head("Display", "Presentation only"))
    ui.html_block(ui.card(ui.rows([
        ("Currency", ui.esc(settings.display.currency)),
        ("Timezone", ui.esc(settings.display.timezone)),
        ("Theme", ui.esc(settings.display.theme)),
    ]), variant="flat"))