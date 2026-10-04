"""Trade screen.

Chart-first, because that is where a trading decision is actually made. The
order panel and the position controls sit directly beneath the chart rather
than on a separate screen, so the levels a user is looking at and the levels
they are trading are never more than one scroll apart.

Trading actions call the same engines the terminal bot uses. Nothing here
invents a signal, sizes a position, or approves a trade.
"""

from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

from charting import render_ohlcv_chart
from charting_overlays import build_ict_overlay, build_overlay_series
from demo_data import MarketDataError
from demo_paper import advance_ict_paper_account, monitor_paper_position
from platform_core.errors import PlatformError
from platform_core.ict import analyze_ict
from platform_core.market_data import assess_health
from platform_ui import (render_data_health, render_ict_overlay_toggles,
                         render_indicator_controls, render_ict_stages, render_status)
from ui import components as ui
from ui import marketdata, orderpanel, state, strategies

LOGGER = logging.getLogger("ui.screens.trade")

STRATEGY_LABELS = ("Multi-Strategy Engine", "AI Strategy", "ARJUNA Strategy",
                   "Quant Strategy")

# Maps the radio label the app has always exposed onto the internal strategy
# key, so existing session keys and journal records keep resolving.
LABEL_TO_KEY = {
    "ARJUNA Strategy": strategies.ARJUN_KEY,
    "Quant Strategy": "quant.trend",
    "AI Strategy": "custom.dsl",
    "Multi-Strategy Engine": "multi.strategy",
}


def render(symbol: str, timeframe: str, starting_capital: float) -> str:
    """Render the Trade screen and return the selected strategy label.

    The strategy radio is the first radio in the app, which the existing
    end-to-end tests rely on, so it is rendered here in a stable position.
    """
    symbol = st.session_state.get("terminal_watchlist", symbol)
    symbol, timeframe = _market_controls(symbol, timeframe)
    ui.html_block(_terminal_header(symbol, timeframe))
    _paper_banner()

    # Reserve the chart before strategy actions run. Actions publish a snapshot
    # during the same rerun, so the chart remains the stable dominant workspace.
    watchlist_col, chart_col, panel_col = st.columns([0.85, 2.2, 1.15], gap="small")
    with watchlist_col:
        _render_terminal_watchlist(symbol)
    chart_slot = chart_col.empty()
    with panel_col:
        ui.html_block(ui.section_head("Trade panel", "Signal · risk · execution"))
        strategy_choice = st.radio(
            "Strategy", list(STRATEGY_LABELS), horizontal=False, key="strategy_choice",
        )
        st.session_state.setdefault("quant_seen", strategy_choice == "Quant Strategy")
        _render_strategy_workspace(strategy_choice, symbol, timeframe, starting_capital)
        _render_side_panel(strategy_choice, symbol, timeframe, starting_capital)

    with chart_slot.container():
        ui.html_block(ui.section_head("Price chart", f"{symbol} · {timeframe} · finalized candles"))
        _render_chart(symbol, timeframe)

    _render_bottom_terminal(symbol, timeframe)
    return strategy_choice


def _terminal_header(symbol: str, timeframe: str) -> str:
    """Compact price strip above the three-column terminal."""
    snapshot = st.session_state.get("chart_snapshot") or {}
    price = None
    change = None
    if snapshot.get("symbol") == symbol and snapshot.get("timeframe") == timeframe:
        changes = marketdata.series_changes(snapshot.get("frame"))
        price, change = changes.get("price"), changes.get("change_pct")
    tone = ui.tone_class(change)
    price_html = ui.money(price, signed=False) if price is not None else "--"
    change_html = (f'<span class="{tone}">{ui.percent(change)}</span>'
                   if change is not None else '<span class="ui-flat">--</span>')
    return (
        f'<div class="terminal-header"><div><div class="ui-label">Selected market</div>'
        f'<div class="terminal-symbol">{ui.esc(symbol)}</div></div>'
        f'<div><div class="ui-label">LTP</div><div class="terminal-price">{price_html}</div></div>'
        f'<div><div class="ui-label">Change</div><div class="terminal-change">{change_html}</div></div>'
        f'<div><div class="ui-label">Interval</div><div class="terminal-value">{ui.esc(timeframe)}</div></div>'
        f'<div class="terminal-market-status">{ui.status_pill("PAPER", "warning", dot=True)}</div></div>'
    )


def _render_terminal_watchlist(selected: str) -> None:
    """Compact watchlist; quotes load explicitly and never fabricate prices."""
    ui.html_block(ui.section_head("Watchlist", "LTP · change"))
    symbols = marketdata.watchlist()
    rows = st.session_state.get("watchlist_rows") or {}
    selected = st.radio("Watchlist symbol", list(symbols), index=list(symbols).index(selected)
                        if selected in symbols else 0, key="terminal_watchlist",
                        label_visibility="collapsed")
    if not rows:
        ui.html_block(ui.info_state("Quotes not loaded", "Load the watchlist to compare markets."))
        if st.button("Load watchlist", key="terminal_load_watchlist", width="stretch"):
            st.session_state.watchlist_rows = _fetch_watchlist_rows(symbols)
            st.rerun()
        return
    for name in symbols:
        row = rows.get(name) or {}
        tone = ui.tone_class(row.get("change_pct"))
        state_label = "Selected" if name == selected else ""
        ui.html_block(
            f'<div class="terminal-watch-row{" terminal-watch-row--selected" if name == selected else ""}">'
            f'<div><strong>{ui.esc(name)}</strong><div class="ui-sub">{ui.esc(state_label)}</div></div>'
            f'<div class="terminal-watch-price">{ui.money(row.get("price"), signed=False)}'
            f'<span class="{tone}">{ui.percent(row.get("change_pct"))}</span></div></div>'
        )


