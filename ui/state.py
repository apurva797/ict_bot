"""Session-state access layer.

A thin read model over the paper accounts that already live in
``st.session_state["paper_accounts"]``. It owns no trading logic: sizing,
validation, and fills stay in ``platform_core.risk``, ``platform_core.ict``
and ``demo_paper``. Everything here is derived from real closed trades, so a
missing number always means "no data", never "zero".
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

import pandas as pd
import streamlit as st

from portfolio import portfolio_snapshot
from platform_core.analytics import compute_analytics, equity_curve

ACCOUNTS_KEY = "paper_accounts"

# Minimum observations before a distribution-based statistic is reported.
# Below these the sample cannot support the claim and the value stays None.
MIN_SHARPE_SAMPLES = 20


def accounts() -> dict:
    """The session's paper accounts, keyed ``strategy|symbol|timeframe``."""
    return st.session_state.setdefault(ACCOUNTS_KEY, {})


def account_key(strategy_id: str, symbol: str, timeframe: str) -> str:
    """Stable account key. Shared with the engine's existing convention."""
    return f"{strategy_id}|{symbol}|{timeframe}"


def account_for(strategy_id: str, symbol: str, timeframe: str) -> dict | None:
    """One account by its exact key, or ``None`` when it does not exist yet."""
    return accounts().get(account_key(strategy_id, symbol, timeframe))


def all_accounts() -> list[dict]:
    """Every account in the session, for portfolio-level aggregation."""
    return [state for state in accounts().values() if isinstance(state, dict)]


def get_paper_account(strategy_id: str, starting_capital: float, market: str,
                      interval: str) -> dict:
    """Fetch or create one paper account, preserving the engine's state shape.

    This is the single place accounts are created, so the balance-reset rule
    (changing starting capital clears the account) cannot drift between screens.
    """
    store = accounts()
    state = store.setdefault(account_key(strategy_id, market, interval), {})
    if state.get("starting_capital") != float(starting_capital):
        state.clear()
        state.update({
            "balance": float(starting_capital),
            "starting_capital": float(starting_capital),
            "position": None,
            "trades": [],
            "last_action": None,
            "journal_notes": {},
        })
    state.update({
        "strategy_id": strategy_id,
        "market": market,
        "timeframe": interval,
        "fee_rate": 0.0004,
    })
    return state


# --------------------------------------------------------------------------
# Trade-level readers
# --------------------------------------------------------------------------

def _as_float(value: Any) -> float | None:
    """Best-effort float conversion; ``None`` rather than a fake zero."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def trade_pnl(trade: dict) -> float:
    """Net P&L of one closed trade, tolerating the engine's key aliases."""
    for key in ("net_pnl", "realized_pnl", "pnl"):
        value = _as_float(trade.get(key))
        if value is not None:
            return value
    return 0.0


def trade_r(trade: dict) -> float | None:
    """R multiple of one trade, or ``None`` when risk was never recorded."""
    value = _as_float(trade.get("r_multiple"))
    if value is not None:
        return value
    risk = _as_float(trade.get("risk_amount"))
    return trade_pnl(trade) / risk if risk else None


def _parse_time(value: Any) -> pd.Timestamp | None:
    """Parse a timestamp as UTC, or ``None`` when it is missing or invalid."""
    if value in (None, ""):
        return None
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _closed_at(trade: dict) -> pd.Timestamp | None:
    return _parse_time(trade.get("closed_at") or trade.get("exit_time"))


def _opened_at(trade: dict) -> pd.Timestamp | None:
    return _parse_time(trade.get("opened_at") or trade.get("entry_time"))


# Session windows in UTC, matching the kill zones the engine already names.
_SESSION_RANGES = ((7, 12, "London"), (12, 16, "New York"), (16, 21, "London Close"))


def trade_session(trade: dict) -> str:
    """Trading session label derived from the trade's real timestamps."""
    stamp = _closed_at(trade) or _opened_at(trade)
    if stamp is None:
        return "Unknown"
    for start, end, label in _SESSION_RANGES:
        if start <= stamp.hour < end:
            return label
    return "Asia"


