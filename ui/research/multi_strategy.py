"""Aggregate Multi-Strategy historical research surface."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from multi_strategy_backtest import run_multi_strategy_backtest
from ui import components as ui
from ui import marketdata


RESULT_KEY = "research_multi_strategy_backtest"


def render() -> None:
    """Render the aggregate simulator without touching paper accounts."""
    ui.html_block(ui.section_head(
        "Multi-Strategy backtest",
        "Aggregate scorer and confirmation over historical candles",
    ))
    st.caption(
        "This is a historical simulation only. Signals use candles through T "
        "and fill at T+1 open; no paper account or order state is changed."
    )

    from bot import multi_strategy_names

    names = tuple(multi_strategy_names())
    display_name = lambda value: "ARJUNA" if value == "ICT" else value
    with st.form("research_multi_strategy_backtest_form"):
        first, second = st.columns(2)
        symbol = first.selectbox(
            "Symbol", list(marketdata.watchlist()), key="research_multi_symbol"
        )
        timeframe = second.selectbox(
            "Timeframe", list(marketdata.TIMEFRAMES), key="research_multi_timeframe"
        )
        selected = st.multiselect(
            "Strategies to aggregate",
            list(names),
            default=list(names),
            format_func=display_name,
            key="research_multi_strategies",
        )
        period_start, period_end = st.columns(2)
        default_end = datetime.now(timezone.utc).date()
        start_date = period_start.date_input(
            "Backtest start", value=default_end - timedelta(days=365),
            key="research_multi_start",
        )
        end_date = period_end.date_input(
            "Backtest end", value=default_end,
            key="research_multi_end",
        )
        capital, risk, max_open = st.columns(3)
        starting_capital = capital.number_input(
            "Initial capital", min_value=100.0, value=10_000.0,
            step=500.0, key="research_multi_capital",
        )
        risk_percent = risk.number_input(
            "Risk per trade (%)", min_value=0.1, max_value=1.0, value=1.0,
            step=0.1, key="research_multi_risk",
        )
        max_concurrent = max_open.number_input(
            "Max concurrent trades", min_value=1, max_value=10, value=1,
            step=1, key="research_multi_max_open",
        )
        minimum_rr, target_rr = st.columns(2)
        min_rr = minimum_rr.number_input(
            "Minimum R:R", min_value=1.5, max_value=10.0, value=1.5,
            step=0.1, key="research_multi_min_rr",
        )
        target = target_rr.number_input(
            "Target R:R", min_value=1.5, max_value=10.0, value=2.0,
            step=0.1, key="research_multi_target_rr",
        )
        fee_col, slippage_col, compare_col = st.columns(3)
        fee_rate = fee_col.number_input(
            "Fee rate (%)", min_value=0.0, max_value=1.0, value=0.04,
            step=0.01, key="research_multi_fee",
        )
        slippage_rate = slippage_col.number_input(
            "Slippage rate (%)", min_value=0.0, max_value=1.0, value=0.01,
            step=0.01, key="research_multi_slippage",
        )
        compare = compare_col.checkbox(
            "Compare strategies individually",
            value=True, key="research_multi_compare",
        )
        run = st.form_submit_button(
            "Run Multi-Strategy backtest", type="primary"
        )

    if run:
        if not selected:
            st.warning("Select at least one strategy.")
        elif target < min_rr:
            st.warning("Target R:R must be at least the selected minimum R:R.")
        else:
            if start_date > end_date:
                st.warning("Backtest start must be on or before the end date.")
                loaded = None
            else:
                loaded = marketdata.load(symbol, timeframe, marketdata.BACKTEST_CANDLES)
            if loaded is None:
                return
            if not loaded.ok:
                ui.html_block(ui.error_state("Market data unavailable", loaded.error))
            else:
                frame = loaded.frame
                frame = frame.loc[
                    (frame.index.date >= start_date) &
                    (frame.index.date <= end_date)
                ]
                if len(frame) < 102:
                    ui.html_block(ui.error_state(
                        "Not enough historical data",
                        "This date range contains fewer than 102 finalized candles; "
                        "choose a wider range.",
                    ))
                    return
                try:
                    with st.spinner("Replaying confirmed aggregate signals..."):
                        result = run_multi_strategy_backtest(
                            frame,
                            symbol=symbol,
                            timeframe=timeframe,
                            starting_capital=float(starting_capital),
                            risk_fraction=float(risk_percent) / 100.0,
                            min_rr=float(min_rr),
                            target_rr=float(target),
                            max_concurrent_trades=int(max_concurrent),
                            fee_rate=float(fee_rate) / 100.0,
                            slippage_rate=float(slippage_rate) / 100.0,
                            strategy_selection=selected,
                        )
                        comparison = []
                        if compare and len(selected) > 1:
                            for strategy in selected:
                                single = run_multi_strategy_backtest(
                                    frame, symbol=symbol, timeframe=timeframe,
                                    starting_capital=float(starting_capital),
                                    risk_fraction=float(risk_percent) / 100.0,
                                    min_rr=float(min_rr), target_rr=float(target),
                                    max_concurrent_trades=int(max_concurrent),
                                    fee_rate=float(fee_rate) / 100.0,
                                    slippage_rate=float(slippage_rate) / 100.0,
                                    strategy_selection=[strategy],
                                )
                                comparison.append({
                                    "Strategy": display_name(strategy),
                                    "Return %": single["metrics"]["Return %"],
                                    "Final Equity": single["metrics"]["Final capital"],
                                    "Max Drawdown %": single["metrics"]["Max drawdown %"],
                                    "Win Rate %": single["metrics"]["Win rate %"],
                                    "Profit Factor": single["metrics"]["Profit factor"],
                                    "Expectancy": single["metrics"].get("Expectancy"),
                                    "Total Trades": single["metrics"]["Total trades"],
                                })
                    st.session_state[RESULT_KEY] = {
                        **result,
                        "source": loaded.source,
                        "used_fallback": loaded.used_fallback,
                        "comparison": pd.DataFrame(comparison),
                        "backtest_config": {
                            "start": start_date.isoformat(), "end": end_date.isoformat(),
                            "fee_rate": float(fee_rate) / 100.0,
                            "slippage_rate": float(slippage_rate) / 100.0,
                        },
                    }
                    st.success("Multi-Strategy historical backtest completed.")
                except (ValueError, TypeError) as exc:
                    ui.html_block(ui.error_state("Backtest failed", str(exc)))

    result = st.session_state.get(RESULT_KEY)
    if result:
        _render_result(result)


def _render_result(result: dict) -> None:
    """Render only values returned by the historical simulator."""
    metrics = result["metrics"]
    st.caption(
        f"Source: {result['source']} · {result['symbol']} · "
        f"{result['timeframe']} · {result['start']} to {result['end']} UTC"
    )
    if result.get("used_fallback"):
        st.info("Using bundled historical fallback data; this is not a live feed.")

    groups = (
        ("Initial capital", "Final capital", "Total P&L", "Return %", "Total trades"),
        ("Winning trades", "Losing trades", "Win rate %", "Profit factor", "Expectancy"),
        ("Max drawdown %", "Exposure %", "Blocked signals", "Executed signals"),
    )
    for group in groups:
        cells = st.columns(len(group))
        for cell, key in zip(cells, group):
            value = metrics.get(key)
            if value is None:
                rendered = "--"
            elif key in {"Initial capital", "Final capital", "Total P&L", "Expectancy"}:
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

    st.subheader("Strategy breakdown · measured historical results")
    st.dataframe(result["strategy_breakdown"], width="stretch", hide_index=True)
    comparison = result.get("comparison")
    if comparison is not None and not comparison.empty:
        st.subheader("Strategy comparison · same data and assumptions")
        st.dataframe(comparison, width="stretch", hide_index=True)
    st.subheader("Equity curve")
    equity = result["equity"]
    if not equity.empty:
        st.line_chart(equity[["equity"]], height=300)
        st.line_chart(equity[["drawdown_pct"]], height=180)
    else:
        st.info("No evaluation candles produced an equity curve.")

    trades = result["trades"]
    if trades.empty:
        st.info("No confirmed, risk-valid trades were executed in this range.")
    else:
        with st.expander(f"Detailed trade log ({len(trades)} trades)"):
            st.dataframe(trades, width="stretch", hide_index=True)
    blocked = result["blocked_signals"]
    if not blocked.empty:
        with st.expander(f"Blocked signals ({len(blocked)})"):
            st.dataframe(blocked, width="stretch", hide_index=True)