def _fetch_watchlist_rows(symbols) -> dict:
    """Fetch the same validated quote rows used by the Markets screen."""
    collected = {}
    for name in symbols:
        result = marketdata.load(name, "5m", marketdata.WATCHLIST_CANDLES)
        collected[name] = (marketdata.series_changes(result.frame)
                           | {"health": result.health}) if result.ok else {
                               "price": None, "change_pct": None,
                               "change_abs": None, "history": (),
                               "health": "UNAVAILABLE"}
    return collected


def _render_bottom_terminal(symbol: str, timeframe: str) -> None:
    """Keep account state one compact tab away from the decision workspace."""
    st.markdown("<div class='terminal-bottom'>", unsafe_allow_html=True)
    position_tab, orders_tab, history_tab, pnl_tab = st.tabs(
        ["Positions", "Orders", "Trade History", "P&L"])
    with position_tab:
        positions = state.open_positions()
        rows = []
        for position in positions:
            rows.append((
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
            rows, align_right=(2, 3, 4, 5, 6, 7)))
    with orders_tab:
        orders = st.session_state.get("paper_orders") or []
        rows = [(
            ui.esc(order.get("time", "--")), ui.esc(order.get("symbol", symbol)),
            ui.pill(order.get("side", "--"), "profit" if order.get("side") == "BUY" else "loss"),
            ui.esc(order.get("quantity", "--")), ui.money(order.get("price"), signed=False),
            ui.status_pill(order.get("status", "PAPER"), "info", dot=False),
            ui.esc(order.get("strategy", "--")),
        ) for order in orders]
        ui.html_block(ui.table(
            ["Time", "Symbol", "Side", "Qty", "Price", "Status", "Strategy"], rows,
            align_right=(3, 4)))
    with history_tab:
        trades = state.journal()
        rows = [(
            ui.esc(state.trade_symbol(trade)),
            ui.pill(state.trade_direction(trade), "profit" if state.trade_pnl(trade) >= 0 else "loss"),
            ui.esc(str(trade.get("closed_at") or trade.get("exit_time") or "--")),
            f'<span class="{ui.tone_class(state.trade_pnl(trade))}">{ui.money(state.trade_pnl(trade))}</span>',
            ui.pill(state.trade_result(trade), "profit" if state.trade_pnl(trade) > 0 else "loss"),
        ) for trade in trades[:25]]
        ui.html_block(ui.table(["Symbol", "Side", "Closed", "P&L", "Result"], rows,
                               align_right=(3,)))
    with pnl_tab:
        snapshot = state.portfolio()
        analytics = state.analytics(state.journal())
        ui.html_block(ui.grid(4,
            ui.stat("Equity", ui.money(snapshot.get("equity"), signed=False)),
            ui.stat("Realized P&L", f'<span class="{ui.tone_class(snapshot.get("realized_pnl"))}">{ui.money(snapshot.get("realized_pnl"))}</span>'),
            ui.stat("Unrealized P&L", f'<span class="{ui.tone_class(snapshot.get("unrealized_pnl"))}">{ui.money(snapshot.get("unrealized_pnl"))}</span>'),
            ui.stat("Win rate", ui.percent(analytics.get("win_rate_pct"), signed=False)
                    if analytics.get("win_rate_pct") is not None else "--"),
        ))
    st.markdown("</div>", unsafe_allow_html=True)


def _market_controls(symbol: str, timeframe: str) -> tuple[str, str]:
    """Market and timeframe pickers, plus an explicit load action.

    Loading is an explicit action rather than an automatic fetch so a data
    request never fires merely because a user opened the screen.
    """
    first, second = st.columns([1, 1], gap="small")
    with first:
        timeframe = st.selectbox("Candle interval", list(marketdata.TIMEFRAMES),
                                 key="timeframe_select")
    with second:
        st.markdown("")
        st.markdown("")
        if st.button("Load chart", key="load_chart", type="primary", width="stretch"):
            result = marketdata.load(symbol, timeframe, marketdata.CHART_CANDLES)
            marketdata.publish_chart_snapshot(result)
            st.session_state.market_load = {
                "ok": result.ok, "source": result.source, "error": result.error,
                "used_fallback": result.used_fallback,
            }
    return symbol, timeframe


def _paper_banner() -> None:
    """Restate simulation status where execution intent actually lives.

    Every control on this screen can only place paper orders, so the badge
    belongs directly above them instead of only on the Home screen where the
    account is summarised.
    """
    ui.html_block(ui.card(
        '<div class="ui-row">'
        f'<div>{ui.status_pill("PAPER TRADING", "warning", live=True)}</div>'
        f'<div>{ui.pill("Live orders disabled", "loss")}</div>'
        "</div>"
        '<div class="ui-sub" style="margin-top:.5rem">'
        "Simulation only · no broker or exchange credentials. Signals use "
        "finalized candles and fills occur at the next candle open."
        "</div>",
        variant="flat",
    ))


