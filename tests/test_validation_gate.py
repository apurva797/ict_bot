from engine.validation_gate import (
    ValidationEvidence, ValidationStatus, ValidationThresholds, evaluate_strategy,
)


def passing_evidence(**overrides):
    values = dict(
        strategy_id="demo_strategy",
        total_trades=140,
        oos_trades=42,
        oos_return_pct=8.2,
        max_drawdown_pct=12.0,
        fees_slippage_included=True,
        walk_forward_fold_returns_pct=(2.1, 1.2, -0.3, 3.0, 0.5),
        parameter_sensitivity_degradation_pct=18.0,
        out_of_sample_evaluated=True,
        evidence_reference="run://validation-001",
    )
    values.update(overrides)
    return ValidationEvidence(**values)


def test_all_required_gates_pass_for_eligible_strategy():
    report = evaluate_strategy(passing_evidence())
    assert report.status is ValidationStatus.ELIGIBLE
    assert all(check.status == "PASS" for check in report.checks)
    assert report.as_dict()["status"] == "Eligible"


def test_missing_evidence_is_incomplete_not_eligible():
    report = evaluate_strategy(ValidationEvidence(strategy_id="missing_data"))
    assert report.status is ValidationStatus.INCOMPLETE
    assert any(check.status == "MISSING" for check in report.checks)


def test_drawdown_failure_rejects_strategy():
    report = evaluate_strategy(passing_evidence(max_drawdown_pct=24.0))
    assert report.status is ValidationStatus.REJECTED
    assert next(c for c in report.checks if c.name == "maximum_drawdown").status == "FAIL"


def test_costs_excluded_reject_strategy():
    assert evaluate_strategy(passing_evidence(fees_slippage_included=False)).status is ValidationStatus.REJECTED


def test_too_few_oos_trades_reject_strategy():
    assert evaluate_strategy(passing_evidence(oos_trades=10)).status is ValidationStatus.REJECTED


def test_walk_forward_requires_enough_folds():
    report = evaluate_strategy(passing_evidence(walk_forward_fold_returns_pct=(1.0, 2.0)))
    assert report.status is ValidationStatus.REJECTED


def test_walk_forward_positive_ratio_threshold_is_enforced():
    report = evaluate_strategy(passing_evidence(walk_forward_fold_returns_pct=(-1.0, -2.0, 1.0, -3.0)))
    assert report.status is ValidationStatus.REJECTED


def test_parameter_sensitivity_failure_rejects_strategy():
    assert evaluate_strategy(passing_evidence(parameter_sensitivity_degradation_pct=45.0)).status is ValidationStatus.REJECTED


def test_non_finite_metrics_never_pass():
    report = evaluate_strategy(passing_evidence(oos_return_pct=float("nan")))
    assert report.status is ValidationStatus.REJECTED


def test_thresholds_are_configurable():
    limits = ValidationThresholds(minimum_total_trades=50, minimum_oos_trades=20, maximum_drawdown_pct=25)
    report = evaluate_strategy(passing_evidence(total_trades=60, oos_trades=25, max_drawdown_pct=24), limits)
    assert report.status is ValidationStatus.ELIGIBLE
