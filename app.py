"""Streamlit front end for the public, simulation-only trading demo."""

import logging
import os

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from demo_backtest import run_backtest, run_ict_backtest
from demo_data import MarketDataError, MarketDataResult, fetch_market_data
from demo_paper import advance_paper_account, advance_ict_paper_account
from demo_safety import DEMO_MODE, LIVE_ORDERS_ENABLED, SafetyError, assert_demo_mode, ict_entry_gate
from demo_strategy import EXAMPLES, StrategyError, parse_strategy, validate_strategy

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("ict_demo")
load_dotenv()
try:
    if not os.getenv("OPENAI_API_KEY") and st.secrets.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = st.secrets["OPENAI_API_KEY"]
    if not os.getenv("OPENAI_MODEL") and st.secrets.get("OPENAI_MODEL"):
        os.environ["OPENAI_MODEL"] = st.secrets["OPENAI_MODEL"]
except Exception:
    # Streamlit secrets are optional for local runs.
    pass

st.set_page_config(page_title="AI Algo Trading Demo", page_icon="📈", layout="wide")
st.title("AI Algo Trading Demo")
st.write("Describe a trading strategy in plain English. AI converts it into structured rules, then you can backtest and paper trade it.")
st.error("DEMO MODE: ON   ·   LIVE ORDERS: DISABLED\n\nPaper trading only. Not investment advice.")
st.caption("Natural language → restricted strategy rules → backtest → paper trading. No real money or broker order access.")


@st.cache_data(ttl=60, show_spinner=False)
def load_market_data(selected_symbol, selected_timeframe, candle_limit):
    return fetch_market_data(selected_symbol, selected_timeframe, limit=candle_limit)


def show_market_data_status(result: MarketDataResult) -> None:
    st.caption(f"Data source: {result.source} · {len(result.frame)} finalized candles · UTC")
    if result.used_fallback:
        st.info("Using backup data source")

if not DEMO_MODE or LIVE_ORDERS_ENABLED:
    st.error("Safety configuration failed. Execution is disabled.")
    st.stop()

with st.sidebar:
    st.subheader("Simulation settings")
    symbol = st.selectbox("Market", ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    timeframe = st.selectbox("Candle interval", ["1h", "15m", "5m"])
    capital = st.number_input("Starting capital (simulation)", min_value=1000, max_value=1000000, value=10000, step=1000)
    st.caption("Fixed safety limits: 1% max risk · 2.0 minimum R:R · 1x max leverage · 30 minute cooldown")

strategy_choice = st.radio("Strategy", ["AI Strategy", "Existing ICT Strategy"], horizontal=True)

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

    cols = st.columns(3)
    for i, example in enumerate(EXAMPLES):
        cols[i].button(f"Example {i + 1}", width="stretch", on_click=select_example, args=(example,))
    text = st.text_area("Plain English strategy", key="strategy_text", max_chars=500, height=90,
                        placeholder="Example: Buy when RSI(14) is below 30 and exit when RSI(14) goes above 70.")
    gen_col, bt_col, paper_col = st.columns([1, 1, 1])
    generate = gen_col.button("Generate structured rules", type="primary", width="stretch")
    backtest = bt_col.button("Backtest", width="stretch")
    paper_run = paper_col.button("Run paper trading", width="stretch")

    if generate or backtest or paper_run or selected_example:
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
        st.json(strategy, expanded=True)
        st.success("Strategy validated. Fixed demo risk limits apply to all runs.")
        if backtest or paper_run:
            try:
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
                    st.subheader("Equity curve")
                    st.line_chart(equity["equity"], height=270)
                    st.subheader("Trade history")
                    st.dataframe(trades, width="stretch", hide_index=True)
                    st.caption("Assumptions: signals use finalized candles; fills occur at the next candle open with 0.01% slippage and 0.04% fees. If stop and target fall within one candle, stop is assumed first. Open positions are marked to market and closed at the end of the sample.")
                if paper_run:
                    state = st.session_state.setdefault("paper_account", {"balance": float(capital), "position": None, "trades": [], "last_action": None})
                    if state.get("starting_capital") != float(capital):
                        state.clear()
                        state.update({"balance": float(capital), "starting_capital": float(capital), "position": None, "trades": [], "last_action": None})
                    result = advance_paper_account(data, strategy, state)
                    st.subheader("PAPER TRADING")
                    st.info(result)
                    p1, p2, p3 = st.columns(3)
                    p1.metric("Paper balance", f"${state['balance']:,.2f}")
                    p2.metric("Open position", "Yes" if state["position"] else "No")
                    p3.metric("Closed trades", len(state["trades"]))
                    if state["position"]:
                        st.json(state["position"])
                    st.dataframe(pd.DataFrame(state["trades"]), width="stretch", hide_index=True)
            except (MarketDataError, StrategyError, SafetyError, ValueError) as exc:
                st.warning(str(exc))
            except Exception:
                LOGGER.exception("Public demo operation failed")
                st.error("The demo could not complete that operation. Please try again.")
else:
    st.subheader("Existing ICT Strategy")
    st.write("Uses the project's existing liquidity sweep, displacement, market structure shift, fair value gap, order block, and higher-timeframe bias logic.")
    st.info("ICT entries are restricted to the London (07:00–10:00 UTC) and New York (12:00–15:00 UTC) windows. The configured news blackout blocks entries. Cooldown and fixed risk limits remain active.")
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
                st.caption("Finalized-candle ICT signals fill at the next candle open. Kill zones, news blackout, cooldown, fixed 1% risk, 2R target and 1x notional cap apply.")
            if paper_ict:
                state = st.session_state.setdefault("ict_paper_account", {"balance": float(capital), "position": None, "trades": [], "last_action": None})
                if state.get("starting_capital") != float(capital):
                    state.clear()
                    state.update({"balance": float(capital), "starting_capital": float(capital), "position": None, "trades": [], "last_action": None})
                st.subheader("ICT PAPER TRADING")
                st.info(advance_ict_paper_account(data, state, news_blackout=blackout))
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

st.divider()
st.caption("AI turns natural-language trading ideas into structured, testable strategies. Historical simulation is not a prediction of future results.")
