"""Streamlit front end for the public, simulation-only trading demo."""

import logging
import os
import json
import math
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from demo_backtest import run_backtest, run_ict_backtest
from demo_data import (MarketDataError, MarketDataResult, fetch_market_data,
                       fetch_historical_market_data, SUPPORTED_INTERVALS)
from demo_paper import advance_paper_account, advance_ict_paper_account
from demo_safety import DEMO_MODE, LIVE_ORDERS_ENABLED, SafetyError, assert_demo_mode, ict_entry_gate
from demo_strategy import EXAMPLES, StrategyError, generate_strategy_code, interpret_strategy, parse_strategy_json, summarize_strategy, validate_strategy
from platform_strategies import strategy_registry
from charting import render_ohlcv_chart
from charting_overlays import build_ict_overlay, build_overlay_series
from portfolio import portfolio_snapshot
from voice_strategy import render_voice_strategy_controls
from platform_core.errors import PlatformError
from platform_core.market_data import assess_health
from platform_core.settings import load_settings
from platform_core.status import DataHealth, Status
from platform_ui import (render_data_health, render_ict_overlay_toggles,
                         render_ict_stages, render_indicator_controls,
                         render_mobile_nav, render_paper_banner, render_status)

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("ict_demo")
load_dotenv()
SETTINGS = load_settings()
DEFAULT_RR_UI = SETTINGS.risk.default_rr
try:
    if not os.getenv("GEMINI_API_KEY") and st.secrets.get("GEMINI_API_KEY"):
        os.environ["GEMINI_API_KEY"] = st.secrets["GEMINI_API_KEY"]
    if not os.getenv("GEMINI_MODEL") and st.secrets.get("GEMINI_MODEL"):
        os.environ["GEMINI_MODEL"] = st.secrets["GEMINI_MODEL"]
    if not os.getenv("OPENAI_API_KEY") and st.secrets.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = st.secrets["OPENAI_API_KEY"]
    if not os.getenv("OPENAI_MODEL") and st.secrets.get("OPENAI_MODEL"):
        os.environ["OPENAI_MODEL"] = st.secrets["OPENAI_MODEL"]
except Exception:
    # Streamlit secrets are optional for local runs.
    pass

st.set_page_config(page_title="AI Algo Trading Demo", page_icon="📈", layout="wide")
from platform_ui import inject_design_system

inject_design_system(SETTINGS)
render_paper_banner(SETTINGS)
render_mobile_nav()
st.title("Trading Platform · Paper")
st.error(
    f"DEMO MODE: ON   ·   LIVE ORDERS: DISABLED   ·   ENVIRONMENT: {SETTINGS.environment}\n\n"
    "Paper trading only. No broker or exchange credentials exist in this application. "
    "Not investment advice."
)
st.caption(
    "Market data → data validation → strategy engine → signal → risk engine → paper execution "
    "→ portfolio & P&L → backtesting. Strategies are plug-in modules; the engine never "
    "depends on this UI."
)
# Reserve one stable location for the chart so strategy-panel reruns do not
# mount it at a different Streamlit delta path.
chart_slot = st.container()


@st.cache_data(ttl=60, show_spinner=False)
def load_market_data(selected_symbol, selected_timeframe, candle_limit):
    return fetch_market_data(selected_symbol, selected_timeframe, limit=candle_limit)


def show_market_data_status(result: MarketDataResult, symbol: str, timeframe: str) -> None:
    """Record the chart snapshot for the market that was actually loaded."""
    st.caption(f"Data source: {result.source} · {len(result.frame)} finalized candles · UTC")
    if result.used_fallback:
        st.info("Using backup data source")
    st.session_state.chart_snapshot = {
        "frame": result.frame.tail(400).copy(),
        "symbol": symbol,
        "timeframe": timeframe,
        "source": result.source,
    }


def show_additional_metrics(metrics):
    fields = (
        "Gross profit", "Gross loss", "Expectancy", "Average win", "Average loss", "Average R",
        "Fees paid", "Estimated slippage paid", "Average trade duration minutes",
        "Maximum consecutive wins", "Maximum consecutive losses", "Sharpe ratio", "Sortino ratio",
    )
    with st.expander("Additional backtest metrics"):
        st.json({name: metrics.get(name) if metrics.get(name) is not None else "N/A" for name in fields})


def get_paper_account(strategy_id, starting_capital, market, interval):
    accounts = st.session_state.setdefault("paper_accounts", {})
    account_key = f"{strategy_id}|{market}|{interval}"
    state = accounts.setdefault(account_key, {})
    if state.get("starting_capital") != float(starting_capital):
        state.clear()
        state.update({"balance": float(starting_capital), "starting_capital": float(starting_capital),
                      "position": None, "trades": [], "last_action": None, "journal_notes": {}})
    state.update({"strategy_id": strategy_id, "market": market, "timeframe": interval, "fee_rate": 0.0004})
    return state


