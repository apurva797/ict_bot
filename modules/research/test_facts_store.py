from modules.research.facts_store import FactsStore, extract_fact_candidates
from modules.research.service import DocumentPage, ResearchDocument


def test_extracts_metric_with_exact_source_page_and_unverified_status():
    document = ResearchDocument(
        "annual-report.pdf",
        (
            DocumentPage(4, "Business overview with no financial metric."),
            DocumentPage(9, "Revenue from operations ₹ 1,234 crore in FY 2025-26."),
        ),
    )
    facts = extract_fact_candidates(document, "Example Industries Ltd.")
    assert len(facts) == 1
    fact = facts[0]
    assert fact.metric_name == "revenue"
    assert fact.reported_value == "1234"
    assert fact.page_number == 9
    assert fact.period == "FY 2025-26"
    assert fact.source_document_title == "annual-report.pdf"
    assert fact.supporting_passage.startswith("Revenue from operations")
    assert fact.verification_status == "needs_review"


def test_extraction_requires_company_name():
    document = ResearchDocument("report.pdf", (DocumentPage(1, "Revenue was 100 crore."),))
    try:
        extract_fact_candidates(document, " ")
    except ValueError as exc:
        assert "Company name" in str(exc)
    else:
        raise AssertionError("Blank company name must be rejected")


def test_store_is_idempotent_and_retains_provenance(tmp_path):
    document = ResearchDocument("report.pdf", (DocumentPage(3, "EBITDA was 42 crore in FY 2025-26."),))
    candidates = extract_fact_candidates(document, "Example Ltd")
    store = FactsStore(tmp_path / "facts.sqlite3")
    assert store.save_candidates(candidates) == 1
    assert store.save_candidates(candidates) == 0
    rows = store.list_facts("Example Ltd")
    assert len(rows) == 1
    assert rows[0]["page_number"] == 3
    assert rows[0]["verification_status"] == "needs_review"
    assert rows[0]["source_document_hash"]
    assert rows[0]["supporting_passage"] == "EBITDA was 42 crore in FY 2025-26."


def test_human_review_status_is_validated_and_saved(tmp_path):
    document = ResearchDocument("report.pdf", (DocumentPage(1, "Revenue was 100 crore."),))
    fact = extract_fact_candidates(document, "Example Ltd")[0]
    store = FactsStore(tmp_path / "facts.sqlite3")
    store.save_candidates([fact])
    assert store.set_verification_status(
        fact.fact_id, "verified", normalized_value="100", reporting_basis="consolidated",
        notes="Checked against the cited table.",
    ) is True
    saved = store.list_facts("Example Ltd")[0]
    assert saved["verification_status"] == "verified"
    assert saved["reporting_basis"] == "consolidated"
    assert saved["normalized_value"] == "100"


def test_unknown_fact_cannot_be_marked_with_invalid_status(tmp_path):
    store = FactsStore(tmp_path / "facts.sqlite3")
    try:
        store.set_verification_status("unknown", "approved")
    except ValueError as exc:
        assert "status must be" in str(exc)
    else:
        raise AssertionError("Unsupported verification status must be rejected")
