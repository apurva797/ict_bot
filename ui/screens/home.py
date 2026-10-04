"""Home dashboard.

The first screen a returning trader sees. It answers four questions at a
glance: what is my account worth, what did it do today, which strategies are
active, and what is the market doing right now.

Every figure is derived from real session paper state. A strategy that has
never traded shows "--", never "$0.00", so "no data" is never mistaken for
"performing flat".
"""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from ui import components as ui
from ui import marketdata, state, strategies
from ui.strategies import ARJUN_KEY

# The account this screen reports on. The platform has no authentication, so
# the portfolio is one authoritative session account rather than a split that
# would double-count the same capital across strategies.
PRIMARY_ACCOUNT = ("multi.strategy", "BTC/USDT", "5m")
DEFAULT_NAME = "Apurva"


def _greeting(now: datetime) -> str:
    """Time-of-day appropriate greeting, rendered by the component library."""
    hour = now.hour
    period = ("Good morning" if hour < 12 else
              "Good afternoon" if hour < 18 else "Good evening")
    return period


def _account_summary(account: dict | None, trades: list[dict]) -> dict:
    """Value, today's P&L, and cash for one account, plus its risk stats."""
    if account is None:
        return {"exists": False, "equity": None, "today": None, "cash": None,
                "overall": None,
                "open_positions": 0, "win_rate": None, "drawdown": None,
                "trades_today": 0}
    snapshot = state.portfolio_snapshot_for([account])
    todays = state.trades_today(trades)
    analytics = state.analytics(trades)
    balance = float(account.get("balance", 0.0) or 0.0)
    return {
        "exists": True,
        "equity": float(snapshot.get("equity", balance) or balance),
        # None (not 0.0) when nothing closed today, so the header can say so.
        "today": state.todays_pnl(trades) if todays else None,
        "cash": balance,
        "overall": float(snapshot.get("realized_pnl", 0.0) or 0.0)
        + float(snapshot.get("unrealized_pnl", 0.0) or 0.0),
        "open_positions": int(snapshot.get("positions", 0) or 0),
        "win_rate": analytics.get("win_rate_pct"),
        "drawdown": analytics.get("max_drawdown_pct"),
        "trades_today": len(todays),
    }


def render() -> None:
    """Render the Home screen."""
    now = datetime.now(timezone.utc)
    trades = state.journal()
    account = state.account_for(*PRIMARY_ACCOUNT)
    summary = _account_summary(account, trades)

    ui.html_block(ui.greeting(_greeting(now) + f", {DEFAULT_NAME}", now=now))

    # Simulation status is stated once, at the top of the primary screen, so
    # it can never be mistaken for a live-money account.
    ui.html_block(ui.card(
        '<div class="ui-row">'
        f'<div>{ui.status_pill("PAPER TRADING", "warning", live=True)}</div>'
        f'<div>{ui.pill("Live orders disabled", "loss")}</div>'
        "</div>"
        '<div class="ui-sub" style="margin-top:.5rem">'
        "Simulation only. This app holds no broker or exchange credentials. Trades "
        "execute against finalized candles in a session-scoped account."
        "</div>",
        variant="flat",
    ))

    # ---- Portfolio value --------------------------------------------------
    ui.html_block(ui.section_head("Portfolio"))
    equity = (f'<span class="ui-flat">--</span>' if not summary["exists"]
              else ui.money(summary["equity"]))
    today = ('<span class="ui-flat">--</span>' if summary["today"] is None
             else f'<span class="{ui.tone_class(summary["today"])}">'
                  f'{ui.money(summary["today"])}</span>')
    cash = ("<span class=\"ui-flat\">--</span>" if not summary["exists"]
            else ui.money(summary["cash"], signed=False))
    overall = ('<span class="ui-flat">--</span>' if summary["overall"] is None
               else f'<span class="{ui.tone_class(summary["overall"])}">'
                    f'{ui.money(summary["overall"])}</span>')
    today_note = (f'{summary["trades_today"]} closed today'
                  if summary["trades_today"] else "No closed trades today")
    cash_note = "Available to trade" if summary["exists"] else "No paper account yet"

    ui.html_block(ui.grid(4,
        ui.card(ui.stat("Portfolio value", equity, "Paper equity", size="lg"),
                variant="accent"),
        ui.card(ui.stat("Available balance", cash, cash_note, size="lg")),
        ui.card(ui.stat("Today's P&L", today, today_note, size="lg"),
                variant="profit" if (summary["today"] or 0) > 0
                else "loss" if (summary["today"] or 0) < 0 else ""),
        ui.card(ui.stat("Overall P&L", overall, "Realized + unrealized", size="lg"),
            variant="profit" if (summary["overall"] or 0) > 0
            else "loss" if (summary["overall"] or 0) < 0 else ""),
    ))

    _render_market_summary()
    _render_strategy_summary()
    _render_strategies()
    _render_market_watch()
    _render_active_trades()


