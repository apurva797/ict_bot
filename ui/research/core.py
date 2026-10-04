"""Research computations: sweeps, walk-forward, Monte Carlo, robustness.

Nothing here renders or touches Streamlit, so every statistic can be tested
directly and reused by a future API layer without dragging in the UI.
"""

from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

import pandas as pd

from platform_core.analytics import compute_analytics

# A configuration needs this many closed trades before its win rate means
# anything. Below it the metrics are reported, but the configuration is not
# eligible to be called robust.
MIN_TRADES = 10

# Minimum out-of-sample trades before an in-sample result can be trusted.
MIN_OOS_TRADES = 5

# Upper bound on one sweep, so a mistyped range cannot launch a run long enough
# to look like a hang. Truncation is always reported to the user.
MAX_CONFIGURATIONS = 120


@dataclass(frozen=True)
class Config:
    """One point in a parameter grid."""

    values: dict = field(default_factory=dict)

    def label(self) -> str:
        """Human-readable parameter summary."""
        return " · ".join(f"{key}={value}" for key, value in sorted(self.values.items()))

    def key(self) -> str:
        """Stable identity for caching and de-duplication."""
        return "|".join(f"{key}={value}" for key, value in sorted(self.values.items()))


@dataclass
class ConfigResult:
    """Measured outcome of one configuration on one slice of data."""

    config: Config
    metrics: dict = field(default_factory=dict)
    trades: int = 0
    error: str = ""
    # Raw closed-trade rows, retained so downstream analyses (Monte Carlo,
    # stability) work on real records rather than on summary aggregates.
    trade_rows: list = field(default_factory=list)
    equity_points: list = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True when the run produced enough trades to judge."""
        return not self.error and self.trades >= MIN_TRADES

    @property
    def pnls(self) -> list[float]:
        """Net P&L of each closed trade in this run."""
        return [float(trade.get("net_pnl") or 0.0) for trade in self.trade_rows]

    @property
    def net_pnl(self) -> float:
        return float(self.metrics.get("realized_pnl", 0.0) or 0.0)

    @property
    def win_rate(self) -> float | None:
        return self.metrics.get("win_rate_pct")

    @property
    def profit_factor(self) -> float | None:
        return self.metrics.get("profit_factor")

    @property
    def expectancy(self) -> float | None:
        return self.metrics.get("expectancy")

    @property
    def max_drawdown(self) -> float | None:
        return self.metrics.get("max_drawdown_pct")

    @property
    def average_r(self) -> float | None:
        return self.metrics.get("average_r")

    def to_dict(self) -> dict:
        record = {
            "label": self.config.label(),
            "values": dict(self.config.values),
            "trades": self.trades,
            "error": self.error,
            "status": ("Failed" if self.error
                       else "Passed" if self.passed else "Too few trades"),
        }
        for name in ("realized_pnl", "win_rate_pct", "profit_factor", "expectancy",
                     "max_drawdown_pct", "average_r", "total_trades"):
            record[name] = self.metrics.get(name)
        return record


def grid(parameter_values: dict) -> list[Config]:
    """Expand a parameter mapping into the full cartesian grid."""
    keys = sorted(parameter_values)
    combos = itertools.product(*(parameter_values[key] for key in keys))
    return [Config(dict(zip(keys, values))) for values in combos]


@dataclass
class SweepResult:
    """Every configuration's result, plus whether the grid was truncated."""

    results: list = field(default_factory=list)
    truncated: bool = False
    total_possible: int = 0


def sweep(parameter_values: dict,
          evaluate: Callable[[Config], ConfigResult]) -> SweepResult:
    """Run every configuration through ``evaluate``, capped for responsiveness.

    A configuration that raises is recorded as a failed result rather than
    aborting the sweep, so one bad parameter combination cannot hide the
    results of all the others.
    """
    configurations = grid(parameter_values)
    total = len(configurations)
    if total > MAX_CONFIGURATIONS:
        configurations = configurations[:MAX_CONFIGURATIONS]
    results = []
    for config in configurations:
        try:
            results.append(evaluate(config))
        except Exception as exc:  # noqa: BLE001 - one bad config must not stop the sweep
            results.append(ConfigResult(
                config=config, error=f"{type(exc).__name__}: {exc}"))
    return SweepResult(results=results, truncated=total > len(configurations),
                       total_possible=total)


