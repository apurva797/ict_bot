"""Trade journal.

Reads the records the paper engine already writes and presents them as a
journal rather than a dataframe dump. Each trade carries the context captured
at entry, which is what makes "why did this trigger?" answerable after the
fact instead of being guesswork.
"""

from __future__ import annotations

import streamlit as st

from ui import components as ui
from ui import state, strategies

WINDOWS = ("Today", "7D", "1M", "3M", "1Y", "All")


def records(trades: list[dict]) -> list[dict]:
    """Enrich each closed trade with the fields the journal displays."""
    rows = []
    for index, trade in enumerate(state.sorted_trades(trades)):
        context = trade.get("journal_context")
        context = context if isinstance(context, dict) else {}
        rows.append({
            "index": index,
            "trade": trade,
            "id": trade.get("trade_id", index + 1),
            "symbol": state.trade_symbol(trade),
            "setup": state.trade_setup(trade),
            "session": state.trade_session(trade),
            "direction": state.trade_direction(trade),
            "entry": trade.get("entry"),
            "stop": trade.get("stop"),
            "target": trade.get("target"),
            "result": state.trade_result(trade),
            "pnl": state.trade_pnl(trade),
            "r": state.trade_r(trade),
            "closed_at": str(trade.get("closed_at") or "--"),
            "strategy": strategies.display_name(str(context.get("strategy_id", ""))),
            "context": state.trade_market_context(trade),
            "exit_reason": str(trade.get("exit_reason") or trade.get("reason") or "--"),
        })
    return rows


def table_markup(rows: list[dict]) -> str:
    """Compact journal table: what happened, and what it produced."""
    body = []
    for row in rows:
        result_tone = ("profit" if row["result"] == "Win"
                       else "loss" if row["result"] == "Loss" else "")
        r_cell = (f'<span class="{ui.tone_class(row["r"])}">'
                  f'{ui.ratio(row["r"], suffix="R")}</span>'
                  if row["r"] is not None else "--")
        body.append((
            ui.esc(str(row["symbol"])),
            ui.pill(row["direction"],
                    "profit" if row["direction"] == "LONG" else "loss"),
            ui.esc(row["session"]),
            ui.esc(row["setup"]),
            ui.pill(row["result"], result_tone),
            f'<span class="{ui.tone_class(row["pnl"])}">{ui.money(row["pnl"])}</span>',
            r_cell,
            ui.esc(row["closed_at"][:16].replace("T", " ")),
        ))
    return ui.table(
        ["Symbol", "Side", "Session", "Setup", "Result", "P&L", "R", "Closed"],
        body, align_right=(5, 6),
    )


def detail_markup(row: dict) -> str:
    """The full record for one trade."""
    result_tone = ("profit" if row["result"] == "Win"
                   else "loss" if row["result"] == "Loss" else "")
    pairs = [
        ("Symbol", ui.esc(row["symbol"])),
        ("Strategy", ui.esc(row["strategy"])),
        ("Session", ui.esc(row["session"])),
        ("Direction", ui.esc(row["direction"])),
        ("Entry", ui.money(row["entry"], signed=False)),
        ("Stop loss", ui.money(row["stop"], signed=False)),
        ("Take profit", ui.money(row["target"], signed=False)),
        ("Exit reason", ui.esc(row["exit_reason"])),
        ("Result", ui.pill(row["result"], result_tone)),
        ("P&L", f'<span class="{ui.tone_class(row["pnl"])}">'
                f'{ui.money(row["pnl"])}</span>'),
        ("R multiple", ui.ratio(row["r"], suffix="R") if row["r"] is not None else "--"),
        ("Closed", ui.esc(row["closed_at"])),
    ]
    return ui.card(
        f'<div class="ui-row"><div style="font-weight:660">'
        f'Trade #{ui.esc(str(row["id"]))} · {ui.esc(row["symbol"])}</div>'
        f'{ui.pill(row["result"], result_tone)}</div>'
        f'<div style="margin-top:.6rem">{ui.rows(pairs)}</div>'
        f'<div style="margin-top:.7rem"><div class="ui-label">Market context at entry</div>'
        f'<div class="ui-sub" style="margin-top:.2rem">{ui.esc(row["context"])}</div></div>',
        variant=result_tone, interactive=True,
    )