def _render_chart(symbol: str, timeframe: str) -> None:
    """Mount the chart in one stable slot from the last loaded snapshot.

    The slot is stable across strategy switches on purpose: remounting the
    component on every panel change would reset zoom and lose the viewport.
    """
    snapshot = st.session_state.get("chart_snapshot")
    if not snapshot:
        ui.html_block(ui.info_state(
            "Waiting for market data",
            "Choose a market and press Load chart. The chart renders candles from "
            "this app's validated provider chain.",
        ))
        return

    loaded_symbol = snapshot["symbol"]
    loaded_timeframe = snapshot["timeframe"]
    frame = snapshot["frame"]
    source = snapshot["source"]

    if (loaded_symbol, loaded_timeframe) != (symbol, timeframe):
        st.caption(f"Showing the last loaded market ({loaded_symbol} · "
                   f"{loaded_timeframe}). Press Load chart to switch.")
    st.caption(f"Data source: {source} · {len(frame)} finalized candles · UTC")
    if str(source).startswith("Bundled"):
        st.info("Using backup data source")

    if "chart_fit_request" not in st.session_state:
        st.session_state.chart_fit_request = 0
    if st.button("Fit chart", key="fit_market_chart"):
        st.session_state.chart_fit_request += 1

    # Overlays are computed from these exact candles, so every line and level
    # on the chart traces back to real market data.
    overlays = build_overlay_series(frame, render_indicator_controls())
    ict_overlay = build_ict_overlay(
        st.session_state.get("ict_analysis"), render_ict_overlay_toggles())
    chart_live = not str(source).startswith("Bundled")
    render_data_health(assess_health(frame, loaded_timeframe, is_live=chart_live)[0],
                       source, is_live=chart_live, candles=len(frame))
    render_ohlcv_chart(
        frame,
        title=f"{loaded_symbol} · {loaded_timeframe}",
        dataset_id=f"{loaded_symbol}|{loaded_timeframe}",
        reset_id=st.session_state.chart_fit_request,
        overlays=overlays,
        ict=ict_overlay,
    )


def _render_strategy_workspace(choice: str, symbol: str, timeframe: str,
                              starting_capital: float) -> None:
    """Dispatch to the workspace for the selected strategy."""
    ui.html_block(ui.section_head(
        f"{strategies.display_name(LABEL_TO_KEY.get(choice, 'multi.strategy'))}",
        "Strategy workspace"))
    if choice == "ARJUNA Strategy":
        _render_arjun(symbol, timeframe, starting_capital)
    elif choice == "Quant Strategy":
        _render_quant(symbol, timeframe, starting_capital)
    elif choice == "AI Strategy":
        from ui.screens.builder import render as render_builder
        render_builder(symbol, timeframe, starting_capital)
    else:
        _render_multi_strategy(symbol, timeframe, starting_capital)


def _arjun_metrics(analysis: dict | None, account: dict | None) -> list[str]:
    """Headline cards for the Arjun screen, from real analysis and trades."""
    trades = list(account.get("trades", [])) if account else []
    stats = state.analytics(trades)
    todays = state.trades_today(trades)
    from config import RISK_PER_TRADE
    from platform_core.settings import load_settings
    risk = load_settings().risk
    drawdown = stats.get("max_drawdown_pct")
    today_pnl = state.todays_pnl(trades) if todays else None
    cards = [
        ui.card(ui.stat("Today's P&L",
                        ('<span class="ui-flat">--</span>' if today_pnl is None
                         else f'<span class="{ui.tone_class(today_pnl)}">'
                              f'{ui.money(today_pnl)}</span>'),
                        f"{len(todays)} trades today")),
        ui.card(ui.stat("Win rate",
                        (f'{ui.percent(stats["win_rate_pct"], signed=False)}%'
                         if stats.get("win_rate_pct") is not None
                         else '<span class="ui-flat">--</span>'),
                        f'{stats.get("total_trades", 0)} closed trades')),
        ui.card(ui.stat("Trades", ui.esc(str(stats.get("total_trades", 0))),
                        "Closed in this session")),
        ui.card(ui.stat("Current drawdown",
                        ('<span class="ui-flat">--</span>' if drawdown is None
                         else f'<span class="ui-neg">{ui.percent(drawdown)}</span>'),
                        "Peak to trough")),
        ui.card(ui.stat("Risk per trade", f'{risk.risk_per_trade * 100:.2f}%',
                        f"Minimum {risk.min_rr:g}R · {risk.max_leverage:g}x max")),
    ]
    return cards


def _render_arjun(symbol: str, timeframe: str, starting_capital: float) -> None:
    """ARJUN workspace: status header, decision chain, and paper controls."""
    from config import NEWS_BLACKOUT, NEWS_FILTER_ENABLED

    account = state.account_for(strategies.ARJUN_KEY, symbol, timeframe)
    ui.html_block(ui.grid(3,
        ui.card(
            f'<div class="ui-label">Arjun</div>'
            f'<div class="ui-value ui-value--sm" style="margin-top:.15rem">ICT Strategy</div>'
            '<div class="ui-sub">Internal strategy key <code>ict</code></div>',
            variant="accent",
        ),
        ui.card(
            f'<div class="ui-label">Status</div>'
            f'<div style="margin-top:.35rem">'
            f'{ui.status_pill("Paper Trading Active", "profit", live=True)}</div>'
            '<div class="ui-sub" style="margin-top:.4rem">'
            "Evaluated 24/7. Risk limits and cooldown always apply.</div>",
        ),
        ui.card(
            f'<div class="ui-label">Engine</div>'
            f'<div class="ui-sub" style="margin-top:.3rem">'
            f"Scored with the shared bot pipeline.<br>"
            f"News blackout: {'active' if (NEWS_FILTER_ENABLED and NEWS_BLACKOUT) else 'off'}"
            "</div>",
        ),
    ))

    ui.html_block(ui.section_head("Performance"))
    ui.html_block(ui.grid(3, *_arjun_metrics(
        st.session_state.get("ict_analysis"), account)))

    actions = st.columns([1, 1, 1], gap="small")
    run_signal = actions[0].button("Check latest ARJUNA signal",
                                   key="arjun_check_signal", type="primary",
                                   width="stretch")
    backtest = actions[1].button("Backtest ARJUNA", key="arjun_backtest",
                                 width="stretch")
    paper = actions[2].button("Run ARJUNA paper trading", key="arjun_paper",
                              width="stretch")

    if not (run_signal or backtest or paper):
        return

    result = marketdata.load(symbol, timeframe,
                             marketdata.BACKTEST_CANDLES if backtest
                             else marketdata.ANALYSIS_CANDLES)
    if not result.ok:
        ui.html_block(ui.error_state("Market data unavailable", result.error))
        return
    st.caption(f"Data source: {result.source} · {len(result.frame)} finalized candles · UTC")
    marketdata.publish_chart_snapshot(result)
    data = result.frame
    blackout = bool(NEWS_FILTER_ENABLED and NEWS_BLACKOUT)

    if run_signal:
        _arjun_signal(data, symbol, timeframe, blackout)
    if backtest:
        # The preserved ICT pipeline replays candle by candle, so the run is
        # long enough to need an explicit busy state rather than a frozen page.
        with st.spinner("Replaying finalized candles through the ARJUNA rules..."):
            _arjun_backtest(data, starting_capital, blackout)
    if paper:
        _arjun_paper(data, symbol, timeframe, starting_capital, blackout)


