"""ARJUN AI: research assistant over the user's own trading data.

Three properties define this module, and all three are structural rather than
promised in a caption:

1. Every answer is computed from stored paper trades by :func:`answer`. There is
   no language model in the path, so a number cannot be invented, softened, or
   hallucinated. If the journal is empty, the answer says so.
2. Unknown questions are refused rather than approximated, so the assistant
   cannot appear knowledgeable about data it never inspected.
3. The assistant is strictly read-only. This module imports no execution,
   sizing, or order-placement code, so it has no capability to trade even if
   asked to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import streamlit as st

from ui import components as ui
from ui import state

ASSISTANT_NAME = "ARJUN AI"

SUGGESTIONS = (
    "Why did I lose today?",
    "Which session performed better historically?",
    "Show my worst setups",
    "How did my trades perform this month?",
    "Find recurring losing patterns",
    "Compare my strategies",
)

# A question that matches no known intent is refused. Guessing here would be
# indistinguishable from fabricating a statistic.
REFUSAL = (
    "I can only answer questions about your stored paper trades. I do not have "
    "market forecasts, broker data, or any source other than this session's "
    "trade journal. Try one of the questions above."
)


@dataclass(frozen=True)
class Answer:
    """One grounded response, carrying the sample it was computed from."""

    headline: str
    detail: str
    sample_size: int
    source: str = "session trade journal"


def _today_losses(trades: list[dict]) -> Answer:
    """Why the account is down today, from today's realised trades."""
    todays = state.trades_today(trades)
    if not todays:
        return Answer("No trades closed today",
                      "Nothing was booked today, so there is no daily result to "
                      "explain yet.", 0)
    losses = [trade for trade in todays if state.trade_pnl(trade) < 0]
    total = sum(state.trade_pnl(trade) for trade in todays)
    if not losses:
        return Answer("Today was profitable",
                      f"{len(todays)} closed trade(s) booked {ui.money(total)}. "
                      "There were no losing trades today.", len(todays))
    worst = min(losses, key=state.trade_pnl)
    reason = (f", which exited as {worst.get('exit_reason')}."
              if worst.get("exit_reason") else ".")
    return Answer("Today's losses",
                  f"{len(losses)} of {len(todays)} trades lost, for a net "
                  f"{ui.money(total)}. The largest loss was "
                  f"{state.trade_symbol(worst)} at "
                  f"{ui.money(state.trade_pnl(worst))}{reason}",
                  len(todays))


def _session_comparison(trades: list[dict]) -> Answer:
    """Which trading session has performed better, by realised P&L."""
    buckets: dict[str, list[float]] = {}
    for trade in trades:
        buckets.setdefault(state.trade_session(trade), []).append(
            state.trade_pnl(trade))
    if len(buckets) < 2:
        return Answer("Not enough sessions yet",
                      "Only one trading session appears in the journal, so there is "
                      "nothing to compare against.", len(trades))
    ranked = sorted(buckets.items(), key=lambda item: sum(item[1]), reverse=True)
    parts = " · ".join(f"{name} {ui.money(sum(pnls))} over {len(pnls)} trade(s)"
                       for name, pnls in ranked)
    return Answer(f"{ranked[0][0]} has performed best by total P&L",
                  parts, len(trades))


def _worst_setups(trades: list[dict]) -> Answer:
    """Setups grouped by their recorded entry rule, worst total first."""
    buckets: dict[str, list[float]] = {}
    for trade in trades:
        buckets.setdefault(state.trade_setup(trade), []).append(state.trade_pnl(trade))
    if not buckets:
        return Answer("No setups recorded yet",
                      "Trades opened before the journal stored an entry rule cannot "
                      "be grouped by setup.", 0)
    ranked = sorted(buckets.items(), key=lambda item: sum(item[1]))
    parts = " · ".join(f"{name}: {ui.money(sum(pnls))} over {len(pnls)}"
                       for name, pnls in ranked[:3])
    return Answer("Setups ranked worst first by total P&L", parts, len(trades))


def _period_performance(trades: list[dict], period: str) -> Answer:
    """Realised performance over a named window."""
    window = {"month": "1M", "week": "7D", "today": "Today", "all": "All"}[period]
    selected = state.filter_trades(trades, window)
    if not selected:
        return Answer(f"Nothing in the last {window}",
                      f"No closed trades fall inside the {window} window, so there is "
                      "no performance to report.", 0)
    stats = state.analytics(selected)
    win_rate = (ui.percent(stats["win_rate_pct"], signed=False)
                if stats.get("win_rate_pct") is not None else "--")
    return Answer(
        f"Performance over {window}",
        f"{stats.get('total_trades', 0)} closed trades, net "
        f"{ui.money(stats.get('realized_pnl'))}. Win rate {win_rate}, profit factor "
        f"{ui.ratio(stats.get('profit_factor'))}.",
        len(selected),
    )