def why_triggered(row: dict) -> str:
    """Explain why this trade fired, from the context stored at entry.

    Everything shown was captured when the position opened. When the engine
    recorded no context, that is stated plainly rather than reconstructed.
    """
    context = row["trade"].get("journal_context")
    context = context if isinstance(context, dict) else {}
    lines = []
    if context.get("strategy_version"):
        lines.append(("Strategy version", ui.esc(str(context["strategy_version"]))))
    lines.append(("Setup matched", ui.esc(str(context.get("entry_rules") or row["setup"]))))
    if context.get("data_source"):
        lines.append(("Data source", ui.esc(str(context["data_source"]))))
    lines.append(("Market context", ui.esc(row["context"])))
    lines.append(("Direction", ui.esc(row["direction"])))
    lines.append((
        "Levels used",
        f"Entry {ui.money(row['entry'], signed=False)} · "
        f"SL {ui.money(row['stop'], signed=False)} · "
        f"TP {ui.money(row['target'], signed=False)}",
    ))
    return ui.card(ui.rows(lines), variant="accent")


def why_outcome(row: dict) -> str:
    """Explain the result from the booked numbers and the recorded exit."""
    pnl = row["pnl"]
    if row["exit_reason"] == "TP":
        verdict = "The trade reached its take profit."
    elif row["exit_reason"] == "SL":
        verdict = "The stop loss was hit."
    else:
        verdict = f"The position closed for another reason: {row['exit_reason']}."
    summary = (
        f'<div class="ui-row">'
        f'{ui.status_pill(row["result"], "profit" if pnl > 0 else "loss", dot=True)}'
        f'<span class="{ui.tone_class(pnl)}">{ui.money(pnl)}</span></div>'
        f'<div style="margin-top:.5rem">{ui.esc(verdict)}</div>'
    )
    if row["r"] is not None:
        summary += ('<div class="ui-sub" style="margin-top:.35rem">'
                    f"That is {ui.ratio(row['r'], suffix='R')} against the planned "
                    "1R risk on this trade.</div>")
    return ui.card(summary, variant="profit" if pnl > 0 else "loss")


def render_journal(trades: list[dict], *, key_prefix: str = "journal",
                   window: str = "All", title: str = "Trade journal") -> None:
    """Render the journal with a time filter and a per-trade detail view."""
    ui.html_block(ui.section_head(title, f"{len(trades)} closed trades"))

    if not trades:
        ui.html_block(ui.empty_state(
            "No closed trades yet",
            "Once the paper engine opens and closes a position, it appears here with "
            "the context captured at entry.",
        ))
        return

    selected = st.segmented_control(
        "Window", list(WINDOWS), default=window, key=f"{key_prefix}_window",
        label_visibility="collapsed",
    )
    filtered = state.filter_trades(trades, selected or window)
    if not filtered:
        ui.html_block(ui.empty_state(
            f"No closed trades in this window",
            f"Widen the filter from {selected} to see earlier activity.",
        ))
        return

    rows = records(filtered)
    ui.html_block(table_markup(rows))

    ui.html_block(ui.section_head("Trade detail"))
    if len(rows) == 1:
        _render_detail(rows[0])
        return
    labels = {
        str(index): (f"#{row['id']} · {row['symbol']} · {row['direction']} · "
                     f"{row['result']} {ui.money(row['pnl'])}")
        for index, row in enumerate(rows)
    }
    choice = st.selectbox("Inspect a trade", list(labels),
                          format_func=labels.get, key=f"{key_prefix}_inspect")
    _render_detail(rows[int(choice)])


def _render_detail(row: dict) -> None:
    """One trade: why it triggered, and why it produced this result."""
    left, right = st.columns(2, gap="medium")
    with left:
        ui.html_block(why_triggered(row))
    with right:
        ui.html_block(why_outcome(row))
    st.markdown("")
    ui.html_block(detail_markup(row))