def _arjun_signal(data, symbol: str, timeframe: str, blackout: bool) -> None:
    """Run the preserved ICT analysis and show its decision chain.

    ``analyze_ict`` is the existing explainable adapter over the original ICT
    primitives; no scoring rule or threshold is changed here.
    """
    from demo_safety import ict_entry_gate
    from platform_core.settings import load_settings

    permitted, reason = ict_entry_gate(data.index[-1], news_blackout=blackout)
    if not permitted:
        ui.html_block(ui.info_state("ARJUNA entry gate", reason))
        return

    try:
        analysis = analyze_ict(data, rr=float(load_settings().risk.default_rr)).to_dict()
    except PlatformError as exc:
        render_status(exc.status, exc.message)
        return
    except (ValueError, TypeError, KeyError) as exc:
        LOGGER.exception("ARJUNA analysis failed")
        ui.html_block(ui.error_state(
            "ARJUNA analysis failed",
            f"{type(exc).__name__}. Details are in the private app log; no stack "
            "trace is shown here.",
        ))
        return

    st.session_state.ict_analysis = analysis
    render_ict_stages(analysis)

    if not analysis.get("has_setup"):
        closest = max(analysis.get("long_score", 0), analysis.get("short_score", 0))
        ui.html_block(ui.info_state(
            "No valid ARJUNA setup",
            f"Closest score {closest} of the 60 required. This is normal and is not "
            "an error.",
        ))
        return

    ui.html_block(ui.card(
        f'<div class="ui-row">{ui.status_pill("Valid setup", "profit", dot=True)}'
        f'{ui.pill(str(analysis.get("side", "")))}</div>'
        f'<div style="margin-top:.55rem">{ui.rows(_level_pairs(analysis))}</div>',
        variant="profit",
    ))
    with st.expander("Full ARJUNA analysis", expanded=False):
        st.json(analysis)


def _level_pairs(analysis: dict) -> list[tuple[str, str]]:
    """Format the entry, stop, target, and R of a setup for display."""
    def price(key: str) -> str:
        value = analysis.get(key)
        return f"${float(value):,.2f}" if value is not None else "--"

    return [
        ("Entry", price("entry")),
        ("Stop loss", price("stop")),
        ("Take profit", price("target")),
        ("Risk : reward", ui.ratio(analysis.get("rr"), suffix="R")),
        ("Score", ui.esc(str(analysis.get("score", 0)))),
    ]


def _backtest_cards(metrics: dict) -> list[str]:
    """The standard metric cards shared by every backtest on this screen."""
    return [
        ui.card(ui.stat("Total P&L",
                        f'<span class="{ui.tone_class(metrics["Total P&L"])}">'
                        f'{ui.money(metrics["Total P&L"])}</span>', "Net of fees")),
        ui.card(ui.stat("Return",
                        f'<span class="{ui.tone_class(metrics["Total return %"])}">'
                        f'{ui.percent(metrics["Total return %"])}</span>',
                        "On starting capital")),
        ui.card(ui.stat("Max drawdown",
                        f'<span class="ui-neg">'
                        f'{ui.percent(metrics["Maximum drawdown %"])}</span>',
                        "Peak to trough")),
        ui.card(ui.stat("Win rate", ui.percent(metrics["Win rate %"], signed=False),
                        f'{metrics["Number of trades"]} trades')),
        ui.card(ui.stat("Profit factor", ui.ratio(metrics["Profit factor"]),
                        "Gross win / gross loss")),
        ui.card(ui.stat("Average trade", ui.money(metrics["Average trade"]),
                        "Per trade")),
    ]


def _arjun_backtest(data, starting_capital: float, blackout: bool) -> None:
    """Historical simulation of the preserved ARJUNA signal."""
    from demo_backtest import run_ict_backtest

    ui.html_block(ui.section_head("ARJUNA backtest", "Historical simulation · no orders"))
    try:
        metrics, equity, trades = run_ict_backtest(
            data, starting_capital=float(starting_capital), news_blackout=blackout)
    except (MarketDataError, ValueError) as exc:
        ui.html_block(ui.error_state("Backtest could not run", str(exc)))
        return
    except Exception:
        LOGGER.exception("ARJUNA backtest failed")
        ui.html_block(ui.error_state(
            "Backtest failed", "The historical simulation could not complete."))
        return

    ui.html_block(ui.grid(3, *_backtest_cards(metrics)))
    if equity is not None and not equity.empty:
        ui.html_block(ui.card(ui.area_chart(
            [(str(index)[:16], float(value)) for index, value in equity["equity"].items()],
            tone="accent"), variant="flat"))
    st.caption(
        "Signals use finalized candles and fill at the next candle open. 24-hour "
        "evaluation, news blackout, cooldown, fixed 1% risk, default 2R target "
        "(minimum 1.5R) and a 1x notional cap apply."
    )
    if trades is not None and not trades.empty:
        with st.expander("Trade log", expanded=False):
            st.dataframe(trades, width="stretch", hide_index=True)