def split_walk_forward(frame: pd.DataFrame, folds: int = 4,
                       train_ratio: float = 0.7) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
    """Split candles into sequential in-sample / out-of-sample folds.

    Folds advance forward in time and never overlap, so every out-of-sample
    slice is strictly later than the data used to select on it. That ordering
    is the entire point of walk-forward: it removes the lookahead that makes an
    ordinary backtest look better than it is.
    """
    if frame is None or len(frame) < folds * 2:
        return []
    # Ceiling division so the final fold is not discarded by integer division:
    # ``len // folds`` leaves a remainder that would silently shorten the run.
    size = math.ceil(len(frame) / folds)
    windows = []
    for index in range(folds):
        start = index * size
        if start >= len(frame):
            break
        # Clamp to the data, but do not break on equality: the final fold
        # legitimately ends exactly at the last candle.
        stop = min(start + size, len(frame))
        cut = start + int(size * train_ratio)
        train, test = frame.iloc[start:cut], frame.iloc[cut:stop]
        if len(train) and len(test):
            windows.append((train, test))
    return windows


def monte_carlo(pnls: Sequence[float], runs: int = 500,
                seed: int = 20260101) -> dict:
    """Resample the realised trade sequence to estimate outcome dispersion.

    This measures how much the result depended on the order in which trades
    happened to occur. It is not a forecast and it says nothing about whether
    the strategy has an edge; it only describes the spread of orderings of
    trades that actually happened.
    """
    values = [float(value) for value in pnls
              if value is not None and math.isfinite(float(value))]
    if len(values) < 2:
        return {"runs": 0, "trades": len(values), "insufficient": True,
                "median": None, "mean": None, "p05": None, "p95": None,
                "worst_drawdown": None, "median_drawdown": None, "risk_of_ruin": None}
    generator = random.Random(seed)
    finals = []
    for _ in range(runs):
        balance = 0.0
        peak = 0.0
        worst = 0.0
        for _ in range(len(values)):
            balance += generator.choice(values)
            peak = max(peak, balance)
            worst = min(worst, balance - peak)
        finals.append((balance, worst))
    balances = sorted(value for value, _ in finals)
    drawdowns = sorted(value for _, value in finals)

    def percentile(series: list[float], fraction: float) -> float:
        index = min(len(series) - 1,
                    max(0, int(round(fraction * (len(series) - 1)))))
        return series[index]

    return {
        "runs": runs, "trades": len(values), "insufficient": False,
        "median": percentile(balances, 0.5),
        "mean": sum(balances) / len(balances),
        "p05": percentile(balances, 0.05),
        "p95": percentile(balances, 0.95),
        "worst_drawdown": drawdowns[0],
        "median_drawdown": percentile(drawdowns, 0.5),
        # The share of resampled sequences that finished at or below zero.
        "risk_of_ruin": sum(1 for value in balances if value <= 0) / len(balances),
    }


def parameter_stability(results: Sequence[ConfigResult], metric: str = "net_pnl") -> dict:
    """How much a metric varies across the parameter grid.

    A configuration sitting on a narrow spike is usually overfitted to one
    exact parameter value; one sitting on a broad plateau is usually real.
    """
    numbers = [float(getattr(result, metric)) for result in results
               if not result.error and result.trades > 0
               and getattr(result, metric) is not None]
    if len(numbers) < 2:
        return {"samples": len(numbers), "median": None, "spread": None, "stable": False}
    ordered = sorted(numbers)
    middle = len(ordered) // 2
    median = (ordered[middle] if len(ordered) % 2
              else (ordered[middle - 1] + ordered[middle]) / 2)
    spread = ordered[-1] - ordered[0]
    # A relative spread under 40% of the median magnitude means the metric does
    # not hinge on a single parameter value.
    stable = abs(median) > 0 and (spread / abs(median)) <= 0.4
    return {"samples": len(numbers), "median": median, "spread": spread,
            "stable": bool(stable)}


