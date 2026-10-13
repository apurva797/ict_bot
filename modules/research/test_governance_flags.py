from modules.research.governance_flags import extract_governance_candidates
from modules.research.service import DocumentPage, ResearchDocument


def test_flags_are_review_candidates_with_page_evidence():
    document = ResearchDocument(name="annual.pdf", pages=(
        DocumentPage(number=1, text="The statutory auditor resigned during the year."),
        DocumentPage(number=2, text="The auditor gave a qualified opinion on the statements."),
    ))
    results = extract_governance_candidates(document)
    assert {item.category for item in results} == {"auditor_change", "qualified_audit_opinion"}
    assert all(item.review_status == "needs_review" for item in results)
    assert {item.page_number for item in results} == {1, 2}
    assert all(item.evidence for item in results)


def test_no_match_does_not_invent_flags():
    document = ResearchDocument(name="annual.pdf", pages=(DocumentPage(number=1, text="The company manufactures industrial pumps."),))
    assert extract_governance_candidates(document) == []


def test_explicit_promoter_pledge_increase_is_detected():
    document = ResearchDocument(name="annual.pdf", pages=(DocumentPage(number=7, text="Promoter share pledge increased during the reporting year."),))
    results = extract_governance_candidates(document)
    assert any(item.category == "pledge_increase" for item in results)