def _arjun_paper(data, symbol: str, timeframe: str, starting_capital: float,
                 blackout: bool) -> None:
    """Advance the ARJUNA paper account by one finalized candle."""
    account = state.get_paper_account(strategies.ARJUN_KEY, starting_capital,
                                      symbol, timeframe)
    context = {
        "strategy_id": "ict", "strategy_version": "1.0.0", "market": symbol,
        "timeframe": timeframe, "data_source": "validated provider chain",
        "entry_rules": "Existing ARJUNA signal implementation",
        "market_context": f"close={float(data.close.iloc[-1]):.8g}",
    }
    try:
        message = advance_ict_paper_account(data, account, news_blackout=blackout,
                                            journal_context=context)
    except (MarketDataError, ValueError) as exc:
        ui.html_block(ui.error_state("Paper trading blocked", str(exc)))
        return
    except Exception:
        LOGGER.exception("ARJUNA paper advance failed")
        ui.html_block(ui.error_state(
            "Paper trading failed", "The paper account could not be advanced."))
        return

    st.info(message)
    render_account_state(account, "Arjun")


def render_account_state(account: dict, label: str) -> None:
    """Balance, open position, and journal for one paper account."""
    balance = float(account.get("balance", 0.0) or 0.0)
    trades = list(account.get("trades", []))
    ui.html_block(ui.grid(3,
        ui.card(ui.stat("Paper balance", ui.money(balance, signed=False),
                        f"{label} · simulation")),
        ui.card(ui.stat("Open position", "Yes" if account.get("position") else "No",
                        "Paper engine state")),
        ui.card(ui.stat("Closed trades", ui.esc(str(len(trades))), "In this session")),
    ))
    position = account.get("position")
    if position:
        pairs = [
            ("Side", ui.esc(str(position.get("side", "--")))),
            ("Entry", f"${float(position.get('entry', 0)):,.2f}"),
            ("Stop loss", f"${float(position.get('stop', 0)):,.2f}"),
            ("Take profit", f"${float(position.get('target', 0)):,.2f}"),
            ("Quantity", ui.esc(f"{float(position.get('quantity', 0)):.8g}")),
            ("Opened", ui.esc(str(position.get("opened_at", "--")))),
        ]
        ui.html_block(ui.card(ui.rows(pairs), variant="accent"))
    from ui.journal import render_journal
    render_journal(trades, key_prefix=label.lower().replace(" ", "_"))


def _render_quant(symbol: str, timeframe: str, starting_capital: float) -> None:
    """Quant workspace: configurable plugin, signal check, backtest, paper."""
    from demo_backtest import run_backtest
    from demo_paper import advance_paper_account
    from demo_strategy import validate_strategy
    from platform_strategies import strategy_registry

    plugin_id = st.selectbox(
        "Quant family", ["quant.trend", "quant.mean_reversion"],
        format_func=lambda value: strategy_registry.get(value).metadata.name,
        key="quant_family",
    )
    parameters: dict = {"side": "BUY"}
    if plugin_id == "quant.trend":
        first, second = st.columns(2)
        parameters["fast"] = first.number_input("Fast EMA", min_value=2, max_value=100, value=20)
        parameters["slow"] = second.number_input("Slow EMA", min_value=3, max_value=200, value=50)
        if parameters["fast"] >= parameters["slow"]:
            ui.html_block(ui.error_state("Invalid parameters",
                                         "The fast EMA must be shorter than the slow EMA."))
            return
    else:
        first, second, third = st.columns(3)
        parameters["period"] = first.number_input("RSI period", min_value=2, max_value=100, value=14)
        parameters["oversold"] = second.number_input("Oversold threshold", min_value=1, max_value=49, value=30)
        parameters["overbought"] = third.number_input("Overbought threshold", min_value=51, max_value=99, value=70)

    buttons = st.columns(3, gap="small")
    check_signal = buttons[0].button("Check latest signal", key="quant_signal",
                                    type="primary", width="stretch")
    backtest = buttons[1].button("Backtest Quant", key="quant_backtest", width="stretch")
    paper = buttons[2].button("Run Quant paper trading", key="quant_paper", width="stretch")
    if not (check_signal or backtest or paper):
        return

    result = marketdata.load(symbol, timeframe,
                             marketdata.BACKTEST_CANDLES if backtest
                             else marketdata.ANALYSIS_CANDLES)
    if not result.ok:
        ui.html_block(ui.error_state("Market data unavailable", result.error))
        return
    st.caption(f"Data source: {result.source} · {len(result.frame)} finalized candles · UTC")
    marketdata.publish_chart_snapshot(result)
    data = result.frame

    try:
        plugin = strategy_registry.get(plugin_id)
        signals = plugin.signal_series(data, parameters)
    except (KeyError, ValueError, TypeError) as exc:
        ui.html_block(ui.error_state("Quant signal failed", str(exc)))
        return

    side = signals.iloc[-1]
    ui.html_block(ui.card(
        f'<div class="ui-row">{ui.status_pill("Latest signal", "accent", dot=False)}'
        f"{ui.pill(str(side), 'profit' if side == 'BUY' else 'loss' if side == 'SELL' else '')}"
        "</div>"
        f'<div class="ui-sub" style="margin-top:.4rem">'
        f'Computed on the latest finalized candle at {ui.esc(str(data.index[-1]))}.</div>',
        variant="accent",
    ))

    if backtest:
        neutral = validate_strategy({
            "side": "BUY",
            "entry": [{"indicator": "price", "operator": ">", "value": 0}],
            "exit": [{"indicator": "price", "operator": "<", "value": 0}],
        })
        try:
            metrics, equity, trades = run_backtest(data, neutral,
                                                   starting_capital=float(starting_capital),
                                                   signal_sides=signals)
        except (MarketDataError, ValueError) as exc:
            ui.html_block(ui.error_state("Quant backtest failed", str(exc)))
            return
        st.subheader("Quant backtest · historical simulation")
        ui.html_block(ui.grid(3, *_backtest_cards(metrics)))
        if equity is not None and not equity.empty:
            ui.html_block(ui.card(ui.area_chart(
                [(str(index)[:16], float(value)) for index, value in equity["equity"].items()],
                tone="accent"), variant="flat"))
        if trades is not None and not trades.empty:
            st.dataframe(trades, width="stretch", hide_index=True)

    if paper:
        account = state.get_paper_account(plugin_id, starting_capital, symbol, timeframe)
        context = {
            "strategy_id": plugin_id, "strategy_version": plugin.metadata.version,
            "market": symbol, "timeframe": timeframe,
            "data_source": result.source,
            "entry_rules": __import__("json").dumps(parameters, sort_keys=True),
            "market_context": f"close={float(data.close.iloc[-1]):.8g}",
        }
        try:
            message = advance_paper_account(
                data, {"side": "BUY", "entry": [], "exit": [],
                       "risk_fraction": .01, "rr": 2., "leverage": 1.},
                account, signal_sides=signals, journal_context=context)
        except (MarketDataError, ValueError) as exc:
            ui.html_block(ui.error_state("Quant paper trading blocked", str(exc)))
            return
        st.subheader("QUANT PAPER TRADING")
        st.info(message)
        render_account_state(account, "Quant")


