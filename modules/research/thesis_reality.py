"""Deterministic Thesis vs Reality checks using only human-verified facts.

This module is deliberately rules-based. It does not infer facts from prose or
use an LLM to assign a status. Each result retains source document and page
references so the reviewer can inspect the evidence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Iterable, Mapping

STATUSES = {"Holding", "Weakening", "Broken", "No Data"}
DIRECTIONS = {"increase", "decrease", "stable"}


@dataclass(frozen=True)
class ThesisClaim:
    claim_id: str
    company_name: str
    metric_name: str
    thesis_text: str
    expected_direction: str
    baseline_period: str
    latest_period: str
    tolerance_pct: float = 5.0

    def as_dict(self) -> dict:
        return asdict(self)


def _number(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    # Parenthesized accounting values represent negatives.
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    text = text.replace("₹", "").replace("INR", "").replace("Rs.", "").strip()
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _period_fact(facts: Iterable[Mapping], company: str, metric: str, period: str) -> dict | None:
    # Only explicitly human-verified facts can support a thesis status.
    matches = [
        dict(fact) for fact in facts
        if str(fact.get("company_name", "")).casefold() == company.casefold()
        and str(fact.get("metric_name", "")).casefold() == metric.casefold()
        and str(fact.get("period", "")).casefold() == period.casefold()
        and fact.get("verification_status") == "verified"
    ]
    # Ambiguous duplicates must not be guessed between. Require one distinct
    # numeric value, unit, and reporting basis for the requested period.
    usable = []
    for fact in matches:
        value = _number(fact.get("normalized_value"))
        if value is None:
            value = _number(fact.get("reported_value"))
        if value is not None:
            usable.append((fact, value))
    signatures = {
        (value, str(fact.get("unit") or "").casefold(), str(fact.get("reporting_basis") or "").casefold())
        for fact, value in usable
    }
    if len(signatures) != 1:
        return None
    signature = next(iter(signatures))
    for fact, value in usable:
        if (value, str(fact.get("unit") or "").casefold(), str(fact.get("reporting_basis") or "").casefold()) == signature:
            return {"fact": fact, "value": value}
    return None


def evaluate_thesis_claim(claim: ThesisClaim, facts: Iterable[Mapping]) -> dict:
    """Evaluate one claim; absent, unverified, ambiguous or incompatible facts yield No Data."""
    if claim.expected_direction not in DIRECTIONS:
        raise ValueError(f"expected_direction must be one of {sorted(DIRECTIONS)}")
    if not 0 <= claim.tolerance_pct <= 100:
        raise ValueError("tolerance_pct must be between 0 and 100")
    if not claim.company_name.strip() or not claim.metric_name.strip():
        raise ValueError("Company and metric are required")
    if not claim.baseline_period.strip() or not claim.latest_period.strip() or claim.baseline_period == claim.latest_period:
        raise ValueError("Choose two different reporting periods")

    fact_rows = list(facts)
    baseline = _period_fact(fact_rows, claim.company_name, claim.metric_name, claim.baseline_period)
    latest = _period_fact(fact_rows, claim.company_name, claim.metric_name, claim.latest_period)
    if baseline is None or latest is None:
        return _result(claim, "No Data", None, None, None, [],
                       "Two unambiguous, human-verified values for both periods were not available.")

    before, after = baseline["value"], latest["value"]
    baseline_fact, latest_fact = baseline["fact"], latest["fact"]
    if before == 0:
        return _result(claim, "No Data", before, after, None, [baseline_fact, latest_fact],
                       "Percentage change cannot be calculated from a zero baseline.")
    # Values are only comparable when unit and reporting basis match.
    baseline_unit = str(baseline_fact.get("unit") or "").strip().casefold()
    latest_unit = str(latest_fact.get("unit") or "").strip().casefold()
    baseline_basis = str(baseline_fact.get("reporting_basis") or "").strip().casefold()
    latest_basis = str(latest_fact.get("reporting_basis") or "").strip().casefold()
    same_unit = bool(baseline_unit) and baseline_unit == latest_unit
    same_basis = bool(baseline_basis) and baseline_basis == latest_basis
    if not same_unit or not same_basis:
        return _result(claim, "No Data", before, after, None, [baseline_fact, latest_fact],
                       "Units or standalone/consolidated reporting basis differ or are unspecified inconsistently.")

    change_pct = ((after - before) / abs(before)) * 100.0
    tolerance = claim.tolerance_pct
    if claim.expected_direction == "increase":
        status = "Holding" if change_pct >= tolerance else ("Weakening" if change_pct >= 0 else "Broken")
    elif claim.expected_direction == "decrease":
        status = "Holding" if change_pct <= -tolerance else ("Weakening" if change_pct <= 0 else "Broken")
    else:  # stable
        magnitude = abs(change_pct)
        status = "Holding" if magnitude <= tolerance else ("Weakening" if magnitude <= tolerance * 2 else "Broken")

    return _result(claim, status, before, after, change_pct, [baseline_fact, latest_fact],
                   f"Rule-based comparison: expected {claim.expected_direction}; tolerance ±{tolerance:g}%.")


def _result(claim: ThesisClaim, status: str, baseline_value: float | None,
            latest_value: float | None, change_pct: float | None, facts: list[dict], explanation: str) -> dict:
    sources = [
        {
            "document": fact.get("source_document_title"),
            "page": fact.get("page_number"),
            "period": fact.get("period"),
            "value": fact.get("normalized_value") or fact.get("reported_value"),
            "unit": fact.get("unit"),
            "reporting_basis": fact.get("reporting_basis"),
            "fact_id": fact.get("fact_id"),
        }
        for fact in facts
    ]
    return {
        "claim_id": claim.claim_id,
        "company_name": claim.company_name,
        "thesis_text": claim.thesis_text,
        "metric_name": claim.metric_name,
        "expected_direction": claim.expected_direction,
        "baseline_period": claim.baseline_period,
        "latest_period": claim.latest_period,
        "baseline_value": baseline_value,
        "latest_value": latest_value,
        "change_pct": change_pct,
        "status": status,
        "explanation": explanation,
        "sources": sources,
        "disclaimer": "Educational tool. Not investment advice.",
    }


def evaluate_thesis_claims(claims: Iterable[ThesisClaim], facts: Iterable[Mapping]) -> list[dict]:
    rows = list(facts)
    return [evaluate_thesis_claim(claim, rows) for claim in claims]