def render_multi_strategy_backtesting(default_symbol, default_timeframe):
    """Render a separate historical simulator; it never touches paper accounts."""
    from bot import multi_strategy_names
    from multi_strategy_backtest import run_multi_strategy_backtest

    st.subheader("📊 Backtesting")
    st.caption("Historical candles only · combined scorer and confirmation · simulated execution · no broker orders")
    st.info("Combined Multi-Strategy Backtest: selected strategy signals are aggregated by the existing scorer. "
            "Signals use only candles through T close and fill at T+1 open. If SL and TP touch in one candle, SL fills first.")
    with st.form("multi_strategy_backtest_form"):
        columns = st.columns(4)
        bt_symbol = columns[0].selectbox("Symbol", ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
                                         index=["BTC/USDT", "ETH/USDT", "SOL/USDT"].index(default_symbol)
                                         if default_symbol in {"BTC/USDT", "ETH/USDT", "SOL/USDT"} else 0)
        intervals = list(SUPPORTED_INTERVALS)
        bt_timeframe = columns[1].selectbox("Timeframe", intervals,
                                            index=intervals.index(default_timeframe) if default_timeframe in intervals else 0)
        today = datetime.now(ZoneInfo("UTC")).date()
        sample_days = max(1, int(500 * SUPPORTED_INTERVALS[bt_timeframe] / 86_400_000))
        bt_start = columns[2].date_input("Start date (UTC)", value=today - timedelta(days=sample_days), key="bt_start_date")
        bt_end = columns[3].date_input("End date (UTC)", value=today, key="bt_end_date")

        risk_cols = st.columns(5)
        initial_capital = risk_cols[0].number_input("Initial capital", min_value=100.0, value=10_000.0, step=500.0)
        risk_percent = risk_cols[1].number_input("Risk per trade (%)", min_value=0.1, max_value=1.0, value=1.0, step=0.1)
        minimum_rr = risk_cols[2].number_input("Minimum R:R", min_value=1.5, max_value=10.0, value=1.5, step=0.1)
        target_rr = risk_cols[3].number_input("Target R:R", min_value=1.5, max_value=10.0,
                                              value=max(2.0, float(minimum_rr)), step=0.1)
        max_open = risk_cols[4].number_input("Max concurrent trades", min_value=1, max_value=10, value=1, step=1)

        cost_cols = st.columns(2)
        fee_percent = cost_cols[0].number_input("Fee per side (%)", min_value=0.0, max_value=1.0, value=0.04, step=0.01)
        slippage_percent = cost_cols[1].number_input("Slippage per side (%)", min_value=0.0, max_value=1.0, value=0.01, step=0.01)

        strategy_names = multi_strategy_names()
        display_name = lambda key: "ARJUNA" if key == "ICT" else key
        strategy_mode = st.radio("Strategy selection", ["All Strategies", "Individual Strategy", "Multiple Strategies"],
                                 horizontal=True, key="bt_strategy_mode")
        if strategy_mode == "All Strategies":
            selected_strategies = list(strategy_names)
        elif strategy_mode == "Individual Strategy":
            one_strategy = st.selectbox("Strategy", strategy_names, format_func=display_name, key="bt_one_strategy")
            selected_strategies = [one_strategy]
        else:
            selected_strategies = st.multiselect("Strategies to aggregate", strategy_names,
                                                 default=list(strategy_names), format_func=display_name,
                                                 key="bt_many_strategies")

        upload = st.file_uploader("Optional historical OHLCV CSV", type=["csv"], key="multi_strategy_backtest_csv")
        st.caption("CSV columns: timestamp, open, high, low, close, volume. Maximum fetched range: 1,000 candles. "
                   "Risk is capped at 1%, minimum R:R is 1.5R, and the default target is 2R.")
        run_backtest_button = st.form_submit_button("Run historical backtest", type="primary")

    if run_backtest_button:
        if bt_start >= bt_end:
            st.warning("Start date must be earlier than end date.")
        elif not selected_strategies:
            st.warning("Select at least one strategy.")
        elif target_rr < minimum_rr:
            st.warning("Target R:R must be at least the selected minimum R:R.")
        else:
            start_dt = datetime.combine(bt_start, time.min, tzinfo=ZoneInfo("UTC"))
            end_dt = datetime.combine(bt_end + timedelta(days=1), time.min, tzinfo=ZoneInfo("UTC"))
            try:
                with st.spinner("Loading historical candles and replaying confirmed signals..."):
                    market_data = fetch_historical_market_data(
                        bt_symbol, bt_timeframe, start_dt, end_dt, csv_data=upload
                    )
                    result = run_multi_strategy_backtest(
                        market_data.frame, symbol=bt_symbol, timeframe=bt_timeframe,
                        starting_capital=initial_capital, risk_fraction=risk_percent / 100.0,
                        min_rr=minimum_rr, target_rr=target_rr,
                        max_concurrent_trades=int(max_open), fee_rate=fee_percent / 100.0,
                        slippage_rate=slippage_percent / 100.0,
                        strategy_selection=selected_strategies,
                    )
                    result["source"] = market_data.source
                st.session_state.multi_strategy_backtest_result = result
                st.success("Historical backtest completed.")
            except (MarketDataError, ValueError) as exc:
                st.warning(str(exc))
            except Exception:
                LOGGER.exception("Multi-strategy historical backtest failed")
                st.error("Backtest failed unexpectedly. Check the historical data and try a shorter range.")

    result = st.session_state.get("multi_strategy_backtest_result")
    if not result:
        return
    st.caption(f"Source: {result['source']} · {result['symbol']} · {result['timeframe']} · "
               f"{result['start']} to {result['end']} UTC · {len(result['equity'])} evaluation candles")
    metrics = result["metrics"]
    metric_groups = [
        ["Initial capital", "Final capital", "Total P&L", "Return %", "Total trades"],
        ["Winning trades", "Losing trades", "Win rate %", "Profit factor", "Average R"],
        ["Max drawdown %", "Maximum consecutive wins", "Maximum consecutive losses", "Average trade duration minutes", "Blocked signals"],
        ["Long trades", "Short trades", "Executed signals"],
    ]
    for group in metric_groups:
        cells = st.columns(len(group))
        for cell, key in zip(cells, group):
            value = metrics[key]
            if key in {"Initial capital", "Final capital", "Total P&L"}:
                rendered = f"${value:,.2f}"
            elif key.endswith("%"):
                rendered = f"{value:.2f}%"
            elif key == "Profit factor" and math.isinf(value):
                rendered = "∞"
            elif isinstance(value, float):
                rendered = f"{value:.2f}"
            else:
                rendered = str(value)
            cell.metric(key, rendered)

    if result["trades"].empty:
        st.info("No confirmed, risk-valid trades were executed in the selected historical range.")
    st.subheader("Strategy breakdown · measured historical results")
    st.dataframe(result["strategy_breakdown"], width="stretch", hide_index=True)
    st.subheader("Equity curve")
    equity = result["equity"]
    if not equity.empty:
        st.line_chart(equity[["equity"]], height=300)
        st.line_chart(equity[["drawdown_pct"]], height=180)
    if not result["trades"].empty:
        with st.expander("Detailed trade log"):
            st.dataframe(result["trades"], width="stretch", hide_index=True)
            choices = list(range(len(result["trades"])))
            selected_trade = st.selectbox("Inspect a trade", choices,
                                          format_func=lambda index: f"{index + 1} · {result['trades'].iloc[index]['side']} · {result['trades'].iloc[index]['timestamp']}",
                                          key="multi_backtest_trade_detail")
            st.json(result["trades"].iloc[selected_trade].to_dict())
    if not result["blocked_signals"].empty:
        with st.expander(f"Blocked signals ({len(result['blocked_signals'])})"):
            st.dataframe(result["blocked_signals"], width="stretch", hide_index=True)


@st.fragment(run_every="20s")
def render_multi_strategy_paper_monitor():
    """Refresh Coinbase quote/candle state without rerunning the full app."""
    from demo_data import fetch_live_market_snapshot
    from demo_paper import monitor_paper_position

    st.markdown("### Multi-Strategy Paper Position · BTC/USDT · 5m")
    st.caption("PAPER MODE · REAL ORDERS DISABLED")
    account_key = "multi.strategy|BTC/USDT|5m"
    state = st.session_state.get("paper_accounts", {}).get(account_key)
    if state is None:
        state = get_paper_account("multi.strategy", 10_000.0, "BTC/USDT", "5m")
    try:
        snapshot = fetch_live_market_snapshot("BTC/USDT", "5m")
        monitor = monitor_paper_position(state, snapshot)
    except MarketDataError as exc:
        st.error(f"MARKET DATA UNAVAILABLE · {exc}")
        st.caption("No live price or position exit was inferred from stale data.")
        return
    st.metric("LIVE BTC/USDT PRICE", f"${snapshot['price']:,.2f}")
    st.caption(f"Last update: {snapshot['updated_at'].isoformat()} · Data source: {snapshot['source']}")
    account_snapshot = portfolio_snapshot([state])
    st.subheader("Session Portfolio · Multi-Strategy Paper")
    portfolio_cells = st.columns(6)
    portfolio_cells[0].metric("Cash Balance", f"${account_snapshot['balance']:,.2f}")
    portfolio_cells[1].metric("Equity", f"${account_snapshot['equity']:,.2f}")
    portfolio_cells[2].metric("Realized P&L", f"${account_snapshot['realized_pnl']:,.2f}")
    portfolio_cells[3].metric("Unrealized P&L", f"${account_snapshot['unrealized_pnl']:,.2f}")
    portfolio_cells[4].metric("Open Positions", account_snapshot["positions"])
    portfolio_cells[5].metric("Closed Trades", account_snapshot["closed_trades"])
    win_rate = account_snapshot["win_rate_pct"]
    st.caption(f"Win rate: {win_rate:.1f}%" if win_rate is not None else "Win rate: N/A · No closed trades yet")
    position = state.get("position")
    if position is None:
        if state.get("trades"):
            last_trade = state["trades"][-1]
            code = last_trade.get("exit_reason", "")
            st.success(f"{code} HIT — PAPER POSITION CLOSED" if code in {"TP", "SL"} else "PAPER POSITION CLOSED")
            st.metric("Last realized P&L", f"${float(last_trade.get('net_pnl', 0)):,.2f}")
        else:
            st.success("FLAT · No open paper position")
        return

    entry = float(position["entry"])
    stop = float(position["stop"])
    target = float(position["target"])
    quantity = float(position["quantity"])
    direction = 1 if position["side"] == "LONG" else -1
    price = float(snapshot["price"])
    unrealized = account_snapshot["unrealized_pnl"]
    target_distance = (price - target) * direction
    stop_distance = (stop - price) * direction
    cells = st.columns(4)
    cells[0].metric("Position", position["side"])
    cells[1].metric("Entry price", f"${entry:,.2f}")
    cells[2].metric("Current price", f"${price:,.2f}")
    cells[3].metric("Stop loss", f"${stop:,.2f}")
    cells = st.columns(4)
    cells[0].metric("Take profit", f"${target:,.2f}")
    cells[1].metric("Quantity", f"{quantity:.8f}")
    cells[2].metric("Risk amount", f"${float(position.get('risk_amount', 0)):,.2f}")
    cells[3].metric("Unrealized P&L", f"${unrealized:,.2f}")
    cells = st.columns(3)
    cells[0].metric("Distance to target", f"${target_distance:,.2f}")
    cells[1].metric("Distance to stop", f"${stop_distance:,.2f}")
    cells[2].metric("Status", "MONITORING")
    st.caption(f"Opened at: {position.get('opened_at', 'Unknown')} · Last Updated: {state.get('last_mark_updated_at', snapshot['updated_at'].isoformat())} UTC · Live price is separate from the entry price.")




