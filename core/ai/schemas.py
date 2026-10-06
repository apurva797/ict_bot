"""Validated structured output for research analysis."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class Citation(BaseModel):
    evidence_id: str
    document: str
    page: int = Field(ge=1)
    evidence: str


class ResearchAnalysis(BaseModel):
    summary: str
    business_model: str
    financial_trends: str
    key_risks: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    management_commentary: str
    uncertainty: str
    further_research: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)

    @field_validator("summary", "business_model", "financial_trends",
                     "management_commentary", "uncertainty")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Research section cannot be empty.")
        return value.strip()


class ResearchAnswer(BaseModel):
    answer: str
    citation_ids: list[str] = Field(default_factory=list)

    @field_validator("answer")
    @classmethod
    def require_answer(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Research answer cannot be empty.")
        return value.strip()
