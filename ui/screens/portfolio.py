"""Portfolio screen.

Value first, then the curve, then positions, then history. The order matters:
a trader opening this screen wants to know what they have and whether it is
growing before they care which trades made it up.
"""

from __future__ import annotations

import streamlit as st

from ui import components as ui
from ui import state
from ui.journal import WINDOWS, records


def render() -> None:
    """Render the portfolio screen."""
    snapshot = state.portfolio()
    trades = state.sorted_trades(snapshot.get("trade_journal") or [])
    starting = float(snapshot.get("starting_capital", 0.0) or 0.0)

    todays = state.trades_today(trades)
    today_pnl = state.todays_pnl(trades) if todays else None
    unrealized = float(snapshot.get("unrealized_pnl", 0.0) or 0.0)
    realized = float(snapshot.get("realized_pnl", 0.0) or 0.0)
    total_pnl = realized + unrealized
    has_account = bool(state.all_accounts())
    equity_value = ui.money(snapshot.get("equity"), signed=False) if has_account else "--"
    balance_value = ui.money(snapshot.get("balance"), signed=False) if has_account else "--"
    total_value = (f'<span class="{ui.tone_class(total_pnl)}">{ui.money(total_pnl)}</span>'
                   if has_account else '<span class="ui-flat">--</span>')

    ui.html_block(ui.grid(4,
        ui.card(ui.stat("Equity", equity_value,
                        "Paper account", size="lg"), variant="accent"),
        ui.card(ui.stat("Available balance", balance_value,
                        "Cash", size="lg")),
        ui.card(ui.stat(
            "Today's P&L",
            ('<span class="ui-flat">--</span>' if today_pnl is None
             else f'<span class="{ui.tone_class(today_pnl)}">{ui.money(today_pnl)}</span>'),
            f"{len(todays)} closed today", size="lg"),
            variant="profit" if (today_pnl or 0) > 0
            else "loss" if (today_pnl or 0) < 0 else ""),
        ui.card(ui.stat(
            "Total P&L",
            total_value,
            (f"Realized {ui.money(realized)} · Unrealized {ui.money(unrealized)}"
             if has_account else "No paper account yet"), size="lg"),
            variant="profit" if has_account and total_pnl > 0
            else "loss" if has_account and total_pnl < 0 else ""),
    ))

    ui.html_block(ui.section_head("Equity curve", "Realized paper equity over time"))
    if not trades:
        ui.html_block(ui.empty_state(
            "No equity curve yet",
            "The curve is drawn from closed paper trades. Run a strategy from the "
            "Trade tab to start building it.",
        ))
    else:
        curve = state.equity_series(trades, starting)
        tone = "pos" if float(realized or 0) >= 0 else "neg"
        ui.html_block(ui.card(
            ui.area_chart([(str(label)[:16], value) for label, value in curve], tone=tone),
            variant="flat",
        ))
        _summary_cards(trades)

    _positions()
    _history(trades)


def _summary_cards(trades: list[dict]) -> None:
    """Realised performance figures from the shared analytics module."""
    stats = state.analytics(trades)
    win_rate = (ui.percent(stats.get("win_rate_pct"), signed=False)
                if stats.get("win_rate_pct") is not None else "--")
    expectancy = (ui.money(stats.get("expectancy"))
                  if stats.get("expectancy") is not None else "--")
    drawdown = (f'<span class="ui-neg">{ui.percent(stats.get("max_drawdown_pct"))}</span>'
                if stats.get("max_drawdown_pct") is not None else "--")
    average_r = (ui.ratio(stats.get("average_r"), suffix="R")
                 if stats.get("average_r") is not None else "--")
    cards = [
        ("Win rate", win_rate, f'{stats.get("total_trades", 0)} closed trades'),
        ("Profit factor", ui.ratio(stats.get("profit_factor")), "Gross win / gross loss"),
        ("Expectancy", expectancy, "Average per trade"),
        ("Max drawdown", drawdown, "Peak to trough"),
        ("Avg R", average_r, "Per trade in R"),
        ("Fees paid", ui.money(stats.get("fees_paid"), signed=False), "Realized cost"),
    ]
    ui.html_block(ui.grid(3, *[ui.card(ui.stat(label, value, sub))
                               for label, value, sub in cards]))


def _positions() -> None:
    """Every open position across all accounts."""
    ui.html_block(ui.section_head("Active positions", "Live paper positions"))
    positions = state.open_positions()
    if not positions:
        ui.html_block(ui.empty_state("No active positions",
                                     "The paper accounts are flat."))
        return
    body = []
    for position in positions:
        body.append((
            ui.esc(position["symbol"]),
            ui.pill(position["side"], "profit" if position["side"] == "LONG" else "loss"),
            ui.esc(position["quantity"]), ui.money(position["entry"], signed=False),
            ui.money(position["price"], signed=False), ui.money(position["stop"], signed=False),
            ui.money(position["target"], signed=False),
            f'<span class="{ui.tone_class(position["pnl"])}">{ui.money(position["pnl"])}</span>',
            ui.status_pill("OPEN", "profit", dot=True),
        ))
    ui.html_block(ui.table(
        ["Symbol", "Side", "Qty", "Entry", "LTP", "SL", "TP", "P&L", "Status"],
        body, align_right=(2, 3, 4, 5, 6, 7)))


def _history(trades: list[dict]) -> None:
    """Closed trades, filtered by window."""
    ui.html_block(ui.section_head("Trade history", f"{len(trades)} closed trades"))
    if not trades:
        ui.html_block(ui.empty_state("No trade history yet",
                                     "Closed paper trades appear here."))
        return
    selected = st.segmented_control("Window", list(WINDOWS), default="All",
                                    key="portfolio_window",
                                    label_visibility="collapsed")
    filtered = state.filter_trades(trades, selected or "All")
    if not filtered:
        ui.html_block(ui.empty_state(
            "No closed trades in this window", f"Widen the filter from {selected}."))
        return
    body = []
    for row in records(filtered):
        result_tone = ("profit" if row["result"] == "Win"
                       else "loss" if row["result"] == "Loss" else "")
        body.append((
            ui.esc(str(row["symbol"])),
            ui.pill(row["direction"],
                    "profit" if row["direction"] == "LONG" else "loss"),
            ui.money(row["entry"], signed=False),
            ui.money(row["trade"].get("exit"), signed=False)
            if row["trade"].get("exit") is not None else "--",
            f'<span class="{ui.tone_class(row["pnl"])}">{ui.money(row["pnl"])}</span>',
            ui.money(row["stop"], signed=False),
            ui.money(row["target"], signed=False),
            ui.pill(row["result"], result_tone),
        ))
    ui.html_block(ui.table(
        ["Symbol", "Side", "Entry", "Exit", "P&L", "SL", "TP", "Result"],
        body, align_right=(2, 3, 4, 5, 6)))