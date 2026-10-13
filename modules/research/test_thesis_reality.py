from modules.research.thesis_reality import ThesisClaim, evaluate_thesis_claim


def _fact(period, value, *, status="verified", unit="INR crore", basis="consolidated", page=5):
    return {
        "fact_id": f"{period}-{value}", "company_name": "Example Ltd", "metric_name": "revenue",
        "period": period, "reported_value": str(value), "normalized_value": str(value),
        "unit": unit, "reporting_basis": basis, "verification_status": status,
        "source_document_title": f"annual-report-{period}.pdf", "page_number": page,
    }


def _claim(direction="increase", tolerance=5):
    return ThesisClaim("claim-1", "Example Ltd", "revenue", "Revenue grows year on year", direction,
                       "FY 2024-25", "FY 2025-26", tolerance)


def test_increasing_revenue_claim_holds_with_verified_sources():
    result = evaluate_thesis_claim(_claim(), [_fact("FY 2024-25", 100), _fact("FY 2025-26", 120, page=8)])
    assert result["status"] == "Holding"
    assert result["change_pct"] == 20
    assert result["sources"][1]["page"] == 8
    assert result["disclaimer"] == "Educational tool. Not investment advice."


def test_claim_is_no_data_when_facts_are_unverified():
    result = evaluate_thesis_claim(_claim(), [_fact("FY 2024-25", 100), _fact("FY 2025-26", 120, status="needs_review")])
    assert result["status"] == "No Data"
    assert result["change_pct"] is None


def test_negative_growth_breaks_increase_thesis():
    result = evaluate_thesis_claim(_claim(), [_fact("FY 2024-25", 100), _fact("FY 2025-26", 90)])
    assert result["status"] == "Broken"
    assert result["change_pct"] == -10


def test_small_growth_weakens_but_does_not_break_increase_thesis():
    result = evaluate_thesis_claim(_claim(), [_fact("FY 2024-25", 100), _fact("FY 2025-26", 102)])
    assert result["status"] == "Weakening"


def test_mismatched_reporting_basis_returns_no_data():
    result = evaluate_thesis_claim(_claim(), [_fact("FY 2024-25", 100, basis="standalone"), _fact("FY 2025-26", 120, basis="consolidated")])
    assert result["status"] == "No Data"


def test_ambiguous_duplicate_values_return_no_data():
    result = evaluate_thesis_claim(_claim(), [_fact("FY 2024-25", 100), _fact("FY 2025-26", 120), _fact("FY 2025-26", 130)])
    assert result["status"] == "No Data"
