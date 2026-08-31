from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class VisionPagePlan:
    page_number: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class VisionPlan:
    eligible: bool
    reason: str
    fields: tuple[str, ...] = ()
    pages: tuple[VisionPagePlan, ...] = ()


@dataclass(frozen=True)
class RenderedPage:
    page_number: int
    image_base64: str
    image_sha256: str
    width: int
    height: int


@dataclass(frozen=True)
class VisionFieldCandidate:
    field_name: str
    proposed_value: str
    evidence: str
    page_number: int
    confidence: float
    content_type: str = "printed"
    block_id: str = ""
    uncertain_tokens: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["uncertain_tokens"] = list(self.uncertain_tokens)
        payload["confidence"] = round(float(self.confidence), 3)
        return payload


@dataclass
class VisionRecoveryReport:
    attempted: bool = False
    status: str = "NOT_REQUIRED"
    model: str = ""
    trigger_reason: str = ""
    reviewed_fields: list[str] = field(default_factory=list)
    selected_pages: list[int] = field(default_factory=list)
    page_requests: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[VisionFieldCandidate] = field(default_factory=list)
    accepted_fields: list[str] = field(default_factory=list)
    human_required_fields: list[str] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": "3.5",
            "attempted": self.attempted,
            "status": self.status,
            "model": self.model,
            "trigger_reason": self.trigger_reason,
            "reviewed_fields": list(self.reviewed_fields),
            "selected_pages": list(self.selected_pages),
            "page_requests": list(self.page_requests),
            "candidates": [item.to_dict() for item in self.candidates],
            "accepted_fields": list(self.accepted_fields),
            "human_required_fields": list(self.human_required_fields),
            "rejected": list(self.rejected),
            "failures": list(self.failures),
            "duration_seconds": round(self.duration_seconds, 3),
        }
