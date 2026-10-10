"""Institutional-grade daily return accounting and performance analytics.

Calculates exact daily calendar equity, returns, P&L in account currency,
drawdowns, monthly returns, and full risk metrics. Reconciles end-of-day
equity with closed trades and continuous candle valuation.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd


def compute_daily_returns_table(
    equity_curve: Union[pd.DataFrame, List[Dict[str, Any]]],
    trades: Union[pd.DataFrame, List[Dict[str, Any]]],
    starting_capital: float,
    start_date: Optional[Union[str, date, datetime, pd.Timestamp]] = None,
    end_date: Optional[Union[str, date, datetime, pd.Timestamp]] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """Compute exact calendar daily accounting, monthly summary, and performance metrics.

    Parameters
    ----------
    equity_curve : DataFrame or list of dicts with 'time' (or index) and 'equity'.
                   May also include 'open_positions' count and 'cash'.
    trades : DataFrame or list of dicts of closed trades with 'closed_at', 'pnl', 'fees', etc.
    starting_capital : Initial portfolio capital.
    start_date : Earliest calendar date. If None, derived from equity_curve.
    end_date : Latest calendar date. If None, derived from equity_curve.

    Returns
    -------
    daily_df : DataFrame indexed by Date with daily accounting columns.
    monthly_df : DataFrame grouped by Month with monthly summary columns.
    metrics : Dict of institutional performance metrics.
    """
    initial_cap = float(starting_capital)
    if initial_cap <= 0:
        raise ValueError("Starting capital must be a positive number.")

    # 1. Normalize equity curve
    if isinstance(equity_curve, list):
        if not equity_curve:
            eq_df = pd.DataFrame(columns=["equity", "open_positions"], index=pd.DatetimeIndex([], name="time"))
        else:
            eq_df = pd.DataFrame(equity_curve)
            if "time" in eq_df.columns:
                eq_df["time"] = pd.to_datetime(eq_df["time"], utc=True)
                eq_df = eq_df.set_index("time")
            else:
                eq_df.index = pd.to_datetime(eq_df.index, utc=True)
    elif isinstance(equity_curve, pd.DataFrame):
        eq_df = equity_curve.copy()
        if "time" in eq_df.columns and not isinstance(eq_df.index, pd.DatetimeIndex):
            eq_df["time"] = pd.to_datetime(eq_df["time"], utc=True)
            eq_df = eq_df.set_index("time")
        elif not isinstance(eq_df.index, pd.DatetimeIndex):
            eq_df.index = pd.to_datetime(eq_df.index, utc=True)
    else:
        eq_df = pd.DataFrame(columns=["equity", "open_positions"], index=pd.DatetimeIndex([], name="time"))

    if not eq_df.empty and eq_df.index.tz is None:
        eq_df.index = eq_df.index.tz_localize("UTC")
    elif not eq_df.empty and str(eq_df.index.tz) != "UTC":
        eq_df.index = eq_df.index.tz_convert("UTC")

    # 2. Normalize trades
    if isinstance(trades, list):
        tr_df = pd.DataFrame(trades) if trades else pd.DataFrame()
    elif isinstance(trades, pd.DataFrame):
        tr_df = trades.copy()
    else:
        tr_df = pd.DataFrame()

    if not tr_df.empty:
        if "closed_at" in tr_df.columns:
            tr_df["closed_at"] = pd.to_datetime(tr_df["closed_at"], utc=True)
        if "opened_at" in tr_df.columns:
            tr_df["opened_at"] = pd.to_datetime(tr_df["opened_at"], utc=True)
        for num_col in ("pnl", "net_pnl", "fees", "slippage_cost", "risk_amount"):
            if num_col in tr_df.columns:
                tr_df[num_col] = pd.to_numeric(tr_df[num_col], errors="coerce").fillna(0.0)

    # 3. Determine calendar range
    if start_date is not None:
        start_dt = pd.Timestamp(start_date).tz_localize("UTC") if pd.Timestamp(start_date).tzinfo is None else pd.Timestamp(start_date).tz_convert("UTC")
        cal_start = start_dt.date()
    elif not eq_df.empty:
        cal_start = eq_df.index[0].date()
    else:
        cal_start = datetime.now(timezone.utc).date()

    if end_date is not None:
        end_dt = pd.Timestamp(end_date).tz_localize("UTC") if pd.Timestamp(end_date).tzinfo is None else pd.Timestamp(end_date).tz_convert("UTC")
        cal_end = end_dt.date()
    elif not eq_df.empty:
        cal_end = eq_df.index[-1].date()
    else:
        cal_end = cal_start

    if cal_start > cal_end:
        cal_start, cal_end = cal_end, cal_start

    # Build sequence of all calendar days
    total_days = (cal_end - cal_start).days + 1
    calendar_dates = [cal_start + timedelta(days=i) for i in range(total_days)]

    # 4. Process each calendar day
    daily_records = []
    current_starting_equity = initial_cap
    peak_equity = initial_cap

    for day in calendar_dates:
        day_str = str(day)
        
        # Sub-slice of candles on this day
        if not eq_df.empty:
            day_candles = eq_df[eq_df.index.date == day]
        else:
            day_candles = pd.DataFrame()

        if not day_candles.empty:
            ending_equity = float(day_candles["equity"].iloc[-1])
            open_pos = int(day_candles["open_positions"].iloc[-1]) if "open_positions" in day_candles.columns else 0
        else:
            # Carry forward previous day's ending equity
            ending_equity = current_starting_equity
            open_pos = 0

        daily_pnl = ending_equity - current_starting_equity
        daily_return_pct = ((ending_equity / current_starting_equity) - 1.0) * 100.0 if current_starting_equity > 0 else 0.0

        # Trades closed on this calendar day
        if not tr_df.empty and "closed_at" in tr_df.columns:
            day_trades = tr_df[tr_df["closed_at"].dt.date == day]
            pnl_col = "pnl" if "pnl" in day_trades.columns else "net_pnl" if "net_pnl" in day_trades.columns else None
            t_count = len(day_trades)
            if pnl_col:
                wins = int((day_trades[pnl_col] > 0).sum())
                losses = int((day_trades[pnl_col] < 0).sum())
            else:
                wins = 0
                losses = 0
            fees = float(day_trades["fees"].sum()) if "fees" in day_trades.columns else 0.0
        else:
            t_count = 0
            wins = 0
            losses = 0
            fees = 0.0

        # Drawdown tracking
        peak_equity = max(peak_equity, ending_equity)
        drawdown_pct = ((peak_equity - ending_equity) / peak_equity) * 100.0 if peak_equity > 0 else 0.0

        daily_records.append({
            "Date": day_str,
            "Starting Equity": round(current_starting_equity, 2),
            "Ending Equity": round(ending_equity, 2),
            "Daily P&L": round(daily_pnl, 2),
            "Daily Return %": round(daily_return_pct, 4),
            "Total Trades": t_count,
            "Winning Trades": wins,
            "Losing Trades": losses,
            "Fees": round(fees, 2),
            "Open Positions": open_pos,
            "Drawdown %": round(drawdown_pct, 2),
        })

        # Advance starting equity for next calendar day
        current_starting_equity = ending_equity

    daily_df = pd.DataFrame(daily_records).set_index("Date")

    # 5. Monthly aggregation
    monthly_records = []
    if not daily_df.empty:
        temp_daily = daily_df.reset_index()
        temp_daily["Month"] = temp_daily["Date"].apply(lambda d: d[:7])
        
        for m_name, group in temp_daily.groupby("Month", sort=False):
            m_start_eq = float(group["Starting Equity"].iloc[0])
            m_end_eq = float(group["Ending Equity"].iloc[-1])
            m_pnl = m_end_eq - m_start_eq
            m_ret_pct = ((m_end_eq / m_start_eq) - 1.0) * 100.0 if m_start_eq > 0 else 0.0
            m_max_dd = float(group["Drawdown %"].max())
            m_trades = int(group["Total Trades"].sum())
            monthly_records.append({
                "Month": m_name,
                "Starting Equity": round(m_start_eq, 2),
                "Ending Equity": round(m_end_eq, 2),
                "Net P&L": round(m_pnl, 2),
                "Monthly Return %": round(m_ret_pct, 2),
                "Max Drawdown %": round(m_max_dd, 2),
                "Number of Trades": m_trades,
            })
    monthly_df = pd.DataFrame(monthly_records).set_index("Month") if monthly_records else pd.DataFrame(
        columns=["Starting Equity", "Ending Equity", "Net P&L", "Monthly Return %", "Max Drawdown %", "Number of Trades"]
    )

    # 6. Institutional Performance Metrics
    final_capital = float(daily_df["Ending Equity"].iloc[-1]) if not daily_df.empty else initial_cap
    total_net_pnl = final_capital - initial_cap
    total_net_return_pct = (total_net_pnl / initial_cap) * 100.0 if initial_cap > 0 else 0.0

    returns_series = daily_df["Daily Return %"] / 100.0 if not daily_df.empty else pd.Series([], dtype=float)
    pnl_series = daily_df["Daily P&L"] if not daily_df.empty else pd.Series([], dtype=float)

    n_days = len(daily_df)
    profitable_days = int((pnl_series > 0).sum())
    losing_days = int((pnl_series < 0).sum())
    breakeven_days = int((pnl_series == 0).sum())
    active_days = profitable_days + losing_days

    avg_daily_return_pct = float(returns_series.mean() * 100.0) if not returns_series.empty else 0.0
    median_daily_return_pct = float(returns_series.median() * 100.0) if not returns_series.empty else 0.0
    daily_vol_pct = float(returns_series.std(ddof=1) * 100.0) if len(returns_series) > 1 else 0.0
    avg_daily_pnl = float(pnl_series.mean()) if not pnl_series.empty else 0.0

    # Best & worst days
    if not daily_df.empty:
        best_day_idx = pnl_series.idxmax()
        worst_day_idx = pnl_series.idxmin()
        best_day = {
            "date": best_day_idx,
            "return_pct": float(daily_df.loc[best_day_idx, "Daily Return %"]),
            "pnl": float(daily_df.loc[best_day_idx, "Daily P&L"]),
        }
        worst_day = {
            "date": worst_day_idx,
            "return_pct": float(daily_df.loc[worst_day_idx, "Daily Return %"]),
            "pnl": float(daily_df.loc[worst_day_idx, "Daily P&L"]),
        }
    else:
        best_day = {"date": None, "return_pct": 0.0, "pnl": 0.0}
        worst_day = {"date": None, "return_pct": 0.0, "pnl": 0.0}

    # Streaks of losing days
    longest_losing_streak = 0
    cur_streak = 0
    for pnl_val in pnl_series:
        if pnl_val < 0:
            cur_streak += 1
            longest_losing_streak = max(longest_losing_streak, cur_streak)
        else:
            cur_streak = 0

    # Max Drawdown and Duration
    max_dd_pct = float(daily_df["Drawdown %"].max()) if not daily_df.empty else 0.0
    max_dd_duration_days = _calc_drawdown_duration(daily_df["Ending Equity"].values if not daily_df.empty else [])

    # Annualization: 365.25 calendar days convention for crypto spot
    cagr = None
    if n_days > 0 and initial_cap > 0 and final_capital > 0:
        cagr = (math.pow(final_capital / initial_cap, 365.25 / n_days) - 1.0) * 100.0

    # Sharpe & Sortino (Annualized with zero risk-free rate)
    annualized_sharpe = None
    annualized_sortino = None
    if len(returns_series) > 1 and returns_series.std(ddof=1) > 0:
        annualized_sharpe = float((returns_series.mean() / returns_series.std(ddof=1)) * math.sqrt(365.25))
        downside = returns_series[returns_series < 0]
        if len(downside) > 0 and downside.std(ddof=0) > 0:
            annualized_sortino = float((returns_series.mean() / downside.std(ddof=0)) * math.sqrt(365.25))

    # Calmar ratio
    calmar = (cagr / max_dd_pct) if (cagr is not None and max_dd_pct > 0) else None

    # Trade stats
    pnl_col = "pnl" if "pnl" in tr_df.columns else "net_pnl" if "net_pnl" in tr_df.columns else None
    tot_trades = len(tr_df)
    if tot_trades > 0 and pnl_col:
        trades_pnl = tr_df[pnl_col].values
        winning_t = [p for p in trades_pnl if p > 0]
        losing_t = [p for p in trades_pnl if p < 0]
        win_rate = (len(winning_t) / tot_trades) * 100.0
        gross_profit = float(sum(winning_t))
        gross_loss = float(abs(sum(losing_t)))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (math.inf if gross_profit > 0 else 0.0)
        avg_win = float(np.mean(winning_t)) if winning_t else None
        avg_loss = float(np.mean(losing_t)) if losing_t else None
        win_loss_ratio = abs(avg_win / avg_loss) if (avg_win is not None and avg_loss is not None and avg_loss != 0) else None
        expectancy = float(np.mean(trades_pnl))
        avg_r = float(tr_df["r_multiple"].mean()) if "r_multiple" in tr_df.columns and not tr_df["r_multiple"].isna().all() else None
        tot_fees = float(tr_df["fees"].sum()) if "fees" in tr_df.columns else 0.0
        tot_slippage = float(tr_df["slippage_cost"].sum()) if "slippage_cost" in tr_df.columns else 0.0
        
        # Max consecutive wins/losses for trades
        max_c_wins = max_c_losses = c_w = c_l = 0
        for p in trades_pnl:
            if p > 0:
                c_w += 1; c_l = 0
                max_c_wins = max(max_c_wins, c_w)
            elif p < 0:
                c_l += 1; c_w = 0
                max_c_losses = max(max_c_losses, c_l)
            else:
                c_w = c_l = 0
    else:
        tot_trades = 0
        winning_t = losing_t = []
        win_rate = 0.0
        gross_profit = gross_loss = 0.0
        profit_factor = 0.0
        avg_win = avg_loss = win_loss_ratio = expectancy = avg_r = None
        tot_fees = tot_slippage = 0.0
        max_c_wins = max_c_losses = 0

    metrics = {
        "Initial capital": initial_cap,
        "Final capital": final_capital,
        "Total Net P&L": total_net_pnl,
        "Net Return %": total_net_return_pct,
        "CAGR %": cagr,
        "Total Calendar Days": n_days,
        "Active Trading Days": active_days,
        "Profitable Days": profitable_days,
        "Losing Days": losing_days,
        "Breakeven Days": breakeven_days,
        "Profitable Days % (Calendar)": (profitable_days / n_days * 100.0) if n_days else 0.0,
        "Profitable Days % (Active)": (profitable_days / active_days * 100.0) if active_days else 0.0,
        "Average Daily Return %": avg_daily_return_pct,
        "Median Daily Return %": median_daily_return_pct,
        "Daily Return Volatility %": daily_vol_pct,
        "Average Daily P&L": avg_daily_pnl,
        "Best Day": best_day,
        "Worst Day": worst_day,
        "Longest Losing-Day Streak": longest_losing_streak,
        "Maximum Drawdown %": max_dd_pct,
        "Max Drawdown Duration Days": max_dd_duration_days,
        "Sharpe Ratio": annualized_sharpe,
        "Sortino Ratio": annualized_sortino,
        "Calmar Ratio": calmar,
        "Total Trades": tot_trades,
        "Winning Trades": len(winning_t),
        "Losing Trades": len(losing_t),
        "Win Rate %": win_rate,
        "Gross Profit": gross_profit,
        "Gross Loss": gross_loss,
        "Profit Factor": profit_factor,
        "Average Win": avg_win,
        "Average Loss": avg_loss,
        "Win/Loss Ratio": win_loss_ratio,
        "Expectancy": expectancy,
        "Average R": avg_r,
        "Max Consecutive Trade Wins": max_c_wins,
        "Max Consecutive Trade Losses": max_c_losses,
        "Total Fees Paid": tot_fees,
        "Total Slippage Cost": tot_slippage,
    }

    return daily_df, monthly_df, metrics


def _calc_drawdown_duration(equity_values: Sequence[float]) -> int:
    """Calculate the maximum drawdown duration in calendar days."""
    if len(equity_values) == 0:
        return 0
    peak = equity_values[0]
    max_duration = 0
    cur_duration = 0
    for val in equity_values:
        if val >= peak:
            peak = val
            cur_duration = 0
        else:
            cur_duration += 1
            max_duration = max(max_duration, cur_duration)
    return max_duration
