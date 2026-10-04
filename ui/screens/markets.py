"""Markets screen.

The full watchlist with a detail view. Prices come from the same validated
provider chain the strategies use, so a price shown here is the same price
the engine would act on. An unavailable instrument is labelled as such rather
than omitted, so a gap in the watchlist is never mistaken for a quiet market.
"""

from __future__ import annotations

import streamlit as st

from ui import components as ui
from ui import marketdata


def render(symbol: str, timeframe: str) -> None:
    """Render the Markets screen for the currently selected market."""
    ui.html_block(ui.section_head("Market watch", "Finalized candles · validated providers"))

    rows = st.session_state.get("watchlist_rows") or {}
    refresh = st.button("Refresh quotes", key="markets_refresh", width="content")
    if refresh or not rows:
        rows = _fetch_watchlist()
        st.session_state.watchlist_rows = rows

    symbols = marketdata.watchlist()
    columns = st.columns(min(len(symbols), 3), gap="small")
    for column, name in zip(columns, symbols):
        with column:
            _quote_card(name, rows.get(name) or {}, selected=name == symbol)

    ui.html_block(ui.section_head("Instrument detail", f"{symbol} · {timeframe}"))
    result = marketdata.load(symbol, timeframe, marketdata.CHART_CANDLES)
    if not result.ok:
        # A failed provider chain is a first-class state, not a blank panel.
        ui.html_block(ui.error_state(
            "Market data unavailable",
            f"{result.error} The platform could not reach any provider for "
            f"{symbol} · {timeframe}.",
        ))
        return
    changes = marketdata.series_changes(result.frame, bars=40)
    ui.html_block(ui.grid(4,
        ui.card(ui.stat("Last price",
                        ui.money(changes["price"], signed=False), "Finalized close")),
        ui.card(ui.stat("Change",
                        f'<span class="{ui.tone_class(changes["change_pct"])}">'
                        f'{ui.percent(changes["change_pct"])}</span>',
                        "Versus prior close")),
        ui.card(ui.stat("Candles loaded", ui.esc(str(len(result.frame))),
                        f"Up to {marketdata.CHART_CANDLES}")),
        ui.card(ui.stat("Data source", ui.esc(result.source[:24]), result.health,
                        size="sm")),
    ))
    ui.html_block(ui.card(
        ui.area_chart([
            (str(stamp)[:16].replace("T", " "), float(value))
            for stamp, value in result.frame["close"].tail(80).items()
        ], tone="accent"),
        variant="flat",
    ))


def _fetch_watchlist() -> dict:
    """Load every watchlist instrument into a session cache.

    Failures are stored as explicit unavailable rows so a gap in the watchlist
    is never mistaken for a quiet market.
    """
    collected: dict[str, dict] = {}
    for symbol in marketdata.watchlist():
        result = marketdata.load(symbol, "5m", marketdata.WATCHLIST_CANDLES)
        if not result.ok:
            collected[symbol] = {"price": None, "health": "UNAVAILABLE",
                                 "error": result.error}
            continue
        collected[symbol] = {**marketdata.series_changes(result.frame),
                             "health": result.health}
    return collected


def _quote_card(name: str, row: dict, *, selected: bool) -> None:
    """One instrument card, matching the Home watchlist styling."""
    ui.html_block(ui.quote_row(
        name, row.get("price"), row.get("change_pct"), row.get("change_abs"),
        history=row.get("history", ()), decimals=marketdata.price_decimals(name),
        status=row.get("health", ""),
        status_tone="info" if row.get("health") == "LIVE"
        else "warning" if row.get("health") == "BACKUP" else "loss",
    ))
    if selected:
        st.caption("Selected for the chart")