def _render_market_summary() -> None:
    """Show the selected instrument without implying a live quote."""
    symbol = st.session_state.get("shell_symbol", "BTC/USDT")
    timeframe = st.session_state.get("shell_timeframe", "5m")
    rows = st.session_state.get("watchlist_rows") or {}
    row = rows.get(symbol) or {}
    snapshot = st.session_state.get("chart_snapshot") or {}
    if snapshot.get("symbol") == symbol and snapshot.get("timeframe") == timeframe:
        row = {**marketdata.series_changes(snapshot.get("frame")), "health": "LIVE"}
    price = ui.money(row.get("price"), signed=False)
    change = (f'<span class="{ui.tone_class(row.get("change_pct"))}">'
              f'{ui.percent(row.get("change_pct"))}</span>'
              if row.get("change_pct") is not None else '<span class="ui-flat">--</span>')
    health = row.get("health") or "NOT LOADED"
    tone = "profit" if health == "LIVE" else "warning" if health == "BACKUP" else "info"
    ui.html_block(ui.section_head("Market summary", f"{symbol} · {timeframe}"))
    ui.html_block(ui.card(
        f'<div class="ui-row"><div><div class="ui-label">Selected market</div>'
        f'<div class="ui-value ui-value--sm">{ui.esc(symbol)}</div></div>'
        f'<div><div class="ui-label">LTP</div><div class="ui-value">{price}</div></div>'
        f'<div><div class="ui-label">Change</div><div class="ui-value ui-value--sm">{change}</div></div>'
        f'<div>{ui.status_pill(health, tone, dot=True)}</div></div>',
        variant="flat",
    ))


def _render_strategy_summary() -> None:
    """Summarize only signal and risk values already produced by the engines."""
    choice = st.session_state.get("strategy_choice", "Multi-Strategy Engine")
    analysis = st.session_state.get("ict_analysis") or {}
    if choice == "ARJUNA Strategy":
        signal = analysis.get("side", "NO SIGNAL") if analysis else "NO SIGNAL"
        entry, stop, target = analysis.get("entry"), analysis.get("stop"), analysis.get("target")
    else:
        multi = st.session_state.get("multi_strategy_analysis") or {}
        final = multi.get("final_signal") or {}
        signal = final.get("side", "NO SIGNAL") if choice == "Multi-Strategy Engine" else "NO SIGNAL"
        entry = stop = target = None
    from platform_core.settings import load_settings
    risk = load_settings().risk
    ui.html_block(ui.section_head("Strategy summary", "Current session state"))
    ui.html_block(ui.card(
        ui.rows([
            ("Selected", ui.esc(choice.replace(" Strategy", ""))),
            ("Signal", ui.pill(str(signal), "profit" if signal in {"BUY", "LONG"}
                                else "loss" if signal in {"SELL", "SHORT"} else "")),
            ("Entry / SL / TP", ui.esc(" / ".join(
                ui.money(value, signed=False) if value is not None else "--"
                for value in (entry, stop, target)))),
            ("Risk / minimum R:R", ui.esc(f"{risk.risk_per_trade * 100:.2f}% / {risk.min_rr:g}R")),
            ("Active positions", ui.esc(str(len(state.open_positions())))),
        ]), variant="flat"))