def _losing_patterns(trades: list[dict]) -> Answer:
    """Group losing trades by direction, session, and exit reason."""
    losses = [trade for trade in trades if state.trade_pnl(trade) < 0]
    if len(losses) < 2:
        return Answer("Not enough losses to find a pattern",
                      f"Only {len(losses)} losing trade(s) recorded. A pattern needs "
                      "at least two to compare.", len(trades))
    observations = []
    groupings = (
        ("direction", state.trade_direction),
        ("session", state.trade_session),
        ("exit reason", lambda trade: str(trade.get("exit_reason") or "Unknown")),
    )
    for label, getter in groupings:
        buckets: dict[str, list[float]] = {}
        for trade in losses:
            buckets.setdefault(getter(trade), []).append(state.trade_pnl(trade))
        dominant = max(buckets.items(), key=lambda item: len(item[1]))
        observations.append(f"{label}: {dominant[0]} accounted for "
                            f"{len(dominant[1])} of {len(losses)} losses")
    return Answer("Recurring losing patterns",
                  " · ".join(observations) + ". These are counts, not causes.",
                  len(trades))


def _strategy_comparison(trades: list[dict]) -> Answer:
    """Compare recorded strategy versions by realised P&L."""
    buckets: dict[str, list[float]] = {}
    for trade in trades:
        context = trade.get("journal_context")
        context = context if isinstance(context, dict) else {}
        label = (f"{context.get('strategy_id', 'unknown')} "
                 f"v{context.get('strategy_version', '?')}")
        buckets.setdefault(label.strip(), []).append(state.trade_pnl(trade))
    if len(buckets) < 2:
        return Answer("Not enough versions to compare",
                      "Fewer than two distinct strategy versions appear in the "
                      "journal.", len(trades))
    parts = " · ".join(f"{name}: {ui.money(sum(pnls))} over {len(pnls)}"
                       for name, pnls in sorted(buckets.items()))
    return Answer("Strategy comparison", parts, len(trades))


# Intent matching runs most specific first, so "worst setups" is not captured
# by the generic losing-pattern answer.
_INTENTS = (
    (r"\btoday\b.*\b(lose|loss|down)|why did i lose", _today_losses),
    (r"\bsession\b", _session_comparison),
    (r"\bworst\b.*\bsetup|setup.*\bworst\b", _worst_setups),
    (r"\b(compare|version|v1\.)\b", _strategy_comparison),
    (r"\b(pattern|recurring|repeat)\b", _losing_patterns),
    (r"\b(month|week|today|overall|performance|perform)\b",
     lambda trades: _period_performance(trades, "month")),
)


def answer(question: str, trades: list[dict] | None = None) -> Answer:
    """Answer a question about the journal, or refuse.

    Refusing is a first-class outcome: an unmatched question returns
    ``REFUSAL`` rather than an approximation.
    """
    text = (question or "").strip().lower()
    rows = state.journal() if trades is None else trades
    if not rows:
        return Answer("No trade data yet",
                      "This session has no closed paper trades, so there is nothing "
                      "to analyse. Run a strategy from the Trade tab first.", 0)
    for pattern, handler in _INTENTS:
        if re.search(pattern, text):
            return handler(rows)
    return Answer("I cannot answer that", REFUSAL, len(trades))


def render() -> None:
    """Render the assistant panel."""
    ui.html_block(ui.section_head(ASSISTANT_NAME, "Answers from your own trades only"))
    ui.html_block(ui.card(
        f'<div class="ui-row">{ui.status_pill("Read-only", "info", dot=True)}'
        f'{ui.pill("No forecasting", "accent", dot=False)}</div>'
        '<div class="ui-sub" style="margin-top:.4rem">Every figure below is computed '
        "from this session's stored paper trades. I have no market forecasts and no "
        "other data source, and I cannot place trades.</div>",
        variant="flat",
    ))
    for asked, result in reversed(st.session_state.get("assistant_history") or []):
        ui.html_block(_exchange_markup(asked, result))

    question = st.text_input("Ask about your trading", key="assistant_question",
                             placeholder="Why did I lose today?")
    if st.button("Ask ARJUN AI", key="assistant_ask", type="primary"):
        _record(question, answer(question))
    st.caption("Try: " + " · ".join(SUGGESTIONS))


def _record(question: str, result: Answer) -> None:
    """Append an exchange to the bounded session history."""
    history = st.session_state.setdefault("assistant_history", [])
    history.append((question, result))
    del history[:-20]


def _exchange_markup(question: str, result: Answer) -> str:
    """One question and its grounded answer."""
    sample = (f"Based on {result.sample_size} closed trade(s) in the {result.source}."
              if result.sample_size else "No data was available for this question.")
    return ui.card(
        f'<div class="ui-sub">Q: {ui.esc(question)}</div>'
        f'<div style="font-weight:640;margin-top:.35rem">'
        f'{ui.esc(result.headline)}</div>'
        f'<div style="margin-top:.2rem">{ui.esc(result.detail)}</div>'
        f'<div class="ui-sub" style="margin-top:.35rem">{ui.esc(sample)}</div>',
        variant="flat", interactive=True,
    )