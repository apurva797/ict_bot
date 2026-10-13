"""Local, auditable facts store for Research Copilot.

Extracted facts are candidates, never silently treated as verified facts. The
original passage, physical PDF page, source hash and extraction method remain
attached so a reviewer can verify them against the source document.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Iterable

from modules.research.service import ResearchDocument

SCHEMA_VERSION = 1
DEFAULT_DB_PATH = Path(os.getenv("RESEARCH_FACTS_DB", "data/research_facts.sqlite3"))

# Deliberately conservative. A match is a review candidate, not a verified fact.
_METRICS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("revenue", re.compile(r"\b(revenue|turnover|sales|income from operations)\b", re.I)),
    ("ebitda", re.compile(r"\b(ebitda|operating profit before depreciation)\b", re.I)),
    ("net_profit", re.compile(r"\b(net profit|profit after tax|\bpat\b)", re.I)),
    ("operating_margin", re.compile(r"\b(operating margin|ebitda margin)\b", re.I)),
    ("debt", re.compile(r"\b(total debt|borrowings|gross debt|net debt)\b", re.I)),
    ("cash_flow_operations", re.compile(r"\b(cash flow from operating activities|operating cash flow)\b", re.I)),
    ("capital_expenditure", re.compile(r"\b(capital expenditure|capex)\b", re.I)),
    ("receivables", re.compile(r"\b(trade receivables|accounts receivable|receivables)\b", re.I)),
    ("inventory", re.compile(r"\b(inventory|inventories)\b", re.I)),
    ("promoter_pledge", re.compile(r"\b(promoter.{0,30}pledge|pledged.{0,30}promoter|shares pledged)\b", re.I)),
    ("related_party_transactions", re.compile(r"\b(related[- ]party transactions?)\b", re.I)),
)
_NUMBER = re.compile(
    r"(?P<currency>₹|INR|Rs\.?|USD|\$)?\s*"
    r"(?P<value>\(?-?\d[\d,]*(?:\.\d+)?\)?)\s*"
    r"(?P<scale>crore|crores|cr\.?|lakh|lakhs|million|billion|%)?",
    re.I,
)
_PERIOD = re.compile(r"\b(FY\s?\d{2,4}(?:\s?[-–/]\s?\d{2,4})?|Q[1-4]\s?FY?\s?\d{2,4}|\d{4}[-–/]\d{2,4})\b", re.I)


@dataclass(frozen=True)
class FactCandidate:
    fact_id: str
    company_name: str
    metric_name: str
    reported_value: str
    unit: str | None
    period: str | None
    source_document_id: str
    source_document_title: str
    source_document_hash: str
    page_number: int
    supporting_passage: str
    extraction_method: str = "keyword_numeric_candidate_v1"
    verification_status: str = "needs_review"
    source_url: str | None = None
    reporting_basis: str | None = None
    normalization_notes: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _clean_value(raw: str) -> str:
    value = raw.strip()
    if value.startswith("(") and value.endswith(")"):
        return "-" + value[1:-1].replace(",", "")
    return value.replace(",", "")


def extract_fact_candidates(document: ResearchDocument, company_name: str) -> list[FactCandidate]:
    """Extract reviewable metric candidates with page-accurate source evidence.

    The parser intentionally does not guess standalone/consolidated basis or
    convert units. It captures the source line and leaves verification pending.
    """
    company = company_name.strip()
    if not company:
        raise ValueError("Company name is required before extracting facts.")
    if not document.pages:
        raise ValueError("Document has no pages to inspect.")

    document_hash = sha256(document.text.encode("utf-8")).hexdigest()
    document_id = sha256(f"{document.name}:{document_hash}".encode("utf-8")).hexdigest()[:24]
    candidates: list[FactCandidate] = []
    seen: set[tuple[int, str, str, str | None]] = set()

    for page in document.pages:
        for raw_line in page.text.splitlines():
            line = " ".join(raw_line.split()).strip()
            if not line or len(line) < 8:
                continue
            period_match = _PERIOD.search(line)
            period = period_match.group(0) if period_match else None
            for metric_name, metric_pattern in _METRICS:
                metric_match = metric_pattern.search(line)
                if not metric_match:
                    continue
                number_match = _NUMBER.search(line, metric_match.end())
                if not number_match:
                    continue
                raw_value = _clean_value(number_match.group("value"))
                scale = (number_match.group("scale") or "").strip()
                currency = (number_match.group("currency") or "").strip()
                unit = " ".join(part for part in (currency, scale) if part) or None
                identity = (page.number, metric_name, raw_value, period)
                if identity in seen:
                    continue
                seen.add(identity)
                fact_id = sha256(
                    f"{document_id}|{page.number}|{metric_name}|{raw_value}|{period}|{line}".encode("utf-8")
                ).hexdigest()
                candidates.append(FactCandidate(
                    fact_id=fact_id,
                    company_name=company,
                    metric_name=metric_name,
                    reported_value=raw_value,
                    unit=unit,
                    period=period,
                    source_document_id=document_id,
                    source_document_title=document.name,
                    source_document_hash=document_hash,
                    page_number=page.number,
                    supporting_passage=line,
                ))
                # One numeric value per metric per line: avoids duplicating
                # comparison figures without knowing which one the label refers to.
                break
    return candidates


class FactsStore:
    """SQLite-backed local facts store with idempotent writes and provenance."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA user_version = 1")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS research_facts (
                    fact_id TEXT PRIMARY KEY,
                    company_name TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    reported_value TEXT NOT NULL,
                    normalized_value TEXT,
                    unit TEXT,
                    period TEXT,
                    reporting_basis TEXT,
                    source_document_id TEXT NOT NULL,
                    source_document_title TEXT NOT NULL,
                    source_document_hash TEXT NOT NULL,
                    source_url TEXT,
                    page_number INTEGER NOT NULL CHECK(page_number >= 1),
                    supporting_passage TEXT NOT NULL,
                    extraction_method TEXT NOT NULL,
                    verification_status TEXT NOT NULL CHECK(verification_status IN ('needs_review','verified','rejected')),
                    normalization_notes TEXT,
                    extracted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_facts_company_metric ON research_facts(company_name, metric_name)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_facts_source ON research_facts(source_document_id, page_number)")

    def save_candidates(self, candidates: Iterable[FactCandidate]) -> int:
        rows = [candidate.as_dict() for candidate in candidates]
        if not rows:
            return 0
        inserted = 0
        with self._connect() as connection:
            for row in rows:
                cursor = connection.execute("""
                    INSERT OR IGNORE INTO research_facts (
                        fact_id, company_name, metric_name, reported_value, unit, period,
                        reporting_basis, source_document_id, source_document_title,
                        source_document_hash, source_url, page_number, supporting_passage,
                        extraction_method, verification_status, normalization_notes
                    ) VALUES (
                        :fact_id, :company_name, :metric_name, :reported_value, :unit, :period,
                        :reporting_basis, :source_document_id, :source_document_title,
                        :source_document_hash, :source_url, :page_number, :supporting_passage,
                        :extraction_method, :verification_status, :normalization_notes
                    )
                """, row)
                inserted += cursor.rowcount
        return inserted

    def list_facts(self, company_name: str | None = None, limit: int = 500) -> list[dict]:
        if limit < 1 or limit > 5000:
            raise ValueError("limit must be between 1 and 5000")
        sql = "SELECT * FROM research_facts"
        params: tuple = ()
        if company_name and company_name.strip():
            sql += " WHERE company_name = ?"
            params = (company_name.strip(),)
        sql += " ORDER BY extracted_at DESC, page_number ASC LIMIT ?"
        params += (limit,)
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(sql, params).fetchall()]

    def set_verification_status(self, fact_id: str, status: str, *, normalized_value: str | None = None,
                                reporting_basis: str | None = None, notes: str | None = None) -> bool:
        """Set review metadata; only explicit human review may verify a candidate."""
        if status not in {"needs_review", "verified", "rejected"}:
            raise ValueError("status must be needs_review, verified, or rejected")
        with self._connect() as connection:
            cursor = connection.execute("""
                UPDATE research_facts
                SET verification_status = ?, normalized_value = ?, reporting_basis = ?, normalization_notes = ?
                WHERE fact_id = ?
            """, (status, normalized_value, reporting_basis, notes, fact_id))
            return cursor.rowcount == 1