def _render_strategies() -> None:
    """Active strategy cards plus the clearly-labelled roadmap."""
    ui.html_block(ui.section_head(
        "Active strategies", "Paper accounts · live engine state"))

    arjun_account = state.account_for(ARJUN_KEY, *PRIMARY_ACCOUNT[1:])
    arjun_trades = list(arjun_account.get("trades", [])) if arjun_account else []
    arjun = _account_summary(arjun_account, arjun_trades)

    quant_account = state.account_for("quant.trend", *PRIMARY_ACCOUNT[1:])
    quant_trades = list(quant_account.get("trades", [])) if quant_account else []
    quant = _account_summary(quant_account, quant_trades)

    ui.html_block(ui.grid(2,
        ui.strategy_card(
            strategies.ARJUN_DISPLAY_NAME, strategies.ARJUN_SUBTITLE,
            status="Paper active" if arjun_account else "Ready to run",
            status_tone="accent" if arjun_account else "warning",
            pnl=arjun["today"], trades=len(arjun_trades),
            active_positions=arjun["open_positions"], win_rate=arjun["win_rate"],
            risk="Within limits" if arjun_account else "Idle",
        ),
        ui.strategy_card(
            "Quant", "Quant Strategy",
            status="Paper active" if quant_account else "Ready to run",
            status_tone="accent" if quant_account else "warning",
            pnl=quant["today"], trades=len(quant_trades),
            active_positions=quant["open_positions"], win_rate=quant["win_rate"],
            risk="Within limits" if quant_account else "Idle",
        ),
    ))

    with st.expander("More strategies", expanded=False):
        st.caption(
            "This is a multi-strategy platform. The strategies below are planned "
            "but have no signal implementation yet, so they are not selectable and "
            "cannot produce a trade."
        )
        ui.html_block(ui.grid(2, *strategies.roadmap_cards()))


def _render_market_watch() -> None:
    """Watchlist rows with price, change, and sparkline from real candles.

    Loading is an explicit action rather than an automatic fetch. Three
    sequential provider round-trips on first paint would block the whole screen,
    so the rows render a request control until the user asks for data, and the
    result is then cached by :func:`marketdata.load`.
    """
    ui.html_block(ui.section_head("Market watch", "Finalized candles · provider chain"))
    symbols = marketdata.watchlist()
    rows = st.session_state.get("watchlist_rows") or {}
    if not rows:
        ui.html_block(ui.info_state(
            "Market watch is not loaded",
            "Fetching quotes contacts the public provider chain, so it runs when you "
            "ask for it rather than on every page load.",
        ))
        if st.button("Load market watch", key="home_load_watchlist",
                     type="primary", width="content"):
            rows = _fetch_watchlist(symbols)
            st.session_state.watchlist_rows = rows
    else:
        if st.button("Refresh quotes", key="home_refresh_watchlist"):
            rows = _fetch_watchlist(symbols)
            st.session_state.watchlist_rows = rows

    columns = st.columns(len(symbols), gap="small")
    for column, symbol in zip(columns, symbols):
        with column:
            row = rows.get(symbol) or {}
            ui.html_block(ui.quote_row(
                symbol, row.get("price"), row.get("change_pct"),
                row.get("change_abs"), history=row.get("history", ()),
                decimals=marketdata.price_decimals(symbol),
                status=row.get("health", ""),
            ))


def _fetch_watchlist(symbols) -> dict:
    """Load every watchlist instrument, caching the outcome per session.

    A symbol that fails is stored as an explicit unavailable row rather than
    omitted, so a gap in the watchlist is never mistaken for a quiet market.
    """
    collected: dict[str, dict] = {}
    for symbol in symbols:
        result = marketdata.load(symbol, "5m", marketdata.WATCHLIST_CANDLES)
        if not result.ok:
            collected[symbol] = {"price": None, "health": "UNAVAILABLE",
                                 "error": result.error}
            continue
        collected[symbol] = {
            **marketdata.series_changes(result.frame),
            "health": result.health,
        }
    return collected


def _render_active_trades() -> None:
    """Compact cards for every open paper position."""
    ui.html_block(ui.section_head("Active trades", "Open paper positions"))
    positions = state.open_positions()
    if not positions:
        ui.html_block(ui.empty_state(
            "No active trades",
            "Open a paper position from the Trade tab and it will appear here.",
            icon="○",
        ))
        return
    columns = st.columns(min(len(positions), 3), gap="small")
    for column, position in zip(columns, positions):
        with column:
            ui.html_block(ui.position_card(
                position["symbol"], position["side"],
                entry=position["entry"], price=position["price"],
                sl=position["stop"], tp=position["target"],
                quantity=position["quantity"], pnl=position["pnl"],
                r_multiple=position["r_multiple"],
                strategy=strategies.display_name(position["account"]),
                opened_at=position["opened_at"], timeframe=position["timeframe"],
            ))