def robustness_score(in_sample: ConfigResult, out_of_sample: ConfigResult,
                     stability: dict | None = None,
                     simulation: dict | None = None) -> dict:
    """Grade one configuration from its out-of-sample and stability evidence.

    The score is a transparent sum of named components rather than a single
    opaque number, so a user can see exactly which check failed and why a
    configuration was rejected.
    """
    if not in_sample.passed:
        return {
            "score": 0.0, "verdict": "Insufficient trades",
            "components": {"trade_count": 0.0}, "passes": False,
            "reason": f"Only {in_sample.trades} closed trades; {MIN_TRADES} required.",
        }

    components: dict[str, float] = {}
    passes = True
    reasons: list[str] = []
    components["trade_count"] = min(1.0, in_sample.trades / (MIN_TRADES * 3))

    oos_trades = out_of_sample.trades
    if out_of_sample.error or oos_trades < MIN_OOS_TRADES:
        passes = False
        components["oos_coverage"] = 0.0
        reasons.append(f"Only {oos_trades} out-of-sample trades; "
                       f"{MIN_OOS_TRADES} required.")
    else:
        components["oos_coverage"] = min(1.0, oos_trades / (MIN_OOS_TRADES * 4))

    is_pnl, oos_pnl = in_sample.net_pnl, out_of_sample.net_pnl
    if is_pnl > 0 and oos_pnl > 0:
        # How much of the in-sample edge survived out of sample.
        components["oos_consistency"] = max(0.0, min(1.0, oos_pnl / is_pnl))
        if oos_pnl < is_pnl * 0.5:
            reasons.append("Out-of-sample P&L fell below half the in-sample result.")
    else:
        components["oos_consistency"] = 0.0
        passes = False
        reasons.append("Out-of-sample P&L was not positive.")

    if stability is not None:
        components["stability"] = 1.0 if stability.get("stable") else 0.35
        if not stability.get("stable"):
            reasons.append("Performance is sensitive to the exact parameter values.")
    else:
        components["stability"] = 0.5

    if simulation is not None and not simulation.get("insufficient"):
        risk = float(simulation.get("risk_of_ruin") or 0.0)
        components["monte_carlo"] = max(0.0, 1.0 - min(1.0, risk * 4))
    else:
        components["monte_carlo"] = 0.5

    score = round(100 * sum(components.values()) / len(components), 1)
    verdict = ("Robust" if passes and score >= 70
               else "Fragile" if passes else "Failed validation")
    return {"score": score, "verdict": verdict, "components": components,
            "passes": bool(passes and score >= 70),
            "reason": " ".join(reasons) or "All validation checks passed."}


def rank(results: Sequence[ConfigResult]) -> list[ConfigResult]:
    """Sort by net P&L, keeping failed and under-sampled runs at the bottom."""
    return sorted(results, key=lambda item: (bool(item.error), not item.passed,
                                             -item.net_pnl))


def evaluate_trades(trades: Iterable[dict]) -> tuple[dict, list]:
    """Analytics plus a per-trade equity series for a trade list."""
    rows = [trade for trade in trades if isinstance(trade, dict)]
    metrics = compute_analytics(rows)
    running = 0.0
    series = []
    for trade in rows:
        running += float(trade.get("net_pnl") or 0.0)
        series.append((str(trade.get("closed_at") or ""), running))
    return metrics, series


def to_dataframe(results: Sequence[ConfigResult]) -> pd.DataFrame:
    """Tabular view of a sweep, ranked for display and export."""
    frame = pd.DataFrame([result.to_dict() for result in rank(results)])
    if frame.empty:
        return frame
    return frame.sort_values("realized_pnl", ascending=False)