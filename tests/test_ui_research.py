"""Research computations must never overstate what the data supports."""

import numpy as np
import pandas as pd

from ui.research import core


def frame(count=400, seed=7, freq="5min"):
    index = pd.date_range("2026-01-01", periods=count, freq=freq, tz="UTC")
    rng = np.random.default_rng(seed)
    closes = 100 + np.cumsum(rng.normal(0, 0.3, count))
    return pd.DataFrame({
        "open": closes - 0.1, "high": closes + 0.5, "low": closes - 0.5,
        "close": closes, "volume": rng.integers(100, 900, count).astype(float),
    }, index=index)


def result(pnl=100.0, trades=12, error=""):
    """A configuration result with representative metrics."""
    return core.ConfigResult(
        config=core.Config({"fast": 20}),
        metrics={"realized_pnl": pnl, "win_rate_pct": 55.0,
                 "profit_factor": 1.4, "expectancy": 8.3,
                 "max_drawdown_pct": -6.0, "average_r": 0.4},
        trades=trades, error=error,
    )


def test_grid_expands_the_full_cartesian_product():
    configurations = core.grid({"fast": [5, 10, 20], "slow": [50, 100]})
    assert len(configurations) == 6
    assert {config.label() for config in configurations} >= {
        "fast=5 · slow=50", "fast=20 · slow=100"}


def test_config_key_is_order_independent():
    assert core.Config({"b": 2, "a": 1}).key() == core.Config({"a": 1, "b": 2}).key()


def test_a_raising_config_is_recorded_and_does_not_stop_the_sweep():
    def evaluate(config):
        if config.values["fast"] == 5:
            raise RuntimeError("bad parameter")
        return result()

    outcome = core.sweep({"fast": [5, 10, 20]}, evaluate)
    assert len(outcome.results) == 3
    assert outcome.results[0].error.startswith("RuntimeError")
    assert all(item.net_pnl == 100.0 for item in outcome.results[1:])


def test_an_oversized_grid_is_capped_and_the_truncation_is_reported():
    outcome = core.sweep({"fast": list(range(core.MAX_CONFIGURATIONS + 50))},
                         lambda config: result())
    assert len(outcome.results) == core.MAX_CONFIGURATIONS
    assert outcome.truncated is True
    assert outcome.total_possible == core.MAX_CONFIGURATIONS + 50


def test_passing_requires_enough_real_trades():
    assert result(trades=core.MIN_TRADES).passed is True
    assert result(trades=core.MIN_TRADES - 1).passed is False
    assert result(error="boom").passed is False


def test_walk_forward_folds_never_overlap_and_oos_follows_training():
    windows = core.split_walk_forward(frame(400), folds=4)
    assert len(windows) == 4
    for train, test in windows:
        assert train.index.max() < test.index.min()
    for earlier, later in zip(windows, windows[1:]):
        assert earlier[0].index.min() < later[0].index.min()


def test_too_little_data_yields_no_folds():
    assert core.split_walk_forward(frame(6), folds=4) == []


def test_monte_carlo_is_deterministic_for_a_given_seed():
    pnls = [120.0, -100.0, 60.0, -100.0, 200.0, -100.0, 80.0, -100.0]
    assert core.monte_carlo(pnls, seed=42) == core.monte_carlo(pnls, seed=42)


def test_a_single_trade_is_reported_as_insufficient_not_as_zero():
    outcome = core.monte_carlo([100.0])
    assert outcome["insufficient"] is True
    assert outcome["median"] is None


def test_a_profitable_sequence_has_a_low_risk_of_ruin():
    outcome = core.monte_carlo([100.0] * 5)
    assert outcome["risk_of_ruin"] == 0.0
    assert outcome["median"] > 0


def test_a_losing_sequence_is_flagged():
    assert core.monte_carlo([-100.0, -100.0, -100.0, 20.0, -100.0])["risk_of_ruin"] > 0.5


def test_a_plateau_is_stable_and_a_spike_is_not():
    plateau = [result(pnl=value) for value in (100.0, 110.0, 95.0, 105.0)]
    assert core.parameter_stability(plateau)["stable"] is True
    spike = [result(pnl=value) for value in (-5000.0, 120.0, 90.0, 110.0)]
    assert core.parameter_stability(spike)["stable"] is False
    assert core.parameter_stability([result()])["stable"] is False


def test_a_configuration_with_too_few_trades_never_passes():
    score = core.robustness_score(result(trades=2), result())
    assert score["passes"] is False
    assert score["score"] == 0.0
    assert score["verdict"] == "Insufficient trades"


def test_a_configuration_that_holds_out_of_sample_can_pass():
    score = core.robustness_score(
        result(pnl=1000.0), result(pnl=800.0, trades=20),
        stability={"stable": True},
        simulation={"insufficient": False, "risk_of_ruin": 0.02})
    assert score["passes"] is True
    assert score["verdict"] == "Robust"
    assert set(score["components"]) == {"trade_count", "oos_coverage",
                                        "oos_consistency", "stability",
                                        "monte_carlo"}


def test_negative_out_of_sample_pnl_fails_with_an_explanation():
    score = core.robustness_score(result(pnl=1000.0), result(pnl=-50.0, trades=20))
    assert score["passes"] is False
    assert "not positive" in score["reason"]


def test_thin_out_of_sample_coverage_fails_explicitly():
    score = core.robustness_score(result(pnl=1000.0), result(pnl=500.0, trades=1))
    assert score["passes"] is False
    assert "out-of-sample trades" in score["reason"]


def test_failed_and_undersampled_runs_sort_below_valid_ones():
    ordered = core.rank([result(error="x"), result(trades=1), result(pnl=500.0)])
    assert ordered[0].trades == 12
    assert ordered[-1].error == "x"


def test_dataframe_marks_each_configuration_status():
    frame_out = core.to_dataframe([result(pnl=10.0), result(trades=1)])
    assert set(frame_out["status"]) == {"Passed", "Too few trades"}
    assert list(frame_out["realized_pnl"]) == sorted(
        frame_out["realized_pnl"], reverse=True)