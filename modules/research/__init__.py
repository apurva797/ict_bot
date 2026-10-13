"""Document research copilot domain."""

from .facts_store import FactCandidate, FactsStore, extract_fact_candidates
from .governance_flags import GovernanceFlagCandidate, extract_governance_candidates
from .service import ResearchDocument, answer_question, analyze_document, extract_document
from .thesis_reality import ThesisClaim, evaluate_thesis_claim, evaluate_thesis_claims

__all__ = [
    "FactCandidate",
    "GovernanceFlagCandidate",
    "extract_governance_candidates",
    "FactsStore",
    "ResearchDocument",
    "ThesisClaim",
    "evaluate_thesis_claim",
    "evaluate_thesis_claims",
    "answer_question",
    "analyze_document",
    "extract_document",
    "extract_fact_candidates",
]