def show_paper_journal(state, key_prefix):
    trades = state.get("trades", [])
    if not trades:
        st.caption("No closed paper trades yet.")
        return
    st.dataframe(pd.DataFrame(trades), width="stretch", hide_index=True)
    labels = [f"#{trade.get('trade_id', index + 1)} {trade.get('side', '')} · {trade.get('closed_at', '')}" for index, trade in enumerate(trades)]
    selected = st.selectbox("Journal entry", range(len(trades)), format_func=lambda index: labels[index], key=f"{key_prefix}_journal_trade")
    note_key = f"{key_prefix}_journal_note_{selected}"
    note = st.text_area("Your note", value=trades[selected].get("notes", ""), key=note_key, max_chars=1000)
    if st.button("Save journal note", key=f"{key_prefix}_save_journal_note"):
        trades[selected]["notes"] = note
        st.success("Note saved in this Streamlit session.")

if not DEMO_MODE or LIVE_ORDERS_ENABLED:
    st.error("Safety configuration failed. Execution is disabled.")
    st.stop()

with st.sidebar:
    st.subheader("Simulation settings")
    symbol = st.selectbox("Market", ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    timeframe = st.selectbox("Candle interval", ["5m", "15m", "1h"])
    capital = st.number_input("Starting capital (simulation)", min_value=1000, max_value=1000000, value=10000, step=1000)
    st.caption("Fixed safety limits: 1% max risk · 1.5R minimum R:R · 1x max leverage · 30 minute cooldown")

strategy_choice = st.radio("Strategy", ["Multi-Strategy Engine", "AI Strategy", "ARJUNA Strategy", "Quant Strategy"],
                            horizontal=True, key="strategy_choice")

with st.expander("Strategy library · built-in plugins"):
    st.caption("Session-only strategy catalog. User accounts and persistent cloud storage are not configured in this public demo.")
    for item in strategy_registry.list():
        st.markdown(f"**{item.name}** · `{item.id}` · v{item.version} · {item.category} — {item.description}")
    for item in st.session_state.get("strategy_library", []):
        st.markdown(f"**{item['name']}** · `{item['market']} {item['timeframe']}` · v{item['version']} · saved {item['saved_at']}")


def render_portfolio_dashboard():
    """Portfolio, P&L, positions, risk, and analytics from real paper trades."""
    from platform_core.analytics import compute_analytics, equity_curve

    st.subheader("Portfolio")
    accounts = list(st.session_state.get("paper_accounts", {}).values())
    if not accounts:
        st.info("No paper account yet. Run a strategy action to create one.")
        return
    snapshot = portfolio_snapshot(accounts)
    head = st.columns(6)
    head[0].metric("Equity", f"${snapshot['equity']:,.2f}")
    head[1].metric("Cash balance", f"${snapshot['balance']:,.2f}")
    head[2].metric("Realized P&L", f"${snapshot['realized_pnl']:,.2f}")
    head[3].metric("Unrealized P&L", f"${snapshot['unrealized_pnl']:,.2f}")
    head[4].metric("Open positions", snapshot["positions"])
    head[5].metric("Closed trades", snapshot["closed_trades"])

    analytics = compute_analytics(snapshot["trade_journal"])
    if analytics["total_trades"]:
        stats = st.columns(6)
        stats[0].metric("Win rate", f"{analytics['win_rate_pct']:.1f}%")
        stats[1].metric("Expectancy",
                        f"${analytics['expectancy']:,.2f}")
        stats[2].metric("Profit factor",
                        f"{analytics['profit_factor']:.2f}"
                        if analytics["profit_factor"] is not None else "N/A")
        stats[3].metric("Average R", f"{analytics['average_r']:.2f}"
                        if analytics["average_r"] is not None else "N/A")
        stats[4].metric("Max drawdown", f"{analytics['max_drawdown_pct']:.2f}%")
        stats[5].metric("Fees paid", f"${analytics['fees_paid']:,.2f}")
        curve = equity_curve(snapshot["trade_journal"], snapshot["starting_capital"])
        if curve:
            st.line_chart(pd.DataFrame(curve)[["equity"]], height=240)
    else:
        st.info("No closed paper trades yet, so performance statistics are not available.")

    st.subheader("Risk status")
    risk = SETTINGS.risk
    risk_cols = st.columns(5)
    risk_cols[0].metric("Risk per trade", f"{risk.risk_per_trade * 100:.2f}%")
    risk_cols[1].metric("Minimum R:R", f"{risk.min_rr:g}R")
    risk_cols[2].metric("Max leverage", f"{risk.max_leverage:g}x")
    risk_cols[3].metric("Cooldown", f"{risk.cooldown_minutes} min")
    risk_cols[4].metric("Max trades / day", risk.max_trades_per_day)

    st.subheader("Recent trades")
    trades = snapshot["trade_journal"][-25:]
    if trades:
        st.dataframe(pd.DataFrame(trades), width="stretch", hide_index=True)
    else:
        st.info("No trade history yet.")


def render_markets_screen():
    """Watchlist with real prices; unavailable markets are reported explicitly."""
    st.subheader("Markets")
    st.caption("Prices come from the validated provider chain used by the strategies.")
    if not st.button("Load watchlist prices", key="load_watchlist"):
        st.info("Request watchlist prices to load the validated provider chain.")
        return
    rows = []
    for candidate in SETTINGS.market.symbols:
        try:
            result = load_market_data(candidate, timeframe, 150)
        except MarketDataError as exc:
            rows.append({"Symbol": candidate, "Status": "UNAVAILABLE",
                         "Detail": str(exc)[:60]})
            continue
        live = not result.source.startswith("Bundled")
        health, _ = assess_health(result.frame, timeframe, is_live=live)
        closes = result.frame["close"]
        change = 0.0
        if len(closes) > 1 and float(closes.iloc[-2]) != 0:
            change = float((closes.iloc[-1] - closes.iloc[-2]) / closes.iloc[-2] * 100)
        rows.append({
            "Symbol": candidate,
            "Price": float(closes.iloc[-1]),
            "Change %": round(change, 3),
            "Health": health.value,
            "Live": live,
            "Source": result.source,
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

if strategy_choice == "Multi-Strategy Engine":
    get_paper_account("multi.strategy", 10_000.0, "BTC/USDT", "5m")
    st.subheader("MULTI-STRATEGY ENGINE")
    st.write("Runs the same finalized-candle strategy, regime, and scorer pipeline used by bot.py. ARJUNA remains one of the strategies.")
    st.caption("Analysis can be run at any time, 24/7. Confirmed signals still pass paper-account cooldown, daily limits, and risk checks before entry.")
    st.caption("Multi-Strategy paper account: BTC/USDT · 5m · $10,000 starting capital.")
    cfg_cols = st.columns(4)
    cfg_cols[0].metric("Market", "BTC/USDT")
    cfg_cols[1].metric("Timeframe", "5m")
    cfg_cols[2].metric("Mode", "PAPER TRADING")
    cfg_cols[3].metric("Session", "24H / 24x7")
    cfg_cols2 = st.columns(4)
    cfg_cols2[0].metric("Risk", "1%")
    cfg_cols2[1].metric("Min R:R", "1.5R")
    cfg_cols2[2].metric("Default Target", "2R")
    cfg_cols2[3].metric("Confirmation", "4+ pts OR 2 heavy")
    st.markdown("### Live Analysis · Current market")
    run_multi_analysis = st.button("Analyze all strategies", type="primary", key="run_multi_strategy_analysis")
    if run_multi_analysis:
        try:
            assert_demo_mode()
            with st.spinner("Running the multi-strategy engine on finalized candles..."):
                data_result = load_market_data(symbol, timeframe, 500)
                show_market_data_status(data_result, symbol, timeframe)
                from bot import analyze_multi_strategy_candles

                candles = [
                    [int(timestamp.timestamp() * 1000), float(row.open), float(row.high),
                     float(row.low), float(row.close), float(row.volume)]
                    for timestamp, row in data_result.frame.iterrows()
                ]
                analysis = analyze_multi_strategy_candles(candles)
            st.session_state.multi_strategy_analysis = analysis
            st.session_state.multi_strategy_market = (symbol, timeframe, data_result.source)
            st.session_state.multi_strategy_frame = data_result.frame.copy()
            st.session_state.multi_strategy_candles = candles
            # Establish one stable BTC/5m paper account only once analysis is
            # explicitly requested; its key and capital survive reruns.
            get_paper_account("multi.strategy", 10_000.0, "BTC/USDT", "5m")
        except (MarketDataError, SafetyError, ValueError) as exc:
            st.warning(str(exc))
        except Exception:
            LOGGER.exception("Multi-strategy engine analysis failed")
            st.error("The multi-strategy analysis could not complete. Please try again.")

    analysis = st.session_state.get("multi_strategy_analysis")
    if analysis:
        loaded_market, loaded_interval, loaded_source = st.session_state.get(
            "multi_strategy_market", (symbol, timeframe, "Unknown source")
        )
        regime_data = analysis["regime"]
        final_signal = analysis["final_signal"]
        signals = analysis["signals"]
        st.caption(f"Analyzed {loaded_market} · {loaded_interval} · {loaded_source} · finalized candles")
        market_cols = st.columns(4)
        market_cols[0].metric("Current price", f"${analysis['price']:,.4f}")
        market_cols[1].metric("Market regime", regime_data.get("regime", "UNKNOWN"))
        market_cols[2].metric("Regime score", regime_data.get("score", 0))
        market_cols[3].metric("Regime reason", regime_data.get("reason", ""))

        st.subheader("Strategy signals")
        signal_rows = [
            {"Strategy": "ARJUNA" if name == "ICT" else name,
             "Signal": signal.get("side", "NEUTRAL"), "Score": signal.get("score", 0),
             "Reason": str(signal.get("reason", "")).replace("ICT", "ARJUNA")}
            for name, signal in signals.items()
        ]
        st.dataframe(pd.DataFrame(signal_rows), width="stretch", hide_index=True)

        st.subheader("Signal aggregation and confirmation")
        final_cols = st.columns(4)
        final_cols[0].metric("Final signal", final_signal.get("side", "NEUTRAL"))
        final_cols[1].metric("Final score", f"{float(final_signal.get('score', 0)):.2f}")
        confirmation_points = final_signal.get("confirmation_points")
        if confirmation_points is None:
            confirmation_points = (
                f"Long {final_signal.get('long_confirmation_points', 0)} · "
                f"Short {final_signal.get('short_confirmation_points', 0)}"
            )
        final_cols[2].metric("Confirmation points", confirmation_points)
        heavy_conditions = final_signal.get("heavy_conditions", [])
        if not heavy_conditions:
            heavy_conditions = sorted(set(
                final_signal.get("long_heavy_conditions_list", [])
                + final_signal.get("short_heavy_conditions_list", [])
            ))
        final_cols[3].metric("Heavy conditions", len(heavy_conditions))
        st.write(f"**Final reason:** {final_signal.get('reason', 'No directional agreement among active strategies.')}")
        display_heavy_conditions = ["ARJUNA" if name == "ICT" else name for name in heavy_conditions]
        st.write(f"**Heavy conditions:** {', '.join(display_heavy_conditions) if display_heavy_conditions else 'None'}")
        confirm_cols = st.columns(2)
        confirm_cols[0].metric("Normal confirmation", "Passed" if final_signal.get("normal_confirmation") else "Not passed")
        confirm_cols[1].metric("Heavy confirmation", "Passed" if final_signal.get("heavy_confirmation") else "Not passed")

        direction = final_signal.get("side")
        if direction not in {"LONG", "SHORT"}:
            st.error("TRADE BLOCKED: NO VALID DIRECTION")
        elif not final_signal.get("confirmation_passed", False):
            st.error("TRADE BLOCKED: INSUFFICIENT CONFIRMATION")
            st.caption("Required: 4+ confirmation points OR 2 independent heavy conditions.")
        else:
            from config import NEWS_BLACKOUT, NEWS_FILTER_ENABLED
            if NEWS_FILTER_ENABLED and NEWS_BLACKOUT:
                st.error("TRADE BLOCKED: NEWS BLACKOUT ACTIVE")
            else:
                st.success("SIGNAL AND CONFIRMATION GATES PASSED · PAPER MODE ONLY")
                st.caption("Before entry, the paper account applies cooldown, daily trade/loss limits, 1% risk, the default 2R target, and the 1x notional cap.")

        st.markdown("### Paper Trading · Current market + simulated execution")
        paper_multi = st.button("Update multi-strategy paper account", key="update_multi_strategy_paper")
        if paper_multi:
            frame = st.session_state.get("multi_strategy_frame")
            if frame is None or (loaded_market, loaded_interval) != ("BTC/USDT", "5m"):
                st.warning("Select BTC/USDT and 5m, then analyze the strategies before updating the dedicated paper account.")
            else:
                from config import DEFAULT_RR, MAX_DAILY_LOSS_R, MAX_TRADES_PER_DAY, NEWS_BLACKOUT, NEWS_FILTER_ENABLED
                from bot import build_multi_strategy_trade_levels
                from demo_paper import advance_paper_account

                state = get_paper_account("multi.strategy", 10_000.0, "BTC/USDT", "5m")
                timestamp = frame.index[-1]
                session_date = str(timestamp.date())
                if state.get("daily_entry_date") != session_date:
                    state["daily_entry_date"] = session_date
                    state["daily_entry_count"] = 0
                daily_r = 0.0
                for trade in state.get("trades", []):
                    try:
                        closed_date = str(pd.Timestamp(trade["closed_at"]).date())
                        risk_distance = float(trade.get("risk_distance", abs(float(trade["entry"])) * 0.01))
                        risk = risk_distance * float(trade["quantity"])
                        if closed_date == session_date and risk > 0:
                            daily_r += float(trade["net_pnl"]) / risk
                    except (KeyError, TypeError, ValueError):
                        continue

                final_side = final_signal.get("side")
                confirmed = final_side in {"LONG", "SHORT"} and final_signal.get("confirmation_passed", False)
                blocked_reason = None
                if NEWS_FILTER_ENABLED and NEWS_BLACKOUT and state.get("position") is None:
                    blocked_reason = "TRADE BLOCKED: NEWS BLACKOUT ACTIVE"
                elif state.get("position") is None and not confirmed:
                    blocked_reason = (
                        "TRADE BLOCKED: INSUFFICIENT CONFIRMATION"
                        if final_side in {"LONG", "SHORT"}
                        else "TRADE BLOCKED: NO VALID DIRECTION"
                    )
                elif state.get("position") is None and state["daily_entry_count"] >= MAX_TRADES_PER_DAY:
                    blocked_reason = "TRADE BLOCKED: MAX DAILY TRADES REACHED"
                elif state.get("position") is None and daily_r <= -abs(MAX_DAILY_LOSS_R):
                    blocked_reason = "TRADE BLOCKED: MAX DAILY LOSS REACHED"

                approved_side = final_side if confirmed and blocked_reason is None else "NEUTRAL"
                entry_levels = None
                if approved_side in {"LONG", "SHORT"} and state.get("position") is None:
                    try:
                        entry_levels = build_multi_strategy_trade_levels(
                            st.session_state.multi_strategy_candles, approved_side
                        )
                    except (TypeError, ValueError, KeyError) as exc:
                        blocked_reason = f"TRADE BLOCKED: {exc}"
                        approved_side = "NEUTRAL"
                side_series = pd.Series("NEUTRAL", index=frame.index, dtype="object")
                side_series.iloc[-1] = "BUY" if approved_side == "LONG" else "SELL" if approved_side == "SHORT" else "NEUTRAL"
                was_flat = state.get("position") is None
                result = advance_paper_account(
                    frame,
                    {"side": "BUY", "entry": [], "exit": [], "risk_fraction": 0.01,
                     "rr": DEFAULT_RR, "leverage": 1.0},
                    state,
                    signal_sides=side_series,
                    journal_context={
                        "strategy_id": "multi.strategy", "market": loaded_market,
                        "timeframe": loaded_interval, "data_source": loaded_source,
                        "entry_rules": "Existing bot.py multi-strategy scorer; 4+ points OR 2 heavy conditions",
                        "market_context": f"regime={regime_data.get('regime', 'UNKNOWN')}; score={final_signal.get('score', 0)}",
                    },
                    entry_levels=entry_levels,
                )
                if was_flat and state.get("position") is not None:
                    state["daily_entry_count"] += 1
                if blocked_reason and was_flat:
                    st.error(blocked_reason)
                else:
                    st.info(result)
                paper_cols = st.columns(3)
                paper_cols[0].metric("Multi-strategy paper balance", f"${state['balance']:,.2f}")
                paper_cols[1].metric("Open position", "Yes" if state.get("position") else "No")
                paper_cols[2].metric("Closed trades", len(state.get("trades", [])))
                if state.get("position"):
                    st.json(state["position"])
                show_paper_journal(state, "multi_strategy")

    st.divider()
    render_multi_strategy_paper_monitor()
    render_multi_strategy_backtesting(symbol, timeframe)

elif strategy_choice == "AI Strategy":
    st.subheader("1 · Describe or speak a strategy")
    st.caption("Use everyday English, Hindi, or Hinglish. Voice works in browsers that support microphone speech recognition.")
    if "strategy_text" not in st.session_state:
        st.session_state.strategy_text = ""
    selected_example = st.session_state.pop("_selected_example", None)

    def select_example(example):
        st.session_state.strategy_text = example
        st.session_state._selected_example = example
        st.session_state.generated_strategy = None
        st.session_state.strategy_interpretation = None
        st.session_state.strategy_error = None
        st.session_state.pop("strategy_rules_json", None)
        st.session_state.pop("generated_code", None)

    cols = st.columns(3)
    for i, example in enumerate(EXAMPLES):
        cols[i].button(f"Example {i + 1}", width="stretch", on_click=select_example, args=(example,))
    voice_language = st.selectbox(
        "Voice language", ["Hinglish / Indian English", "Hindi", "English"],
        format_func=lambda value: value,
        key="strategy_voice_language",
    )
    voice_language_code = {
        "Hinglish / Indian English": "en-IN", "Hindi": "hi-IN", "English": "en-IN",
    }[voice_language]
    voice_reply = st.checkbox("Speak clarification and confirmation", key="strategy_voice_reply")
    prior_interpretation = st.session_state.get("strategy_interpretation")
    voice_prompt = ""
    if voice_reply and prior_interpretation:
        voice_prompt = prior_interpretation.clarification or prior_interpretation.summary
    transcript, voice_status = render_voice_strategy_controls(voice_language_code, voice_prompt)
    if transcript and transcript != st.session_state.get("last_strategy_transcript"):
        st.session_state.strategy_text = transcript
        st.session_state.last_strategy_transcript = transcript
        st.session_state.pop("generated_strategy", None)
        st.session_state.strategy_interpretation = None
        st.session_state.strategy_error = None
    if voice_status:
        st.caption(voice_status)
    text = st.text_area("Plain English strategy", key="strategy_text", max_chars=500, height=90,
                        placeholder="Try: BTC me EMA 20 50 ko cross kare to buy; RSI 30 ke neeche buy; previous high break ho to buy.")
    generate = st.button("Generate strategy", type="primary", width="stretch")
    backtest = False
    paper_run = False

    if generate or selected_example:
        try:
            interpreted = interpret_strategy(selected_example or text)
            st.session_state.strategy_interpretation = interpreted
            st.session_state.interpreted_strategy_text = selected_example or text
            st.session_state.generated_strategy = interpreted.specification
            st.session_state.strategy_error = None
            if interpreted.validation_message:
                st.session_state.strategy_error = interpreted.validation_message
        except Exception as exc:
            LOGGER.exception("Strategy interpretation failed")
            st.session_state.generated_strategy = None
            st.session_state.strategy_error = "I couldn't finish interpreting that strategy. Try adding the entry trigger in everyday language."
        if voice_reply:
            # Re-render the keyed speech component with the newly prepared
            # clarification/confirmation so it speaks immediately.
            st.rerun()

    strategy = st.session_state.get("generated_strategy")
    interpretation = st.session_state.get("strategy_interpretation")
    interpretation_stale = bool(
        interpretation and st.session_state.get("interpreted_strategy_text") is not None
        and text.strip() != st.session_state.interpreted_strategy_text.strip()
    )
    if interpretation_stale:
        strategy = None
        interpretation = None
        st.session_state.strategy_error = None
        st.info("The description changed. Generate strategy again to refresh and validate it before running.")
    if st.session_state.get("strategy_error") and not (interpretation and interpretation.validation_message):
        st.warning(st.session_state.strategy_error)
    if interpretation and interpretation.clarification:
        st.info(interpretation.clarification)
        if interpretation.suggestions:
            st.caption("Helpful confirmations: " + " · ".join(interpretation.suggestions))
        if "Use the complete existing ARJUNA strategy" in interpretation.suggestions:
            st.button("Open ARJUNA Strategy", key="open_ict_from_builder",
                      on_click=lambda: st.session_state.update({"strategy_choice": "ARJUNA Strategy"}))
        else:
            st.caption("Add your answer to the strategy description, then choose Generate strategy.")
    if interpretation and interpretation.draft:
        candidate = interpretation.draft
        st.subheader("Strategy confirmation")
        st.markdown(f"**Strategy understood:** {interpretation.summary}")
        if interpretation.validation_message:
            st.error("Safety validation: " + interpretation.validation_message)
        confirmation_cols = st.columns(4)
        confirmation_cols[0].metric("Risk", f"{candidate.get('risk_fraction', 0.01) * 100:g}%")
        confirmation_cols[1].metric("Target", f"{candidate.get('rr', 2.0):g}R")
        confirmation_cols[2].metric("Session", "24H")
        confirmation_cols[3].metric("Mode", "Paper Trading")
        st.caption("The fixed demo limit is 1% risk, minimum 1.5R, maximum 1x leverage. No live orders.")
    if strategy:
        st.subheader("Validated strategy rules")
        strategy_symbol = interpretation.symbol if interpretation and interpretation.symbol else symbol
        strategy_timeframe = interpretation.timeframe if interpretation and interpretation.timeframe else timeframe
        strategy_name = st.text_input("Strategy name", value=st.session_state.get("strategy_name", "My custom strategy"), key="strategy_name")
        encoded_rules = json.dumps(strategy, indent=2)
        if st.session_state.get("strategy_rules_source") != encoded_rules:
            st.session_state.strategy_rules_json = encoded_rules
            st.session_state.strategy_rules_source = encoded_rules
        edited_rules = st.text_area("Review and edit the strategy rules (JSON)", key="strategy_rules_json", height=190)
        edit_col, code_col, save_col = st.columns(3)
        apply_edits = edit_col.button("Validate", width="stretch")
        make_code = code_col.button("Generate code preview", width="stretch")
        save_version = save_col.button("Save strategy version", width="stretch")
        action_cols = st.columns(3)
        backtest = action_cols[0].button("Backtest", type="primary", width="stretch")
        paper_run = action_cols[1].button("Paper Trade", width="stretch")
        action_cols[2].button("Edit", width="stretch", on_click=lambda: st.session_state.update({"generated_strategy": None, "strategy_interpretation": None, "strategy_error": None, "interpreted_strategy_text": None}))
        if apply_edits:
            try:
                strategy = parse_strategy_json(edited_rules)
                st.session_state.generated_strategy = strategy
                st.session_state.strategy_rules_source = json.dumps(strategy, indent=2)
                if interpretation:
                    interpretation.specification = strategy
                    interpretation.draft = strategy
                    interpretation.summary = summarize_strategy(strategy, strategy_symbol, strategy_timeframe)
                    interpretation.clarification = None
                    interpretation.validation_message = None
                st.success("Edited rules validated. They are ready for backtest or paper trading.")
            except StrategyError as exc:
                st.warning(str(exc))
        if make_code:
            try:
                st.session_state.generated_code = generate_strategy_code(parse_strategy_json(edited_rules))
            except StrategyError as exc:
                st.warning(str(exc))
        if save_version:
            try:
                checked = parse_strategy_json(edited_rules)
                st.session_state.generated_strategy = checked
                st.session_state.strategy_rules_source = json.dumps(checked, indent=2)
                if interpretation:
                    interpretation.specification = checked
                    interpretation.draft = checked
                    interpretation.summary = summarize_strategy(checked, strategy_symbol, strategy_timeframe)
                    interpretation.clarification = None
                    interpretation.validation_message = None
                versions = st.session_state.setdefault("strategy_library", [])
                matching = [item for item in versions if item["name"] == strategy_name and item["market"] == strategy_symbol and item["timeframe"] == strategy_timeframe]
                version = len(matching) + 1
                versions.append({"name": strategy_name.strip() or "Custom strategy", "market": strategy_symbol, "timeframe": strategy_timeframe,
                                 "version": version, "saved_at": datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M IST"), "spec": checked})
                st.success(f"Saved {strategy_name} v{version} in this browser session.")
            except StrategyError as exc:
                st.warning(str(exc))
        if st.session_state.get("generated_code"):
            st.code(st.session_state.generated_code, language="python")
        st.json(strategy, expanded=True)
        st.success("Strategy validated. Fixed demo risk limits apply to all runs.")
        if backtest or paper_run:
            try:
                strategy = parse_strategy_json(edited_rules)
                st.session_state.generated_strategy = strategy
                assert_demo_mode()
                with st.spinner("Loading public candles and simulating..."):
                    data_result = load_market_data(strategy_symbol, strategy_timeframe, 1000)
                show_market_data_status(data_result, strategy_symbol, strategy_timeframe)
                data = data_result.frame
                if backtest:
                    metrics, equity, trades = run_backtest(data, strategy, starting_capital=float(capital))
                    st.subheader("3 · Backtest performance")
                    metric_cols = st.columns(5)
                    for col, name in zip(metric_cols, ["Total return %", "Total P&L", "Maximum drawdown %", "Win rate %", "Number of trades"]):
                        value = metrics[name]
                        label = f"{value:.2f}%" if name.endswith("%") else f"${value:,.2f}" if name == "Total P&L" else str(value)
                        col.metric(name, label)
                    st.caption(f"Starting: ${metrics['Starting capital']:,.2f} · Ending: ${metrics['Ending capital']:,.2f} · Wins: {metrics['Wins']} · Losses: {metrics['Losses']} · Average trade: ${metrics['Average trade']:,.2f} · Profit factor: {metrics['Profit factor']:.2f}")
                    show_additional_metrics(metrics)
                    st.subheader("Equity curve")
                    st.line_chart(equity["equity"], height=270)
                    st.subheader("Trade history")
                    st.dataframe(trades, width="stretch", hide_index=True)
                    st.caption("Assumptions: signals use finalized candles; fills occur at the next candle open with 0.01% slippage and 0.04% fees. If stop and target fall within one candle, stop is assumed first. Open positions are marked to market and closed at the end of the sample.")
                if paper_run:
                    state = get_paper_account("custom.dsl", capital, strategy_symbol, strategy_timeframe)
                    context = {"strategy_id": "custom.dsl", "strategy_version": "1.0.0", "market": strategy_symbol,
                               "timeframe": strategy_timeframe, "data_source": data_result.source,
                               "entry_rules": json.dumps(strategy["entry"], sort_keys=True),
                               "market_context": f"close={float(data.close.iloc[-1]):.8g}"}
                    result = advance_paper_account(data, strategy, state, journal_context=context)
                    st.subheader("PAPER TRADING")
                    st.info(result)
                    p1, p2, p3 = st.columns(3)
                    p1.metric("Paper balance", f"${state['balance']:,.2f}")
                    p2.metric("Open position", "Yes" if state["position"] else "No")
                    p3.metric("Closed trades", len(state["trades"]))
                    if state["position"]:
                        st.json(state["position"])
                    show_paper_journal(state, "custom")
            except (MarketDataError, StrategyError, SafetyError, ValueError) as exc:
                st.warning(str(exc))
            except Exception:
                LOGGER.exception("Public demo operation failed")
                st.error("The demo could not complete that operation. Please try again.")
elif strategy_choice == "ARJUNA Strategy":
    st.subheader("ARJUNA Strategy")
    st.write("Uses the project's existing liquidity sweep, displacement, market structure shift, fair value gap, order block, and higher-timeframe bias logic.")
    st.info("Valid ARJUNA setups are evaluated 24 hours a day. The configured news blackout, cooldown, and fixed risk limits remain active.")
    current_ist = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST")
    st.caption(f"Current time: {current_ist}")
    st.caption("This runs ARJUNA independently. Select Multi-Strategy Engine to score ARJUNA alongside the other existing strategies.")
    ict_buttons = st.columns(3)
    run_ict = ict_buttons[0].button("Check latest ARJUNA signal", type="primary", width="stretch")
    backtest_ict = ict_buttons[1].button("Backtest ARJUNA", width="stretch")
    paper_ict = ict_buttons[2].button("Run ARJUNA paper trading", width="stretch")
    if run_ict or backtest_ict or paper_ict:
        try:
            from config import NEWS_BLACKOUT, NEWS_FILTER_ENABLED
            blackout = NEWS_FILTER_ENABLED and NEWS_BLACKOUT
            data_result = load_market_data(symbol, timeframe, 1000 if backtest_ict else 500)
            show_market_data_status(data_result, symbol, timeframe)
            data = data_result.frame
            if run_ict:
                from platform_core.ict import analyze_ict
                from platform_core.errors import PlatformError
                permitted, reason = ict_entry_gate(data.index[-1], news_blackout=blackout)
                if not permitted:
                    st.info(f"ARJUNA entry gate: {reason}")
                else:
                    try:
                        analysis = analyze_ict(data, rr=float(DEFAULT_RR_UI)).to_dict()
                    except PlatformError as exc:
                        render_status(exc.status, exc.message)
                        analysis = None
                    if analysis is not None:
                        st.session_state.ict_analysis = analysis
                        render_ict_stages(analysis)
                        if analysis["has_setup"]:
                            st.success(
                                f"Valid ARJUNA setup: {analysis['side']} · score {analysis['score']} "
                                f"· entry {analysis['entry']:.2f} · stop {analysis['stop']:.2f} "
                                f"· target {analysis['target']:.2f} · R:R {analysis['rr']:.2f}"
                            )
                        else:
                            st.info(
                                "No valid ARJUNA setup on the latest candle. "
                                f"Closest score {max(analysis['long_score'], analysis['short_score'])} "
                                "of 60 required."
                            )
                        with st.expander("Full ARJUNA analysis"):
                            st.json(analysis)
            if backtest_ict:
                metrics, equity, trades = run_ict_backtest(data, starting_capital=float(capital), news_blackout=blackout)
                st.subheader("ARJUNA backtest performance")
                metric_cols = st.columns(5)
                for col, name in zip(metric_cols, ["Total return %", "Total P&L", "Maximum drawdown %", "Win rate %", "Number of trades"]):
                    value = metrics[name]
                    label = f"{value:.2f}%" if name.endswith("%") else f"${value:,.2f}" if name == "Total P&L" else str(value)
                    col.metric(name, label)
                st.line_chart(equity["equity"], height=270)
                st.dataframe(trades, width="stretch", hide_index=True)
                show_additional_metrics(metrics)
                st.caption("Finalized-candle ARJUNA signals fill at the next candle open. 24-hour evaluation, news blackout, cooldown, fixed 1% risk, default 2R target (minimum 1.5R) and 1x notional cap apply.")
            if paper_ict:
                state = get_paper_account("ict", capital, symbol, timeframe)
                st.subheader("ARJUNA PAPER TRADING")
                ict_context = {"strategy_id": "ict", "strategy_version": "1.0.0", "market": symbol,
                               "timeframe": timeframe, "data_source": data_result.source,
                               "entry_rules": "Existing ARJUNA signal implementation",
                               "market_context": f"close={float(data.close.iloc[-1]):.8g}"}
                st.info(advance_ict_paper_account(data, state, news_blackout=blackout, journal_context=ict_context))
                c1, c2, c3 = st.columns(3)
                c1.metric("Paper balance", f"${state['balance']:,.2f}")
                c2.metric("Open position", "Yes" if state["position"] else "No")
                c3.metric("Closed trades", len(state["trades"]))
                st.dataframe(pd.DataFrame(state["trades"]), width="stretch", hide_index=True)
        except MarketDataError as exc:
            LOGGER.warning("ARJUNA market-data/signal request could not proceed: %s", exc)
            st.warning(str(exc))
        except Exception as exc:
            LOGGER.exception("ARJUNA signal failed")
            st.error(
                f"ARJUNA calculation failed ({type(exc).__name__}). "
                "Details are recorded in private app logs; no stack trace or credentials are shown here."
            )
else:
    st.subheader("Quant Strategy")
    st.write("Quant plugins share the platform's normalized market data, risk limits, backtest, and session-scoped paper account.")
    plugin_id = st.selectbox("Quant family", ["quant.trend", "quant.mean_reversion"],
                             format_func=lambda value: strategy_registry.get(value).metadata.name)
    parameters = {"side": "BUY"}
    if plugin_id == "quant.trend":
        q1, q2 = st.columns(2)
        parameters["fast"] = q1.number_input("Fast EMA", min_value=2, max_value=100, value=20)
        parameters["slow"] = q2.number_input("Slow EMA", min_value=3, max_value=200, value=50)
        if parameters["fast"] >= parameters["slow"]:
            st.warning("Fast EMA must be shorter than slow EMA.")
    else:
        q1, q2, q3 = st.columns(3)
        parameters["period"] = q1.number_input("RSI period", min_value=2, max_value=100, value=14)
        parameters["oversold"] = q2.number_input("Oversold threshold", min_value=1, max_value=49, value=30)
        parameters["overbought"] = q3.number_input("Overbought threshold", min_value=51, max_value=99, value=70)
    quant_buttons = st.columns(3)
    check_quant = quant_buttons[0].button("Check latest signal", type="primary", width="stretch")
    backtest_quant = quant_buttons[1].button("Backtest Quant", width="stretch")
    paper_quant = quant_buttons[2].button("Run Quant paper trading", width="stretch")
    if check_quant or backtest_quant or paper_quant:
        try:
            if plugin_id == "quant.trend" and parameters["fast"] >= parameters["slow"]:
                raise ValueError("Fast EMA must be shorter than slow EMA.")
            data_result = load_market_data(symbol, timeframe, 1000 if backtest_quant else 500)
            show_market_data_status(data_result, symbol, timeframe)
            data = data_result.frame
            plugin = strategy_registry.get(plugin_id)
            signals = plugin.signal_series(data, parameters)
            side = signals.iloc[-1]
            st.subheader("Latest Quant signal")
            st.write(side if side in {"BUY", "SELL"} else "No valid Quant signal on the latest finalized candle.")
            if backtest_quant:
                from demo_strategy import validate_strategy
                neutral_rules = validate_strategy({
                    "side": "BUY",
                    "entry": [{"indicator": "price", "operator": ">", "value": 0}],
                    "exit": [{"indicator": "price", "operator": "<", "value": 0}],
                })
                metrics, equity, trades = run_backtest(data, neutral_rules, starting_capital=float(capital), signal_sides=signals)
                st.subheader("Quant backtest · historical simulation")
                metric_cols = st.columns(5)
                for col, name in zip(metric_cols, ["Total return %", "Total P&L", "Maximum drawdown %", "Win rate %", "Number of trades"]):
                    value = metrics[name]
                    label = f"{value:.2f}%" if name.endswith("%") else f"${value:,.2f}" if name == "Total P&L" else str(value)
                    col.metric(name, label)
                st.line_chart(equity["equity"], height=270)
                st.dataframe(trades, width="stretch", hide_index=True)
                show_additional_metrics(metrics)
                st.caption("Signals use finalized candles and next-candle-open fills, 0.01% slippage, 0.04% fees, 1% risk, default 2R target (minimum 1.5R), 1x notional cap, 30-minute cooldown, and conservative stop-first intrabar handling.")
            if paper_quant:
                state = get_paper_account(plugin_id, capital, symbol, timeframe)
                quant_context = {"strategy_id": plugin_id, "strategy_version": plugin.metadata.version, "market": symbol,
                                 "timeframe": timeframe, "data_source": data_result.source,
                                 "entry_rules": json.dumps(parameters, sort_keys=True),
                                 "market_context": f"close={float(data.close.iloc[-1]):.8g}"}
                st.subheader("QUANT PAPER TRADING")
                st.info(advance_paper_account(data, {"side": "BUY", "entry": [], "exit": [], "risk_fraction": .01, "rr": 2., "leverage": 1.}, state, signal_sides=signals, journal_context=quant_context))
                p1, p2, p3 = st.columns(3)
                p1.metric("Paper balance", f"${state['balance']:,.2f}")
                p2.metric("Open position", "Yes" if state["position"] else "No")
                p3.metric("Closed trades", len(state["trades"]))
                if state["position"]:
                    st.json(state["position"])
                show_paper_journal(state, plugin_id.replace(".", "_"))
        except (MarketDataError, StrategyError, SafetyError, ValueError) as exc:
            st.warning(str(exc))
        except Exception:
            LOGGER.exception("Quant strategy operation failed")
            st.error("Quant strategy could not complete that operation. Please try again.")

# Keep the same keyed chart mounted on every rerun after its first data load.
# Changing symbol/timeframe takes effect when the user requests a data action;
# the caption makes a previously loaded chart's identity explicit meanwhile.
chart_snapshot = st.session_state.get("chart_snapshot")
if chart_snapshot:
    with chart_slot:
        st.subheader("Market chart")
        loaded_symbol = chart_snapshot["symbol"]
        loaded_timeframe = chart_snapshot["timeframe"]
        st.caption(f"{loaded_symbol} · {loaded_timeframe} · {chart_snapshot['source']} · finalized candles")
        if (loaded_symbol, loaded_timeframe) != (symbol, timeframe):
            st.caption("Showing the last loaded market; run a strategy action to load the selected market.")
        if "chart_fit_request" not in st.session_state:
            st.session_state.chart_fit_request = 0
        if st.button("Fit chart", key="fit_market_chart"):
            st.session_state.chart_fit_request += 1
        chart_frame = chart_snapshot["frame"]
        chart_source = chart_snapshot["source"]
        # Overlays are computed from these exact candles, so every line and
        # level on the chart traces back to real market data.
        chart_overlays = build_overlay_series(chart_frame, render_indicator_controls())
        chart_ict = build_ict_overlay(st.session_state.get("ict_analysis"),
                                      render_ict_overlay_toggles())
        chart_live = not chart_source.startswith("Bundled")
        render_data_health(assess_health(chart_frame, loaded_timeframe, chart_live)[0],
                           chart_source, is_live=chart_live, candles=len(chart_frame))
        render_ohlcv_chart(
            chart_frame,
            title=f"{loaded_symbol} · {loaded_timeframe}",
            dataset_id=f"{loaded_symbol}|{loaded_timeframe}",
            reset_id=st.session_state.chart_fit_request,
            overlays=chart_overlays,
            ict=chart_ict,
        )

st.divider()
render_portfolio_dashboard()
st.divider()
render_markets_screen()

paper_accounts = st.session_state.get("paper_accounts", {})
if paper_accounts:
    if strategy_choice == "Multi-Strategy Engine":
        auth_key = "multi.strategy|BTC/USDT|5m"
        auth_account = paper_accounts.get(auth_key)
        portfolio_accounts = [auth_account] if auth_account else []
        caption_text = "Showing the authoritative BTC/USDT 5m multi-strategy account only."
    elif strategy_choice == "ARJUNA Strategy":
        auth_key = f"ict|{symbol}|{timeframe}"
        auth_account = paper_accounts.get(auth_key)
        portfolio_accounts = [auth_account] if auth_account else []
        caption_text = f"Showing the active ARJUNA Strategy account ({auth_key})."
    elif strategy_choice == "AI Strategy":
        strategy_info = st.session_state.get("strategy_interpretation")
        strat_sym = strategy_info.symbol if strategy_info and strategy_info.symbol else symbol
        strat_tf = strategy_info.timeframe if strategy_info and strategy_info.timeframe else timeframe
        auth_key = f"custom.dsl|{strat_sym}|{strat_tf}"
        auth_account = paper_accounts.get(auth_key)
        portfolio_accounts = [auth_account] if auth_account else []
        caption_text = f"Showing the active AI Strategy account ({auth_key})."
    elif strategy_choice == "Quant Strategy":
        q_plugin_id = plugin_id if "plugin_id" in locals() else "quant.trend"
        auth_key = f"{q_plugin_id}|{symbol}|{timeframe}"
        auth_account = paper_accounts.get(auth_key)
        portfolio_accounts = [auth_account] if auth_account else []
        caption_text = f"Showing the active Quant Strategy account ({auth_key})."
    else:
        portfolio_accounts = []
        caption_text = ""

    if portfolio_accounts:
        snapshot = portfolio_snapshot(portfolio_accounts)
        st.divider()
        st.subheader("Session portfolio · PAPER TRADING")
        st.caption(caption_text)
        portfolio_cols = st.columns(5)
        portfolio_cols[0].metric("Cash balance", f"${snapshot['balance']:,.2f}")
        portfolio_cols[1].metric("Equity", f"${snapshot['equity']:,.2f}")
        portfolio_cols[2].metric("Realized P&L", f"${snapshot['realized_pnl']:,.2f}")
        portfolio_cols[3].metric("Unrealized P&L", f"${snapshot['unrealized_pnl']:,.2f}")
        portfolio_cols[4].metric("Open positions", snapshot["positions"])
        st.caption(f"Closed paper trades: {snapshot['closed_trades']} · Win rate: {snapshot['win_rate_pct']:.1f}%" if snapshot["win_rate_pct"] is not None else f"Closed paper trades: {snapshot['closed_trades']} · Win rate: N/A")
        if snapshot["trade_journal"]:
            st.subheader("Combined trade journal")
            st.dataframe(pd.DataFrame(snapshot["trade_journal"]), width="stretch", hide_index=True)

st.divider()
st.caption("AI turns natural-language trading ideas into structured, testable strategies. Historical simulation is not a prediction of future results.")

