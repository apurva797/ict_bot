"""Research suite.

Pure, testable computations over real price series and real trade records:
parameter sweeps, walk-forward splits, Monte Carlo resampling, and robustness
grading.

Two rules hold throughout. First, every number comes from actual candles or
actual closed trades; nothing is modelled or assumed. Second, a configuration
is never labelled "best" without evidence that it survived out-of-sample and
robustness checks, because the point of this package is to stop an in-sample
optimum being mistaken for a working strategy.
"""

from __future__ import annotations

from ui.research.core import (MIN_OOS_TRADES, MIN_TRADES, MAX_CONFIGURATIONS,
                              Config, ConfigResult, SweepResult, evaluate_trades,
                              grid, monte_carlo, parameter_stability, rank,
                              robustness_score, split_walk_forward, sweep,
                              to_dataframe)

__all__ = [
    "MIN_OOS_TRADES", "MIN_TRADES", "MAX_CONFIGURATIONS", "Config", "ConfigResult",
    "SweepResult", "evaluate_trades", "grid", "monte_carlo", "parameter_stability",
    "rank", "robustness_score", "split_walk_forward", "sweep", "to_dataframe",
]


def render() -> None:
    """Render the research workspace.

    Imported lazily inside the function: the screens module imports this
    package's own computations, so importing it at module scope would create a
    cycle.
    """
    from ui.research.screens import render as render_screens

    render_screens()