def _render_multi_strategy(symbol: str, timeframe: str,
                           starting_capital: float) -> None:
    """Multi-strategy engine: regime, per-strategy signals, and paper trading.

    This is the same ``bot.py`` pipeline the terminal bot runs, including the
    4+ points or 2 heavy conditions confirmation gate.
    """
    from config import DEFAULT_RR

    ui.html_block(ui.grid(4,
        ui.card(ui.stat("Market", ui.esc(f"{symbol} · {timeframe}"), "Paper only")),
        ui.card(ui.stat("Confirmation", "4+ pts or 2 heavy", "Engine gate")),
        ui.card(ui.stat("Risk", "1%", f"Minimum 1.5R · default {DEFAULT_RR:g}R")),
        ui.card(ui.stat("Session", "24 / 7", "Time never blocks a setup")),
    ))

    if st.button("Analyze all strategies", key="run_multi_analysis", type="primary"):
        analysis = _run_multi_analysis(symbol, timeframe, starting_capital)
        _render_multi_results(analysis)
        st.divider()
        _multi_paper_controls(analysis, starting_capital)
        return

    # Re-renders (rerun, tab change, strategy switch) must keep the controls
    # available: without this, a saved analysis would show results but offer no
    # way to act on them until the user re-ran the analysis.
    analysis = st.session_state.get("multi_strategy_analysis")
    if analysis:
        _render_multi_results(analysis)
        st.divider()
        _multi_paper_controls(analysis, starting_capital)


