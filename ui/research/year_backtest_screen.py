"""Optional Streamlit page for the ARJUNA one-year backtest add-on.

Integrate by importing render_year_backtest_screen() from the existing research navigation.
This file does not modify app.py or existing navigation automatically.
"""
from __future__ import annotations

from pathlib import Path
import pandas as pd
import streamlit as st

from engine.year_backtest import BacktestConfig, run_one_year, save_results


def render_year_backtest_screen() -> None:
    st.title("Arjuna · One-Year Backtest")
    st.caption("Historical simulation only. Results depend on data coverage, costs, and assumptions; they are not a promise of future performance.")
    with st.form("arjuna_one_year_backtest_form"):
        col1, col2, col3 = st.columns(3)
        with col1:
            symbol = st.text_input("Symbol", "BTC/USDT")
            timeframe = st.selectbox("Timeframe", ["5m", "15m", "30m", "1h", "4h", "1d"], index=0)
            days = st.slider("Historical period (days)", 30, 365, 365, 5)
        with col2:
            capital = st.number_input("Starting capital", min_value=1000.0, value=100000.0, step=5000.0)
            risk_pct = st.slider("Risk per trade (%)", 0.1, 2.0, 1.0, 0.1)
            rr = st.slider("Target reward:risk", 1.5, 5.0, 2.0, 0.5)
        with col3:
            exchange = st.selectbox("Exchange", ["binance", "coinbase"], index=0)
            commission_pct = st.number_input("Commission per side (%)", min_value=0.0, value=0.06, step=0.01)
            slippage_pct = st.number_input("Slippage per side (%)", min_value=0.0, value=0.05, step=0.01)
        strategy_names = st.multiselect("Strategies to compare", ["ema_trend", "rsi_mean_reversion", "breakout", "multi_strategy"], default=["ema_trend", "rsi_mean_reversion", "breakout", "multi_strategy"])
        submitted = st.form_submit_button("Run historical backtest", type="primary")
    if not submitted:
        return
    if not strategy_names:
        st.warning("Select at least one strategy.")
        return
    config = BacktestConfig(symbol=symbol.strip(), timeframe=timeframe, days=days,
                            starting_capital=capital, risk_per_trade=risk_pct / 100,
                            preferred_rr=rr, min_rr=1.5, exchange_id=exchange,
                            commission_rate=commission_pct / 100,
                            slippage_rate=slippage_pct / 100,
                            data_source="auto", strategy=strategy_names[0])
    try:
        with st.spinner("Loading historical candles and running backtests…"):
            results = run_one_year(config, strategy_names)
            saved = save_results(results)
        summary = pd.DataFrame([r.metrics for r in results.values()])
        st.subheader("Strategy comparison")
        st.dataframe(summary, use_container_width=True, hide_index=True)
        for name, result in results.items():
            st.divider()
            st.subheader(name.replace("_", " ").title())
            if result.warnings:
                for warning in result.warnings:
                    st.warning(warning)
            metrics = result.metrics
            cols = st.columns(4)
            cols[0].metric("Net profit", f"{metrics['net_profit']:,.2f}", f"{metrics['total_return_pct']:.2f}%")
            cols[1].metric("Max drawdown", f"{metrics['max_drawdown_pct']:.2f}%")
            cols[2].metric("Win rate", f"{metrics['win_rate_pct']:.2f}%", f"{metrics['total_trades']} trades")
            cols[3].metric("Profit factor", str(metrics["profit_factor"]))
            eq = result.equity_frame()
            if not eq.empty:
                eq["timestamp"] = pd.to_datetime(eq.timestamp, utc=True)
                st.line_chart(eq.set_index("timestamp")[["equity"]])
            trades = result.trades_frame()
            st.dataframe(trades.tail(100), use_container_width=True, hide_index=True)
            if not trades.empty:
                st.download_button(f"Download {name} trades CSV", trades.to_csv(index=False).encode("utf-8"), file_name=f"{name}_trades.csv", mime="text/csv", key=f"download_{name}")
        st.success("Backtest finished. Results saved to backtest_results/.")
        st.caption("Saved artifacts: " + ", ".join(Path(p).name for p in saved.values()))
    except Exception as exc:
        st.error(f"Backtest failed: {exc}")
        st.info("Check the selected exchange, symbol, timeframe support, network availability, and data-source coverage.")
