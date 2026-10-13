"""Evidence-linked governance signal candidates; never automatic allegations.

A phrase match is a review prompt, not proof of misconduct. Every candidate
retains its source page and quoted evidence and must be human-reviewed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import re
from typing import Iterable

from modules.research.service import ResearchDocument


@dataclass(frozen=True)
class GovernanceFlagCandidate:
    candidate_id: str
    category: str
    signal: str
    source_document: str
    page_number: int
    evidence: str
    review_status: str = "needs_review"

    def as_dict(self) -> dict:
        return asdict(self)


_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("auditor_change", "Auditor appointment, resignation, or change mentioned", re.compile(r"\b(auditor.{0,45}(resign|resigned|resignation|appoint|appointed|change|changed|retir)|resign.{0,45}auditor)\b", re.I)),
    ("qualified_audit_opinion", "Qualified/adverse audit opinion or disclaimer mentioned", re.compile(r"\b(qualified opinion|adverse opinion|disclaimer of opinion|modified opinion|emphasis of matter)\b", re.I)),
    ("promoter_pledge", "Promoter share pledge/release mentioned", re.compile(r"\b(promoter.{0,35}pledged|pledge of promoter|promoter.{0,25}pledge|shares pledged by promoters?)\b", re.I)),
    ("related_party_transactions", "Related-party transaction/amount mentioned", re.compile(r"\b(related[- ]party transaction|transactions with related parties|related parties)\b", re.I)),
    ("board_exit", "Director or key board member exit mentioned", re.compile(r"\b(director.{0,40}(resign|resigned|resignation|ceased to be)|resignation of (an? )?(independent )?director|independent director.{0,35}resign)\b", re.I)),
    ("pledge_increase", "Increase in promoter pledge explicitly mentioned", re.compile(r"\b(increase[d]?|rose|increased from|higher).{0,45}(promoter.{0,20}pledge|pledged shares|shares pledged)\b|\b(promoter.{0,20}pledge|pledged shares|shares pledged).{0,45}(increase[d]?|rose|higher)\b", re.I)),
)


def extract_governance_candidates(document: ResearchDocument) -> list[GovernanceFlagCandidate]:
    """Return source-linked review candidates from readable page text.

    Sentence splitting is conservative and candidates are not classified as
    confirmed red flags. Repeated identical category/evidence/page is deduped.
    """
    if not document.pages:
        raise ValueError("Document has no pages to inspect.")
    results: list[GovernanceFlagCandidate] = []
    seen: set[tuple[str, int, str]] = set()
    for page in document.pages:
        text = re.sub(r"\s+", " ", page.text).strip()
        if not text:
            continue
        sentences = re.split(r"(?<=[.!?;])\s+", text)
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 12:
                continue
            for category, signal, pattern in _RULES:
                if not pattern.search(sentence):
                    continue
                identity = (category, page.number, sentence.casefold())
                if identity in seen:
                    continue
                seen.add(identity)
                candidate_id = hashlib.sha256(
                    f"{document.name}|{page.number}|{category}|{sentence}".encode("utf-8")
                ).hexdigest()
                results.append(GovernanceFlagCandidate(
                    candidate_id=candidate_id,
                    category=category,
                    signal=signal,
                    source_document=document.name,
                    page_number=page.number,
                    evidence=sentence,
                ))
    return results