def trade_setup(trade: dict) -> str:
    """Setup label taken from the journal context the engine stored."""
    context = trade.get("journal_context")
    if isinstance(context, dict):
        for key in ("setup", "entry_rules"):
            if context.get(key):
                return _shorten(str(context[key]))
    reason = trade.get("entry_reason") or trade.get("reason")
    return _shorten(str(reason)) if reason else "Unlabelled"


def _shorten(text: str, limit: int = 46) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def trade_market_context(trade: dict) -> str:
    """Market context captured when the trade was opened."""
    context = trade.get("journal_context")
    if isinstance(context, dict) and context.get("market_context"):
        return str(context["market_context"])
    return "Not recorded"


def trade_symbol(trade: dict) -> str:
    """Symbol from the journal context, falling back to a stored market."""
    context = trade.get("journal_context")
    if isinstance(context, dict) and context.get("market"):
        return str(context["market"])
    return str(trade.get("market") or trade.get("symbol") or "--")


def trade_direction(trade: dict) -> str:
    """Trade side, normalised to upper case."""
    return str(trade.get("side") or "--").upper()


def trade_result(trade: dict) -> str:
    """Win / Loss / Break-even from booked P&L, not from the exit label."""
    pnl = trade_pnl(trade)
    if pnl > 0:
        return "Win"
    return "Loss" if pnl < 0 else "Break-even"


# --------------------------------------------------------------------------
# Portfolio aggregation
# --------------------------------------------------------------------------

def portfolio() -> dict:
    """Aggregate every session account into one portfolio view.

    Delegates to the existing :func:`portfolio.portfolio_snapshot` so the
    arithmetic stays in one tested place.
    """
    return portfolio_snapshot(all_accounts())


def journal() -> list[dict]:
    """Every closed trade in the session, newest first."""
    return sorted_trades(portfolio().get("trade_journal") or [])


def todays_pnl(trades: Sequence[dict], *, now: datetime | None = None) -> float:
    """Realized P&L booked today in UTC.

    Use :func:`trades_today` to tell "flat so far" apart from "no trades yet";
    this function returns ``0.0`` for both.
    """
    return sum(trade_pnl(trade) for trade in trades_today(trades, now=now))


def trades_today(trades: Sequence[dict], *, now: datetime | None = None) -> list[dict]:
    """Closed trades from today (UTC)."""
    moment = now or datetime.now(timezone.utc)
    return [trade for trade in trades
            if (stamp := _closed_at(trade)) is not None and stamp.date() == moment.date()]


def open_positions() -> list[dict]:
    """Every account holding a position, annotated with its account key.

    The current price is the account's own ``last_mark``, which the paper
    engine sets from a real candle. When no mark exists the position is
    reported without a price rather than priced at entry.
    """
    rows = []
    for key, state in accounts().items():
        if not isinstance(state, dict):
            continue
        position = state.get("position")
        if not position:
            continue
        parts = key.split("|")
        entry = _as_float(position.get("entry"))
        quantity = _as_float(position.get("quantity"))
        price = _as_float(state.get("last_mark"))
        pnl = None
        r_multiple = None
        if None not in (price, entry, quantity):
            direction = 1.0 if str(position.get("side")).upper() == "LONG" else -1.0
            pnl = (price - entry) * quantity * direction
            risk = _as_float(position.get("risk_amount"))
            r_multiple = pnl / risk if risk else None
        rows.append({
            "key": key,
            "account": parts[0] if parts else "",
            "symbol": parts[1] if len(parts) > 1 else str(state.get("market", "--")),
            "timeframe": parts[2] if len(parts) > 2 else str(state.get("timeframe", "")),
            "side": str(position.get("side", "--")).upper(),
            "entry": entry,
            "stop": _as_float(position.get("stop")),
            "target": _as_float(position.get("target")),
            "quantity": quantity,
            "price": price,
            "pnl": pnl,
            "r_multiple": r_multiple,
            "opened_at": str(position.get("opened_at", "")),
            "risk_amount": _as_float(position.get("risk_amount")),
        })
    return rows


