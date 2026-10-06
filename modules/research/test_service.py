from io import BytesIO

import pytest

from modules.research.service import (
    DocumentPage,
    ResearchDocument,
    analyze_document,
    analyze_with_ai,
    answer_question,
    extract_document,
)
from core.ai.router import AIRouter
from core.ai.providers.mock import MockProvider


def test_non_pdf_is_rejected():
    with pytest.raises(ValueError, match="PDF"):
        extract_document(BytesIO(b"text"), "notes.txt")


def test_analysis_keeps_page_citations():
    document = ResearchDocument(
        "annual-report.pdf",
        (DocumentPage(7, "Revenue increased while debt declined."),),
    )
    result = analyze_document(document)
    assert "page 7" in result["Financial trends"][0]


def test_missing_answer_is_explicit():
    document = ResearchDocument("report.pdf", (DocumentPage(1, "Business overview."),))
    assert answer_question(document, "What is the dividend?") == (
        "Document me reliable information nahi mila."
    )


def test_mock_analysis_is_structured_and_uses_real_citation_metadata():
    document = ResearchDocument(
        "report.pdf",
        (DocumentPage(7, "Revenue increased while debt declined during the year."),),
    )
    analysis, router = analyze_with_ai(document, AIRouter(MockProvider()))
    assert router.status.active == "mock"
    assert analysis.citations[0].page == 7
    assert analysis.citations[0].document == "report.pdf"
