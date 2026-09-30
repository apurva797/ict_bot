"""Streamlit front end for the public, simulation-only trading demo."""

import logging
import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from demo_backtest import run_backtest, run_ict_backtest
from demo_data import MarketDataError, MarketDataResult, fetch_market_data
from demo_paper import advance_paper_account, advance_ict_paper_account
from demo_safety import DEMO_MODE, LIVE_ORDERS_ENABLED, SafetyError, assert_demo_mode, ict_entry_gate
from demo_strategy import EXAMPLES, StrategyError, generate_strategy_code, parse_strategy, parse_strategy_json, validate_strategy
from platform_strategies import strategy_registry
from charting import render_ohlcv_chart
from portfolio import portfolio_snapshot

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("ict_demo")
load_dotenv()
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
st.title("AI Algo Trading Demo")
st.write("Describe a trading strategy in plain English. Gemini can interpret it into editable structured rules; the deterministic strategy engine validates, backtests, and paper trades those rules.")
st.error("DEMO MODE: ON   ·   LIVE ORDERS: DISABLED\n\nPaper trading only. Not investment advice.")
st.caption("Natural language → restricted strategy rules → backtest → paper trading. No real money or broker order access.")


@st.cache_data(ttl=60, show_spinner=False)
def load_market_data(selected_symbol, selected_timeframe, candle_limit):
    return fetch_market_data(selected_symbol, selected_timeframe, limit=candle_limit)


def show_market_data_status(result: MarketDataResult) -> None:
    st.caption(f"Data source: {result.source} · {len(result.frame)} finalized candles · UTC")
    if result.used_fallback:
        st.info("Using backup data source")
    render_ohlcv_chart(result.frame.tail(400), title=f"{symbol} · {timeframe}")


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
    state.update({"strategy_id": strategy_id, "market": market, "timeframe": interval})
    return state


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
    timeframe = st.selectbox("Candle interval", ["1h", "15m", "5m"])
    capital = st.number_input("Starting capital (simulation)", min_value=1000, max_value=1000000, value=10000, step=1000)
    st.caption("Fixed safety limits: 1% max risk · 2.0 minimum R:R · 1x max leverage · 30 minute cooldown")

strategy_choice = st.radio("Strategy", ["AI Strategy", "Existing ICT Strategy", "Quant Strategy"], horizontal=True)

with st.expander("Strategy library · built-in plugins"):
    st.caption("Session-only strategy catalog. User accounts and persistent cloud storage are not configured in this public demo.")
    for item in strategy_registry.list():
        st.markdown(f"**{item.name}** · `{item.id}` · v{item.version} · {item.category} — {item.description}")
    for item in st.session_state.get("strategy_library", []):
        st.markdown(f"**{item['name']}** · `{item['market']} {item['timeframe']}` · v{item['version']} · saved {item['saved_at']}")