def _run_multi_analysis(symbol: str, timeframe: str, starting_capital: float) -> dict:
    """Run the existing multi-strategy engine over validated candles."""
    from bot import analyze_multi_strategy_candles

    result = marketdata.load(symbol, timeframe, marketdata.ANALYSIS_CANDLES)
    if not result.ok:
        ui.html_block(ui.error_state("Market data unavailable", result.error))
        return {}
    marketdata.publish_chart_snapshot(result)
    st.caption(f"Data source: {result.source} · {len(result.frame)} finalized candles · UTC")

    with st.spinner("Running the multi-strategy engine on finalized candles..."):
        candles = [
            [int(timestamp.value // 1_000_000), float(row.open), float(row.high),
             float(row.low), float(row.close), float(row.volume)]
            for timestamp, row in result.frame.iterrows()
        ]
        analysis = analyze_multi_strategy_candles(candles)
    st.session_state.multi_strategy_analysis = analysis
    st.session_state.multi_strategy_market = (symbol, timeframe, result.source)
    st.session_state.multi_strategy_frame = result.frame.copy()
    st.session_state.multi_strategy_candles = candles
    state.get_paper_account("multi.strategy", float(starting_capital), "BTC/USDT", "5m")
    return analysis


def _multi_paper_controls(analysis: dict, starting_capital: float) -> None:
    """Advance the dedicated multi-strategy paper account by one candle.

    This is the engine's own entry path: confirmed signals only, then the
    account's cooldown, daily trade/loss limits, ATR sizing, and the 1x notional
    cap. The UI adds a gate before it is even offered, and that gate is
    advisory — the account below remains the authority.
    """
    from config import (DEFAULT_RR, MAX_DAILY_LOSS_R, MAX_TRADES_PER_DAY,
                        NEWS_BLACKOUT, NEWS_FILTER_ENABLED)
    from bot import build_multi_strategy_trade_levels
    from demo_paper import advance_paper_account

    ui.html_block(ui.section_head("Paper trading", "Simulated execution"))
    if not st.button("Update multi-strategy paper account",
                     key="update_multi_strategy_paper", type="primary"):
        return

    frame = st.session_state.get("multi_strategy_frame")
    loaded_market, loaded_interval, loaded_source = st.session_state.get(
        "multi_strategy_market", ("BTC/USDT", "5m", "Unknown source"))
    if frame is None or (loaded_market, loaded_interval) != ("BTC/USDT", "5m"):
        ui.html_block(ui.error_state(
            "Cannot update the paper account",
            "Select BTC/USDT and 5m, then analyze the strategies before updating "
            "the dedicated paper account.",
        ))
        return

    account = state.get_paper_account("multi.strategy", float(starting_capital),
                                      "BTC/USDT", "5m")
    session_date = str(frame.index[-1].date())
    if account.get("daily_entry_date") != session_date:
        account["daily_entry_date"] = session_date
        account["daily_entry_count"] = 0
    daily_r = 0.0
    for trade in account.get("trades", []):
        try:
            closed_date = str(pd.Timestamp(trade["closed_at"]).date())
            distance = float(trade.get("risk_distance",
                                       abs(float(trade["entry"])) * 0.01))
            risk = distance * float(trade["quantity"])
            if closed_date == session_date and risk > 0:
                daily_r += float(trade["net_pnl"]) / risk
        except (KeyError, TypeError, ValueError):
            continue

    final = analysis.get("final_signal") or {}
    side = final.get("side")
    confirmed = side in {"LONG", "SHORT"} and bool(final.get("confirmation_passed"))
    flat = account.get("position") is None
    blackout = bool(NEWS_FILTER_ENABLED and NEWS_BLACKOUT)

    blocked = None
    if blackout and flat:
        blocked = "TRADE BLOCKED: NEWS BLACKOUT ACTIVE"
    elif flat and not confirmed:
        blocked = ("TRADE BLOCKED: INSUFFICIENT CONFIRMATION" if confirmed is False
                   and side in {"LONG", "SHORT"} else "TRADE BLOCKED: NO VALID DIRECTION")
    elif flat and int(account.get("daily_entry_count", 0)) >= MAX_TRADES_PER_DAY:
        blocked = "TRADE BLOCKED: MAX DAILY TRADES REACHED"
    elif flat and daily_r <= -abs(MAX_DAILY_LOSS_R):
        blocked = "TRADE BLOCKED: MAX DAILY LOSS REACHED"

    approved = side if (confirmed and blocked is None and flat) else "NEUTRAL"
    entry_levels = None
    if approved in {"LONG", "SHORT"}:
        try:
            entry_levels = build_multi_strategy_trade_levels(
                st.session_state.multi_strategy_candles, approved)
        except (TypeError, ValueError, KeyError) as exc:
            blocked = f"TRADE BLOCKED: {exc}"
            approved = "NEUTRAL"

    side_series = pd.Series("NEUTRAL", index=frame.index, dtype="object")
    side_series.iloc[-1] = "BUY" if approved == "LONG" else "SELL" if approved == "SHORT" else "NEUTRAL"
    was_flat = account.get("position") is None
    context = {
        "strategy_id": "multi.strategy", "market": loaded_market,
        "timeframe": loaded_interval, "data_source": loaded_source,
        "entry_rules": "Existing bot.py multi-strategy scorer; 4+ points OR 2 heavy conditions",
        "market_context": (f"regime={(analysis.get('regime') or {}).get('regime', 'UNKNOWN')}; "
                           f"score={final.get('score', 0)}"),
    }
    message = advance_paper_account(
        frame,
        {"side": "BUY", "entry": [], "exit": [], "risk_fraction": 0.01,
         "rr": DEFAULT_RR, "leverage": 1.0},
        account, signal_sides=side_series, journal_context=context,
        entry_levels=entry_levels,
    )
    if was_flat and account.get("position") is not None:
        account["daily_entry_count"] = int(account.get("daily_entry_count", 0)) + 1
    if blocked and was_flat:
        st.error(blocked)
    else:
        st.info(message)
    render_account_state(account, "Multi-Strategy")


def _render_multi_results(analysis: dict) -> None:
    """Regime, per-strategy signals, and the aggregate confirmation gate."""
    regime = analysis.get("regime") or {}
    final = analysis.get("final_signal") or {}
    signals = analysis.get("signals") or {}
    price = analysis.get("price")

    ui.html_block(ui.section_head("Live analysis", "Current market"))
    ui.html_block(ui.grid(4,
        ui.card(ui.stat("Current price", ui.money(price, signed=False),
                        "Finalized close")),
        ui.card(ui.stat("Market regime", ui.esc(str(regime.get("regime", "UNKNOWN"))),
                        ui.esc(str(regime.get("reason", "")))[:60])),
        ui.card(ui.stat("Regime score", ui.esc(str(regime.get("score", 0))),
                        "Regime detector")),
        ui.card(ui.stat("Final signal", ui.esc(str(final.get("side", "NEUTRAL"))),
                        f'Score {float(final.get("score", 0)):.2f}')),
    ))

    ui.html_block(ui.section_head("Strategy signals", f"{len(signals)} active signals"))
    rows = [
        (ui.esc(strategies.display_name(name)),
         ui.pill(str(signal.get("side", "NEUTRAL")),
                 "profit" if signal.get("side") == "LONG"
                 else "loss" if signal.get("side") == "SHORT" else ""),
         ui.esc(str(signal.get("score", 0))),
         ui.esc(str(signal.get("reason", "")).replace("ICT", "Arjun"))[:70])
        for name, signal in signals.items()
    ]
    ui.html_block(ui.table(["Strategy", "Signal", "Score", "Reason"], rows,
                           align_right=(2,)))

    ui.html_block(ui.section_head("Confirmation"))
    passed = bool(final.get("confirmation_passed"))
    heavy = final.get("heavy_conditions") or []
    body = (
        f'<div class="ui-row">{ui.status_pill("Gates passed" if passed else "Trade blocked",
                                             "profit" if passed else "loss", dot=True)}</div>'
        f'<div style="margin-top:.5rem">{ui.rows([("Direction", ui.esc(str(final.get("side", "NEUTRAL")))), ("Final score", ui.esc(str(final.get("score", 0)))), ("Normal confirmation", "Passed" if final.get("normal_confirmation") else "Not passed"), ("Heavy confirmation", "Passed" if final.get("heavy_confirmation") else "Not passed"), ("Heavy conditions", ui.esc(", ".join(strategies.display_name(name) for name in heavy) or "None")) ])}</div>'
        f'<div class="ui-sub" style="margin-top:.45rem">{ui.esc(str(final.get("reason", "")))}</div>'
    )
    ui.html_block(ui.card(body, variant="profit" if passed else "loss"))


