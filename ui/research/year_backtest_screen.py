"""ICT Arjuna's one-year historical backtest screen.

This screen intentionally calls the production ICT signal implementation; it
must not route the ICT strategy through the generic EMA/RSI research baselines.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import demo_data
from demo_backtest import run_ict_backtest


def _interval(timeframe: str) -> pd.Timedelta:
    intervals = {"5m": pd.Timedelta(minutes=5), "15m": pd.Timedelta(minutes=15),
                 "1h": pd.Timedelta(hours=1)}
    if timeframe not in intervals:
        raise ValueError(f"Unsupported ICT historical timeframe: {timeframe}")
    return intervals[timeframe]


def validate_historical_coverage(frame: pd.DataFrame, start: pd.Timestamp,
                                 end: pd.Timestamp, timeframe: str) -> dict:
    """Reject incomplete history instead of presenting a partial period as a year."""
    interval = _interval(timeframe)
    if frame is None or frame.empty:
        raise ValueError("The historical provider returned no candles.")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("Historical candles must use a timestamp index.")
    index = frame.index
    index = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    if index.has_duplicates:
        raise ValueError("Historical data contains duplicate candle timestamps; test stopped.")
    if not index.is_monotonic_increasing:
        raise ValueError("Historical candles are not chronological; test stopped.")
    expected_index = pd.date_range(start=start, end=end - interval, freq=interval, tz="UTC")
    missing = expected_index.difference(index)
    extra = index.difference(expected_index)
    coverage = 100.0 * (len(expected_index) - len(missing)) / max(1, len(expected_index))
    if len(missing) or len(extra) or len(index) != len(expected_index):
        first_missing = missing[0].isoformat() if len(missing) else "none"
        last_missing = missing[-1].isoformat() if len(missing) else "none"
        raise ValueError(
            f"Incomplete historical coverage ({coverage:.2f}%). Expected {len(expected_index):,} "
            f"candles but received {len(index):,}; missing {len(missing):,}, extra {len(extra):,}. "
            f"First/last missing: {first_missing} / {last_missing}. "
            "No partial-period performance has been reported. Try another timeframe or upload complete OHLCV history."
        )
    return {"expected_candles": len(expected_index), "actual_candles": len(index),
            "coverage_pct": coverage, "first_candle": index[0], "last_candle": index[-1]}


def render_year_backtest_screen() -> None:
    st.title("ICT Arjuna · 365-Day Backtest")
    st.caption(
        "Runs the existing strategies.ict.ict_signal() logic on historical candles. "
        "This is a historical simulation, not investment advice or a promise of future returns."
    )
    with st.form("ict_arjuna_one_year_backtest_form"):
        col1, col2, col3 = st.columns(3)
        with col1:
            symbol = st.selectbox("Market", ["BTC/USDT", "ETH/USDT", "SOL/USDT"], index=0)
            timeframe = st.selectbox("Candle timeframe", ["1h", "15m", "5m"], index=0,
                                     help="A full year of 5m candles exceeds the current 50,000-candle safety limit.")
            days = st.slider("Historical period (days)", 30, 365, 365, 5)
        with col2:
            capital = st.number_input("Starting capital (quote currency)", min_value=1000.0,
                                      value=100000.0, step=5000.0)
            commission_pct = st.number_input("Commission per side (%)", min_value=0.0,
                                             max_value=1.0, value=0.06, step=0.01)
            slippage_pct = st.number_input("Slippage per side (%)", min_value=0.0,
                                           max_value=1.0, value=0.05, step=0.01)
        with col3:
            st.info("Fixed safety controls\n\n• Risk per trade: 1%\n• Maximum leverage: 1×\n• Minimum reward:risk: 1.5R\n• ICT evaluation: 24/7\n• Cooldown: existing platform rule")
            st.caption("Stop/target handling uses the existing demo backtest's 1% price-distance stop and 2R target. These are execution assumptions, not new ICT signal rules.")
        submitted = st.form_submit_button("Run ICT Arjuna historical backtest", type="primary")

    if not submitted:
        return
    if capital <= 0:
        st.error("Starting capital must be greater than zero.")
        return

    interval = _interval(timeframe)
    now = pd.Timestamp.now(tz="UTC")
    end = now.floor(timeframe)
    start = end - pd.Timedelta(days=int(days))
    expected = int((end - start) / interval)
    if expected > demo_data.MAX_HISTORICAL_CANDLES:
        st.error(
            f"This request needs {expected:,} candles, above the configured limit of "
            f"{demo_data.MAX_HISTORICAL_CANDLES:,}. A full year of {timeframe} data cannot be tested "
            "under the current limit. Choose 15m/1h or provide complete data through the historical-data pipeline."
        )
        return

    try:
        with st.spinner(f"Downloading up to {expected:,} historical {timeframe} candles and testing ICT Arjuna…"):
            data = demo_data.fetch_historical_market_data(symbol, timeframe, start, end)
            frame = data.frame.copy()
            coverage = validate_historical_coverage(frame, start, end, timeframe)
            # Preserve the actual existing ICT signal and demo execution model;
            # only commission/slippage assumptions are supplied by the UI.
            metrics, equity, trades = run_ict_backtest(
                frame, starting_capital=float(capital),
                fee_rate=float(commission_pct) / 100,
                slippage=float(slippage_pct) / 100,
            )
    except Exception as exc:
        st.error(f"ICT Arjuna backtest stopped: {exc}")
        st.info("No result was reported because complete, validated history or a valid simulation was unavailable.")
        return

    st.subheader("ICT Arjuna results")
    st.caption(f"Source: {data.source} · {symbol} · {timeframe} · Requested: {days} days")
    st.caption(
        f"Coverage: {coverage['coverage_pct']:.2f}% ({coverage['actual_candles']:,}/"
        f"{coverage['expected_candles']:,} candles) · "
        f"First: {coverage['first_candle'].isoformat()} · Last: {coverage['last_candle'].isoformat()}"
    )
    if data.used_fallback:
        st.warning("The historical-data provider reported fallback data. Verify the source label before relying on these results.")

    pnl = float(metrics["Total P&L"])
    total_return = float(metrics["Total return %"])
    cols = st.columns(4)
    cols[0].metric("Ending capital", f"{metrics['Ending capital']:,.2f}", f"{pnl:+,.2f}")
    cols[1].metric("Net return", f"{total_return:.2f}%")
    cols[2].metric("Maximum drawdown", f"{metrics['Maximum drawdown %']:.2f}%")
    cols[3].metric("Win rate", f"{metrics['Win rate %']:.2f}%", f"{metrics['Number of trades']} trades")
    cols2 = st.columns(4)
    pf = metrics["Profit factor"]
    cols2[0].metric("Profit factor", "∞" if pf == float("inf") else f"{pf:.3f}")
    cols2[1].metric("Fees paid", f"{metrics['Fees paid']:,.2f}")
    cols2[2].metric("Estimated slippage", f"{metrics['Estimated slippage paid']:,.2f}")
    cols2[3].metric("Average R", "—" if metrics["Average R"] is None else f"{metrics['Average R']:.2f}R")
    st.caption("Educational tool. Not investment advice. Historical results do not predict future performance.")

    if equity is not None and not equity.empty:
        curve = equity.copy()
        curve.index = pd.to_datetime(curve.index, utc=True)
        st.subheader("Equity curve")
        st.line_chart(curve[["equity"]])
    st.subheader("Trades")
    if trades is None or trades.empty:
        st.info("No trades were generated by the existing ICT signal during this period. Metrics above reflect zero closed trades.")
    else:
        st.dataframe(trades, use_container_width=True, hide_index=True)
        st.download_button("Download ICT Arjuna trades CSV", trades.to_csv(index=False).encode("utf-8"),
                           file_name="ict_arjuna_365_day_trades.csv", mime="text/csv")
