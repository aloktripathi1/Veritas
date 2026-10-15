"""Typed data shapes shared across the pipeline."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ClaimType = Literal["reported_fact", "management_statement", "forecast", "third_party_allegation", "opinion", "other"]


class Document(BaseModel):
    doc_id: str                      # deterministic: D1..Dn by sorted filename
    filename: str
    source: str = ""
    url: str = ""
    published: str = ""              # ISO date as given in front matter
    doc_type: str = ""
    lines: list[str]                 # full file lines (1-indexed via lines[i-1])
    body_start: int = 1              # first line after the front matter
    relevant: bool = True
    relevance_reason: str = ""
    injection_flags: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    """One verified claim. `quote` has been checked to appear verbatim in the document."""
    evidence_id: str                 # E01..
    doc_id: str
    line_start: int
    line_end: int
    quote: str
    metric: str = ""                 # canonical key, e.g. revenue, promoter_pledge_pct_of_promoter_holding
    entity: str = ""
    value: float | None = None
    unit: str = ""
    period: str = ""                 # e.g. "Q1 FY27", "as of 30 Jun 2026"
    claim_type: ClaimType = "other"
    note: str = ""


class Conflict(BaseModel):
    metric: str
    period: str
    evidence_ids: list[str]
    values: list[float]
    kind: Literal["numeric_conflict", "stale_vs_new"]
    explanation: str


class Issue(BaseModel):
    severity: Literal["error", "warning"]
    code: str
    message: str
