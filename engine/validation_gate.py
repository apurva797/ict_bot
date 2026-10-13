"""Evidence-based strategy validation gate for Arjuna.

This module does not run or fabricate backtests. It evaluates metrics produced by
separate in-sample/out-of-sample, walk-forward, cost-aware, and parameter-sensitivity
runs. Missing evidence results in INCOMPLETE, never an optimistic pass.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from math import isfinite
from typing import Mapping, Sequence


class ValidationStatus(str, Enum):
    ELIGIBLE = "Eligible"
    REJECTED = "Rejected"
    INCOMPLETE = "Incomplete"


@dataclass(frozen=True)
class ValidationThresholds:
    minimum_total_trades: int = 100
    minimum_oos_trades: int = 30
    maximum_drawdown_pct: float = 20.0
    minimum_oos_return_pct: float = 0.0
    minimum_walk_forward_folds: int = 4
    minimum_positive_fold_ratio: float = 0.60
    maximum_parameter_sensitivity_degradation_pct: float = 30.0

    def __post_init__(self) -> None:
        if self.minimum_total_trades < 1 or self.minimum_oos_trades < 1:
            raise ValueError("Trade thresholds must be positive integers.")
        if self.maximum_drawdown_pct <= 0:
            raise ValueError("maximum_drawdown_pct must be positive.")
        if self.minimum_walk_forward_folds < 1:
            raise ValueError("minimum_walk_forward_folds must be at least 1.")
        if not 0 <= self.minimum_positive_fold_ratio <= 1:
            raise ValueError("minimum_positive_fold_ratio must be between 0 and 1.")
        if self.maximum_parameter_sensitivity_degradation_pct < 0:
            raise ValueError("Sensitivity degradation threshold cannot be negative.")


@dataclass(frozen=True)
class ValidationEvidence:
    """Measured results supplied by the backtest/validation pipeline.

    Drawdown is an absolute positive percentage (e.g. 12.5 means 12.5%).
    ``fees_slippage_included`` must be true only when both costs were applied.
    Walk-forward values are net fold returns in percent after costs.
    Parameter sensitivity is the worst relative performance degradation, percent,
    versus the selected baseline under nearby parameter configurations.
    """
    strategy_id: str
    total_trades: int | None = None
    oos_trades: int | None = None
    oos_return_pct: float | None = None
    max_drawdown_pct: float | None = None
    fees_slippage_included: bool | None = None
    walk_forward_fold_returns_pct: tuple[float, ...] | None = None
    parameter_sensitivity_degradation_pct: float | None = None
    out_of_sample_evaluated: bool | None = None
    evidence_reference: str | None = None


@dataclass(frozen=True)
class ValidationCheck:
    name: str
    status: str  # PASS, FAIL, MISSING
    detail: str


@dataclass(frozen=True)
class ValidationReport:
    strategy_id: str
    status: ValidationStatus
    checks: tuple[ValidationCheck, ...]
    reasons: tuple[str, ...]
    thresholds: ValidationThresholds
    evidence_reference: str | None = None

    def as_dict(self) -> dict:
        result = asdict(self)
        result["status"] = self.status.value
        result["thresholds"] = asdict(self.thresholds)
        result["checks"] = [asdict(check) for check in self.checks]
        return result


def _finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value))


def evaluate_strategy(
    evidence: ValidationEvidence,
    thresholds: ValidationThresholds | None = None,
) -> ValidationReport:
    """Return Eligible only when every required gate has measured passing evidence.

    Any measured threshold failure rejects the strategy. Missing or invalid evidence
    yields Incomplete unless another check already establishes a rejection.
    """
    limits = thresholds or ValidationThresholds()
    checks: list[ValidationCheck] = []

    def add(name: str, value: bool | None, pass_detail: str, fail_detail: str, missing_detail: str) -> None:
        if value is None:
            checks.append(ValidationCheck(name, "MISSING", missing_detail))
        elif value:
            checks.append(ValidationCheck(name, "PASS", pass_detail))
        else:
            checks.append(ValidationCheck(name, "FAIL", fail_detail))

    total = evidence.total_trades
    add("minimum_total_trades",
        None if total is None else total >= limits.minimum_total_trades,
        f"{total} total trades meets minimum {limits.minimum_total_trades}.",
        f"{total} total trades is below minimum {limits.minimum_total_trades}.",
        "Total trade count was not supplied.")

    oos_count = evidence.oos_trades
    add("minimum_oos_trades",
        None if oos_count is None else oos_count >= limits.minimum_oos_trades,
        f"{oos_count} out-of-sample trades meets minimum {limits.minimum_oos_trades}.",
        f"{oos_count} out-of-sample trades is below minimum {limits.minimum_oos_trades}.",
        "Out-of-sample trade count was not supplied.")

    add("out_of_sample_test",
        evidence.out_of_sample_evaluated,
        "A separate out-of-sample evaluation is recorded.",
        "A separate out-of-sample evaluation is required.",
        "No explicit evidence that a separate out-of-sample period was evaluated.")

    oos_return = evidence.oos_return_pct
    valid_return = _finite_number(oos_return)
    add("oos_net_return",
        None if oos_return is None else (valid_return and float(oos_return) > limits.minimum_oos_return_pct),
        f"OOS net return {oos_return}% is above required {limits.minimum_oos_return_pct}%.",
        f"OOS net return {oos_return}% does not exceed required {limits.minimum_oos_return_pct}% or is invalid.",
        "Out-of-sample net return was not supplied.")

    drawdown = evidence.max_drawdown_pct
    valid_dd = _finite_number(drawdown) and float(drawdown) >= 0
    add("maximum_drawdown",
        None if drawdown is None else (valid_dd and float(drawdown) <= limits.maximum_drawdown_pct),
        f"Max drawdown {drawdown}% is within {limits.maximum_drawdown_pct}% limit.",
        f"Max drawdown {drawdown}% exceeds {limits.maximum_drawdown_pct}% or is invalid.",
        "Maximum drawdown was not supplied.")

    add("fees_and_slippage",
        evidence.fees_slippage_included,
        "Both fees and slippage are included in measured results.",
        "Results explicitly exclude fees and/or slippage.",
        "Cost inclusion has not been confirmed.")

    add("evidence_reference",
        None if not (evidence.evidence_reference and evidence.evidence_reference.strip()) else True,
        "A run/report reference is recorded for auditability.",
        "Evidence reference is empty.",
        "No run/report reference was supplied.")

    folds = evidence.walk_forward_fold_returns_pct
    if folds is None:
        add("walk_forward", None, "", "", "Walk-forward fold results were not supplied.")
    elif len(folds) < limits.minimum_walk_forward_folds:
        checks.append(ValidationCheck(
            "walk_forward", "FAIL",
            f"Only {len(folds)} walk-forward folds; minimum is {limits.minimum_walk_forward_folds}."))
    elif any(not _finite_number(item) for item in folds):
        checks.append(ValidationCheck("walk_forward", "FAIL", "Walk-forward folds contain invalid or non-finite values."))
    else:
        positive_ratio = sum(float(item) > 0 for item in folds) / len(folds)
        passed = positive_ratio >= limits.minimum_positive_fold_ratio
        checks.append(ValidationCheck(
            "walk_forward", "PASS" if passed else "FAIL",
            f"{positive_ratio:.0%} of {len(folds)} folds were profitable; required {limits.minimum_positive_fold_ratio:.0%}."))

    sensitivity = evidence.parameter_sensitivity_degradation_pct
    valid_sensitivity = _finite_number(sensitivity) and float(sensitivity) >= 0
    add("parameter_sensitivity",
        None if sensitivity is None else (valid_sensitivity and float(sensitivity) <= limits.maximum_parameter_sensitivity_degradation_pct),
        f"Worst nearby-parameter degradation {sensitivity}% is within {limits.maximum_parameter_sensitivity_degradation_pct}%.",
        f"Parameter sensitivity degradation {sensitivity}% exceeds {limits.maximum_parameter_sensitivity_degradation_pct}% or is invalid.",
        "Parameter sensitivity test was not supplied.")

    failed = [check for check in checks if check.status == "FAIL"]
    missing = [check for check in checks if check.status == "MISSING"]
    if failed:
        status = ValidationStatus.REJECTED
    elif missing:
        status = ValidationStatus.INCOMPLETE
    else:
        status = ValidationStatus.ELIGIBLE
    reasons = tuple(check.detail for check in checks if check.status != "PASS")
    return ValidationReport(
        strategy_id=evidence.strategy_id.strip() or "unnamed_strategy",
        status=status,
        checks=tuple(checks),
        reasons=reasons,
        thresholds=limits,
        evidence_reference=evidence.evidence_reference,
    )
