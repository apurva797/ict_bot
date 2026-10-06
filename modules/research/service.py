"""Page-aware document analysis and provider-backed Research Copilot."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
import re
from typing import BinaryIO

from pydantic import ValidationError

from core.ai.router import AIRouter
from core.ai.schemas import Citation, ResearchAnalysis, ResearchAnswer
from core.compliance.output_guard import guard_text


@dataclass(frozen=True)
class DocumentPage:
    number: int
    text: str


@dataclass(frozen=True)
class ResearchDocument:
    name: str
    pages: tuple[DocumentPage, ...]

    @property
    def text(self) -> str:
        return "\n".join(page.text for page in self.pages)


def extract_document(file: BinaryIO, name: str) -> ResearchDocument:
    """Extract text while preserving PDF page numbers."""
    if not name.lower().endswith(".pdf"):
        raise ValueError("Please upload a PDF document.")
    payload = file.read()
    if not payload:
        raise ValueError("The uploaded document is empty.")
    try:
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(payload))
    except ImportError as exc:
        raise RuntimeError("PDF support is unavailable. Install pypdf.") from exc
    except Exception as exc:
        raise ValueError("The PDF could not be read. It may be malformed or encrypted.") from exc
    pages = tuple(
        DocumentPage(number=index, text=(page.extract_text() or "").strip())
        for index, page in enumerate(reader.pages, start=1)
    )
    if not any(page.text for page in pages):
        raise ValueError("No readable text was found. The PDF may be image-only.")
    return ResearchDocument(name=name, pages=pages)


_TOPICS = {
    "Business model": ("revenue", "customer", "product", "segment", "geography"),
    "Financial trends": ("revenue", "ebitda", "pat", "cash flow", "debt", "margin"),
    "Risk signals": ("risk", "pledge", "auditor", "related party", "contingent", "liability"),
}


def _sentences(page: DocumentPage) -> list[str]:
    return [item.strip() for item in re.split(r"(?<=[.!?])\s+", page.text.replace("\n", " "))]


def _evidence(document: ResearchDocument, terms: tuple[str, ...], limit: int = 4) -> list[str]:
    rows: list[str] = []
    for page in document.pages:
        for sentence in _sentences(page):
            if any(term in sentence.lower() for term in terms):
                rows.append(f"{sentence} (Source: {document.name}, page {page.number})")
                if len(rows) >= limit:
                    return rows
    return rows


def evidence_chunks(document: ResearchDocument, limit: int = 40) -> list[dict]:
    """Build stable evidence records; providers cannot invent page metadata."""
    chunks: list[dict] = []
    for page in document.pages:
        for index, sentence in enumerate(_sentences(page), start=1):
            if len(sentence) < 20:
                continue
            chunks.append({
                "evidence_id": f"p{page.number}-s{index}",
                "document": document.name,
                "page": page.number,
                "text": sentence,
            })
            if len(chunks) >= limit:
                return chunks
    return chunks


def _prompt(chunks: list[dict]) -> tuple[str, str]:
    system = (
        "You are a cautious Indian investor education research assistant. "
        "Return JSON only matching the requested schema. Use simple Hinglish. "
        "Use only supplied evidence. If evidence is absent, write exactly "
        "'Not found in provided document'. Never give buy, sell, hold, target-price, "
        "or allocation advice. Put supplied evidence_id values in citations."
    )
    user = json.dumps({
        "task": "Create a one-page research analysis.",
        "required_fields": [
            "summary", "business_model", "financial_trends", "key_risks",
            "red_flags", "management_commentary", "uncertainty",
            "further_research", "citations",
        ],
        "evidence": chunks,
    }, ensure_ascii=False)
    return system, user


def _validated_analysis(raw: str, chunks: list[dict]) -> ResearchAnalysis:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        parsed = ResearchAnalysis.model_validate_json(cleaned)
    except ValidationError as exc:
        raise ValueError("AI returned an invalid research structure.") from exc
    by_id = {item["evidence_id"]: item for item in chunks}
    citations = [
        Citation(
            evidence_id=item["evidence_id"],
            document=item["document"],
            page=item["page"],
            evidence=item["text"],
        )
        for citation in parsed.citations
        if (item := by_id.get(citation.evidence_id)) is not None
    ]
    text_fields = ("summary", "business_model", "financial_trends", "management_commentary", "uncertainty")
    list_fields = ("key_risks", "red_flags", "further_research")
    values = {field: guard_text(getattr(parsed, field))[0] for field in text_fields}
    values.update({field: [guard_text(item)[0] for item in getattr(parsed, field)] for field in list_fields})
    return ResearchAnalysis(**values, citations=citations)


def analyze_with_ai(document: ResearchDocument, router: AIRouter | None = None) -> tuple[ResearchAnalysis, AIRouter]:
    """Generate, validate, cite, and compliance-filter an analysis."""
    active_router = router or AIRouter()
    chunks = evidence_chunks(document)
    if not chunks:
        raise ValueError("Document me reliable information nahi mila.")
    system, prompt = _prompt(chunks)
    response = active_router.generate(system_prompt=system, user_prompt=prompt)
    return _validated_analysis(response.raw_text, chunks), active_router


def answer_question_with_ai(
    document: ResearchDocument,
    question: str,
    router: AIRouter | None = None,
) -> tuple[str, list[Citation], AIRouter]:
    """Answer through the configured provider, validating citations locally."""
    if not question.strip():
        raise ValueError("Please enter a question.")
    active_router = router or AIRouter()
    chunks = evidence_chunks(document)
    system = (
        "Answer only from supplied evidence in simple Hinglish. Return JSON only "
        "with answer and citation_ids. If evidence is insufficient, answer exactly "
        "'Document me reliable information nahi mila.'. Never give investment advice."
    )
    prompt = json.dumps({"task": "Answer a document question", "question": question, "evidence": chunks}, ensure_ascii=False)
    response = active_router.generate(system_prompt=system, user_prompt=prompt)
    try:
        parsed = ResearchAnswer.model_validate_json(response.raw_text.strip().strip("`"))
    except ValidationError as exc:
        raise ValueError("AI returned an invalid answer structure.") from exc
    by_id = {item["evidence_id"]: item for item in chunks}
    citations = [
        Citation(evidence_id=item["evidence_id"], document=item["document"], page=item["page"], evidence=item["text"])
        for citation_id in parsed.citation_ids
        if (item := by_id.get(citation_id)) is not None
    ]
    return guard_text(parsed.answer)[0], citations, active_router


def analyze_document(document: ResearchDocument) -> dict[str, list[str]]:
    """Preserved deterministic evidence cards from the previous milestone."""
    return {
        topic: _evidence(document, terms) or ["Document me reliable information nahi mila."]
        for topic, terms in _TOPICS.items()
    }


def answer_question(document: ResearchDocument, question: str) -> str:
    """Preserved deterministic, cited fallback Q&A."""
    question = question.strip()
    if not question:
        raise ValueError("Please enter a question.")
    words = {word.lower() for word in re.findall(r"[A-Za-z0-9]+", question) if len(word) > 3}
    matches = []
    for page in document.pages:
        for sentence in _sentences(page):
            if len(words.intersection(re.findall(r"[a-z0-9]+", sentence.lower()))) >= 1:
                matches.append(f"{sentence} (Source: {document.name}, page {page.number})")
            if len(matches) == 3:
                break
        if len(matches) == 3:
            break
    answer = "\n\n".join(matches) if matches else "Document me reliable information nahi mila."
    return guard_text(answer)[0]