if strategy_choice == "AI Strategy":
    st.subheader("1 · Describe a strategy")
    if "strategy_text" not in st.session_state:
        st.session_state.strategy_text = ""
    selected_example = st.session_state.pop("_selected_example", None)

    def select_example(example):
        st.session_state.strategy_text = example
        st.session_state._selected_example = example
        st.session_state.generated_strategy = None
        st.session_state.strategy_error = None
        st.session_state.pop("strategy_rules_json", None)
        st.session_state.pop("generated_code", None)

    cols = st.columns(3)
    for i, example in enumerate(EXAMPLES):
        cols[i].button(f"Example {i + 1}", width="stretch", on_click=select_example, args=(example,))
    text = st.text_area("Plain English strategy", key="strategy_text", max_chars=500, height=90,
                        placeholder="Example: Buy when RSI(14) is below 30 and exit when RSI(14) goes above 70.")
    gen_col, bt_col, paper_col = st.columns([1, 1, 1])
    generate = gen_col.button("Generate structured rules", type="primary", width="stretch")
    backtest = bt_col.button("Backtest", width="stretch")
    paper_run = paper_col.button("Run paper trading", width="stretch")

    if generate or selected_example or ((backtest or paper_run) and not st.session_state.get("generated_strategy")):
        try:
            st.session_state.generated_strategy = parse_strategy(selected_example or text)
            st.session_state.strategy_error = None
            if selected_example:
                backtest = True
        except StrategyError as exc:
            LOGGER.info("Strategy validation rejected input: %s", exc)
            st.session_state.generated_strategy = None
            st.session_state.strategy_error = str(exc)

    strategy = st.session_state.get("generated_strategy")
    if st.session_state.get("strategy_error"):
        st.warning(st.session_state.strategy_error)
    if strategy:
        st.subheader("2 · Parsed strategy")
        strategy_name = st.text_input("Strategy name", value=st.session_state.get("strategy_name", "My custom strategy"), key="strategy_name")
        encoded_rules = json.dumps(strategy, indent=2)
        if st.session_state.get("strategy_rules_source") != encoded_rules:
            st.session_state.strategy_rules_json = encoded_rules
            st.session_state.strategy_rules_source = encoded_rules
        edited_rules = st.text_area("Review and edit the strategy rules (JSON)", key="strategy_rules_json", height=190)
        edit_col, code_col, save_col = st.columns(3)
        apply_edits = edit_col.button("Validate edited rules", width="stretch")
        make_code = code_col.button("Generate code preview", width="stretch")
        save_version = save_col.button("Save strategy version", width="stretch")
        if apply_edits:
            try:
                strategy = parse_strategy_json(edited_rules)
                st.session_state.generated_strategy = strategy
                st.session_state.strategy_rules_source = json.dumps(strategy, indent=2)
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
                versions = st.session_state.setdefault("strategy_library", [])
                matching = [item for item in versions if item["name"] == strategy_name and item["market"] == symbol and item["timeframe"] == timeframe]
                version = len(matching) + 1
                versions.append({"name": strategy_name.strip() or "Custom strategy", "market": symbol, "timeframe": timeframe,
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
                    data_result = load_market_data(symbol, timeframe, 1000)
                show_market_data_status(data_result)
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
                    state = get_paper_account("custom.dsl", capital, symbol, timeframe)
                    context = {"strategy_id": "custom.dsl", "strategy_version": "1.0.0", "market": symbol,
                               "timeframe": timeframe, "data_source": data_result.source,
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
elif strategy_choice == "Existing ICT Strategy":
    st.subheader("Existing ICT Strategy")
    st.write("Uses the project's existing liquidity sweep, displacement, market structure shift, fair value gap, order block, and higher-timeframe bias logic.")
    st.info("Valid ICT setups are evaluated 24 hours a day. The configured news blackout, cooldown, and fixed risk limits remain active.")
    current_ist = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST")
    st.caption(f"Current time: {current_ist}")
    st.caption("This preserves the existing ICT signal implementation. Its original command-line multi-strategy engine is not executed by the public demo.")
    ict_buttons = st.columns(3)
    run_ict = ict_buttons[0].button("Check latest ICT signal", type="primary", width="stretch")
    backtest_ict = ict_buttons[1].button("Backtest ICT", width="stretch")
    paper_ict = ict_buttons[2].button("Run ICT paper trading", width="stretch")
    if run_ict or backtest_ict or paper_ict:
        try:
            from config import NEWS_BLACKOUT, NEWS_FILTER_ENABLED
            blackout = NEWS_FILTER_ENABLED and NEWS_BLACKOUT
            data_result = load_market_data(symbol, timeframe, 1000 if backtest_ict else 500)
            show_market_data_status(data_result)
            data = data_result.frame
            if run_ict:
                from strategies.ict import ict_signal
                permitted, reason = ict_entry_gate(data.index[-1], news_blackout=blackout)
                if not permitted:
                    st.info(f"No ICT execution: {reason}")
                else:
                    candles = [[int(ts.timestamp() * 1000), r.open, r.high, r.low, r.close, r.volume] for ts, r in data.iterrows()]
                    st.json(ict_signal(candles))
            if backtest_ict:
                metrics, equity, trades = run_ict_backtest(data, starting_capital=float(capital), news_blackout=blackout)
                st.subheader("ICT backtest performance")
                metric_cols = st.columns(5)
                for col, name in zip(metric_cols, ["Total return %", "Total P&L", "Maximum drawdown %", "Win rate %", "Number of trades"]):
                    value = metrics[name]
                    label = f"{value:.2f}%" if name.endswith("%") else f"${value:,.2f}" if name == "Total P&L" else str(value)
                    col.metric(name, label)
                st.line_chart(equity["equity"], height=270)
                st.dataframe(trades, width="stretch", hide_index=True)
                show_additional_metrics(metrics)
                st.caption("Finalized-candle ICT signals fill at the next candle open. 24-hour evaluation, news blackout, cooldown, fixed 1% risk, 2R target and 1x notional cap apply.")
            if paper_ict:
                state = get_paper_account("ict", capital, symbol, timeframe)
                st.subheader("ICT PAPER TRADING")
                ict_context = {"strategy_id": "ict", "strategy_version": "1.0.0", "market": symbol,
                               "timeframe": timeframe, "data_source": data_result.source,
                               "entry_rules": "Existing ICT signal implementation",
                               "market_context": f"close={float(data.close.iloc[-1]):.8g}"}
                st.info(advance_ict_paper_account(data, state, news_blackout=blackout, journal_context=ict_context))
                c1, c2, c3 = st.columns(3)
                c1.metric("Paper balance", f"${state['balance']:,.2f}")
                c2.metric("Open position", "Yes" if state["position"] else "No")
                c3.metric("Closed trades", len(state["trades"]))
                st.dataframe(pd.DataFrame(state["trades"]), width="stretch", hide_index=True)
        except MarketDataError as exc:
            LOGGER.warning("ICT market-data/signal request could not proceed: %s", exc)
            st.warning(str(exc))
        except Exception as exc:
            LOGGER.exception("ICT signal failed")
            st.error(
                f"ICT calculation failed ({type(exc).__name__}). "
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
            show_market_data_status(data_result)
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
                st.caption("Signals use finalized candles and next-candle-open fills, 0.01% slippage, 0.04% fees, 1% risk, 2R target, 1x notional cap, 30-minute cooldown, and conservative stop-first intrabar handling.")
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

paper_accounts = st.session_state.get("paper_accounts", {})
if paper_accounts:
    snapshot = portfolio_snapshot(paper_accounts.values())
    st.divider()
    st.subheader("Session portfolio · PAPER TRADING")
    st.caption("Aggregated from this browser session's isolated strategy / market / timeframe accounts.")
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

