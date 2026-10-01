"""Analytics computed from real closed trades only."""

from __future__ import annotations

from typing import Any

import pandas as pd


def _pnl(trade: dict) -> float:
    for key in ("net_pnl", "realized_pnl", "pnl"):
        if key in trade and trade[key] is not None:
            try:
                return float(trade[key])
            except (TypeError, ValueError):
                continue
    return 0.0


def compute_analytics(trades: list[dict]) -> dict[str, Any]:
    """Return descriptive statistics for a list of closed trades.

    An empty trade list yields explicit ``None`` values rather than zeros that
    could be mistaken for real performance.
    """
    rows = [trade for trade in (trades or []) if isinstance(trade, dict)]
    total = len(rows)
    if total == 0:
        return {
            "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
            "win_rate_pct": None, "average_win": None, "average_loss": None,
            "expectancy": None, "profit_factor": None, "average_r": None,
            "realized_pnl": 0.0, "fees_paid": 0.0, "max_drawdown_pct": None,
            "max_consecutive_wins": 0, "max_consecutive_losses": 0,
        }

    pnls = [_pnl(trade) for trade in rows]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    r_multiples = []
    for trade, value in zip(rows, pnls):
        risk = trade.get("risk_amount")
        try:
            risk = float(risk) if risk else 0.0
        except (TypeError, ValueError):
            risk = 0.0
        if risk > 0:
            r_multiples.append(value / risk)

    equity = []
    running = 0.0
    for value in pnls:
        running += value
        equity.append(running)
    equity_series = pd.Series(equity, dtype=float)
    running_max = equity_series.cummax().clip(lower=0)
    drawdowns = (equity_series - running_max) / running_max.replace(0, pd.NA)
    max_drawdown = float(drawdowns.min()) if len(drawdowns) else 0.0

    streak_wins = streak_losses = best_wins = best_losses = 0
    for value in pnls:
        if value > 0:
            streak_wins += 1
            streak_losses = 0
        elif value < 0:
            streak_losses += 1
            streak_wins = 0
        best_wins = max(best_wins, streak_wins)
        best_losses = max(best_losses, streak_losses)

    return {
        "total_trades": total,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate_pct": 100.0 * len(wins) / total,
        "average_win": (gross_profit / len(wins)) if wins else None,
        "average_loss": (-gross_loss / len(losses)) if losses else None,
        "expectancy": (sum(pnls) / total) if total else None,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else None,
        "average_r": (sum(r_multiples) / len(r_multiples)) if r_multiples else None,
        "realized_pnl": float(sum(pnls)),
        "fees_paid": float(sum(float(trade.get("fees", 0) or 0) for trade in rows)),
        "max_drawdown_pct": max_drawdown * 100 if pd.notna(max_drawdown) else 0.0,
        "max_consecutive_wins": best_wins,
        "max_consecutive_losses": best_losses,
    }


def equity_curve(trades: list[dict], starting_balance: float = 0.0) -> list[dict[str, Any]]:
    """Chronological equity points, one per closed trade."""
    rows = [trade for trade in (trades or []) if isinstance(trade, dict)]
    rows = sorted(rows, key=lambda trade: str(trade.get("closed_at") or ""))
    points: list[dict[str, Any]] = []
    balance = float(starting_balance)
    peak = balance
    for trade in rows:
        balance += _pnl(trade)
        peak = max(peak, balance)
        points.append({
            "closed_at": trade.get("closed_at"),
            "equity": balance,
            "pnl": _pnl(trade),
            "drawdown": (balance - peak) if peak else 0.0,
        })
    return points