def analytics(trades: Sequence[dict]) -> dict:
    """Descriptive statistics from the shared analytics module."""
    return compute_analytics([trade for trade in trades if isinstance(trade, dict)])


def portfolio_snapshot_for(selected: Sequence[dict]) -> dict:
    """Portfolio snapshot for a chosen subset of accounts.

    Lets a screen report on one authoritative account without re-implementing
    the unrealised-P&L and fee arithmetic in :mod:`portfolio`.
    """
    return portfolio_snapshot([state for state in selected if isinstance(state, dict)])


def equity_series(trades: Sequence[dict], starting_balance: float) -> list[tuple[str, float]]:
    """Equity curve for charting."""
    return [(str(point.get("closed_at") or ""), float(point.get("equity")))
            for point in equity_curve([t for t in trades if isinstance(t, dict)],
                                      starting_balance)]


def drawdown_series(trades: Sequence[dict]) -> list[tuple[str, float]]:
    """Drawdown curve for charting, in the same currency as the equity curve."""
    return [(str(point.get("closed_at") or ""), float(point.get("drawdown") or 0.0))
            for point in equity_curve([t for t in trades if isinstance(t, dict)])]


def daily_pnl(trades: Sequence[dict]) -> list[tuple[str, float]]:
    """Realized P&L grouped by UTC close date, chronologically."""
    buckets: dict[str, float] = {}
    for trade in trades:
        stamp = _closed_at(trade)
        if stamp is None:
            continue
        key = str(stamp.date())
        buckets[key] = buckets.get(key, 0.0) + trade_pnl(trade)
    return sorted(buckets.items())


def monthly_returns(trades: Sequence[dict], starting_balance: float) -> list[tuple[str, float]]:
    """Percentage return per calendar month, relative to the running equity.

    Compounding uses the real running balance, so each month is measured
    against the equity actually available at the start of that month.
    """
    buckets: dict[str, float] = {}
    for trade in trades:
        stamp = _closed_at(trade)
        if stamp is None:
            continue
        key = str(stamp.date())[:7]
        buckets[key] = buckets.get(key, 0.0) + trade_pnl(trade)
    if not buckets or starting_balance <= 0:
        return []
    running = float(starting_balance)
    returns = []
    for label, pnl in sorted(buckets.items()):
        base = running or 1.0
        returns.append((label, pnl / base * 100))
        running += pnl
    return returns


def filter_trades(trades: Sequence[dict], window: str,
                  *, now: datetime | None = None) -> list[dict]:
    """Restrict the journal to a named time window.

    ``All`` returns everything; every other option filters on the real UTC
    close timestamp, so a trade with no usable timestamp is excluded rather
    than guessed into a window it may not belong to.
    """
    rows = [trade for trade in trades if isinstance(trade, dict)]
    if window == "All":
        return rows
    moment = now or datetime.now(timezone.utc)
    limits = {"Today": 0, "7D": 7, "1M": 31, "3M": 93, "1Y": 366}
    days = limits.get(window)
    if days is None:
        return rows
    if days == 0:
        return trades_today(rows, now=moment)
    cutoff = moment - timedelta(days=days)
    selected = []
    for trade in rows:
        stamp = _closed_at(trade)
        if stamp is not None and stamp.to_pydatetime() >= cutoff:
            selected.append(trade)
    return selected


def sorted_trades(trades: Iterable[dict], *, newest_first: bool = True) -> list[dict]:
    """Chronological ordering, with undated rows placed last in both modes."""
    rows = [trade for trade in trades if isinstance(trade, dict)]
    return sorted(
        rows,
        key=lambda trade: (_closed_at(trade) or pd.Timestamp.min.tz_localize("UTC")),
        reverse=newest_first,
    )