def _render_side_panel(choice: str, symbol: str, timeframe: str,
                       starting_capital: float) -> None:
    """Order ticket for the pending setup, plus the live position card."""
    account = state.get_paper_account(LABEL_TO_KEY.get(choice, "multi.strategy"),
                                      float(starting_capital), symbol, timeframe)
    ui.html_block(ui.section_head("Position", "Live paper state"))
    _render_position(account, choice)

    analysis = st.session_state.get("ict_analysis") or {}
    has_setup = bool(analysis.get("has_setup"))
    strategy_key = LABEL_TO_KEY.get(choice, "multi.strategy")
    strategy_name = strategies.display_name(strategy_key)

    if choice == "ARJUNA Strategy" and has_setup:
        intent = orderpanel.OrderIntent(
            symbol=symbol, timeframe=timeframe,
            direction=str(analysis.get("side", "LONG")),
            entry=float(analysis["entry"]), stop_loss=float(analysis["stop"]),
            take_profit=float(analysis["target"]),
            strategy_id=strategy_key, strategy_name=strategy_name,
            reason=str(analysis.get("reason", "")),
            timestamp=pd.Timestamp.now(tz="UTC"),
        )
        decision = orderpanel.render_intent(
            intent, account, key_prefix="arjun_order",
            open_positions=1 if account.get("position") else 0,
            daily_trades=int(account.get("daily_entry_count", 0)),
            daily_r=float(account.get("daily_r", 0.0)),
        )
        if decision is not None:
            _confirm_paper_entry(decision, intent, account)
    else:
        ui.html_block(ui.card(
            ui.info_state(
                "No pending setup",
                "Run the strategy's signal check to get entry, stop, and target "
                "levels. The ticket appears here the moment a valid setup exists.",
            ),
            variant="flat",
        ))


def _render_position(account: dict, choice: str) -> None:
    """The open position for this account, or an explicit empty state."""
    position = account.get("position")
    if not position:
        ui.html_block(ui.empty_state("No active trades",
                                     "Flat. The paper engine is waiting for a valid setup.",
                                     icon="○"))
        return
    mark = account.get("last_mark")
    direction = str(position.get("side", "")).upper()
    pnl = None
    try:
        entry = float(position["entry"])
        quantity = float(position["quantity"])
        if mark is not None:
            pnl = (float(mark) - entry) * quantity * (1 if direction == "LONG" else -1)
    except (KeyError, TypeError, ValueError):
        entry = quantity = None
    risk = position.get("risk_amount")
    ui.html_block(ui.position_card(
        str(account.get("market", "--")), direction,
        entry=entry if entry is not None else None,
        price=float(mark) if mark is not None else None,
        sl=position.get("stop"), tp=position.get("target"),
        quantity=quantity, pnl=pnl,
        r_multiple=(pnl / float(risk)) if (pnl is not None and risk) else None,
        strategy=strategies.display_name(str(account.get("strategy_id", ""))),
        opened_at=str(position.get("opened_at", "")),
        timeframe=str(account.get("timeframe", "")),
    ))


def _confirm_paper_entry(decision, intent: orderpanel.OrderIntent, account: dict) -> None:
    """Open the paper position using exactly the levels risk approved.

    Quantity and risk amount come from :class:`RiskDecision` rather than from
    any arithmetic here, and the levels come from the validated
    :class:`OrderIntent`, so the position that opens is exactly the ticket the
    user confirmed. Nothing is sent to a broker.
    """
    account["position"] = {
        "side": intent.direction,
        "entry": intent.entry,
        "stop": intent.stop_loss,
        "target": intent.take_profit,
        "quantity": decision.quantity,
        "risk_amount": decision.risk_amount,
        "risk_distance": decision.risk_distance,
        "opened_at": str(intent.timestamp),
        "journal_context": {
            "strategy_id": intent.strategy_id,
            "strategy_version": "1.0.0",
            "market": intent.symbol,
            "timeframe": intent.timeframe,
            "entry_rules": intent.reason,
            "market_context": intent.reason,
        },
    }
    account["last_action"] = str(intent.timestamp)
    account["last_mark"] = intent.entry
    account["daily_entry_count"] = int(account.get("daily_entry_count", 0)) + 1
    st.success(
        f"Paper {intent.direction} position opened at ${intent.entry:,.2f} "
        f"for {decision.quantity:.8g} units. No order was sent anywhere."
    )


# PLACEHOLDER_IMPORTS