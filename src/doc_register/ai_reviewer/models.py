from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..models import ExtractionResult


@dataclass(frozen=True)
class AIReviewRequest:
    result: ExtractionResult
    file_name: str
    fields: list[str]
    issues: list[str]
    evidence_text: str


@dataclass(frozen=True)
class AIFieldProposal:
    field_name: str
    proposed_value: str
    evidence: str
    confidence: float


@dataclass(frozen=True)
class AIFieldDecision:
    field_name: str
    original_value: str
    proposed_value: str
    decision: str
    confidence: float
    method: str
    evidence: str
    validation_results: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class AIReviewReport:
    attempted: bool = False
    status: str = "NOT_REQUIRED"
    model: str = ""
    reviewed_fields: list[str] = field(default_factory=list)
    accepted_fields: list[str] = field(default_factory=list)
    rejected_fields: list[str] = field(default_factory=list)
    human_required_fields: list[str] = field(default_factory=list)
    remaining_issues: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    reason: str = ""
    human_review_required: bool = False
    field_decisions: list[AIFieldDecision] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["duration_seconds"] = round(self.duration_seconds, 3)
        return payload
