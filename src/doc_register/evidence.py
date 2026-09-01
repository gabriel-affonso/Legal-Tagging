"""Step 3.6 evidence authority and conflict-preservation primitives.

The resolver intentionally retains every candidate.  A published value is a
view of this graph, never an instruction to discard conflicting documentary
facts.  This module is dependency-free so the same policy can be used by the
text, OCR and Vision paths.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


SOURCE_AUTHORITIES: dict[str, int] = {
    "CONTRACT_EXPLICIT": 100,
    "CADERNETA": 95,
    "CRP": 95,
    "BANK_DOCUMENT": 90,
    "IDENTIFICATION_DOCUMENT": 90,
    "VISION_VERIFIED": 85,
    "RULE_RECOVERY": 70,
    "LLM_EXTRACTION": 50,
    "OCR": 65,
}


@dataclass(frozen=True)
class Evidence:
    field: str
    value: str
    source: str
    page: int | None
    confidence: float
    authority_score: int
    candidate_id: str = ""
    zone: str = ""
    role: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["confidence"] = round(float(self.confidence), 3)
        return payload


def authority_for_candidate(candidate: Any) -> tuple[str, int]:
    """Classify old and new candidates under the 3.6 source hierarchy."""
    source_type = str(getattr(candidate, "source_type", "") or "").lower()
    zone = str(getattr(candidate, "source_zone", "") or "").lower()
    pattern = str(getattr(candidate, "matched_pattern", "") or "").lower()
    if source_type.startswith("caderneta") or zone == "cadastral_record":
        return "CADERNETA", SOURCE_AUTHORITIES["CADERNETA"]
    if "land_registry" in source_type or zone == "land_registry_certificate":
        return "CRP", SOURCE_AUTHORITIES["CRP"]
    if source_type == "vision_page" or zone.startswith("vision_"):
        return "VISION_VERIFIED", SOURCE_AUTHORITIES["VISION_VERIFIED"]
    if source_type == "legacy" or "prior_pipeline_output" in pattern:
        return "LLM_EXTRACTION", SOURCE_AUTHORITIES["LLM_EXTRACTION"]
    if zone in {"bank_details", "bank_statement"}:
        return "BANK_DOCUMENT", SOURCE_AUTHORITIES["BANK_DOCUMENT"]
    if zone in {"landlord_identification_annex", "identification_document", "passport", "citizen_card"}:
        return "IDENTIFICATION_DOCUMENT", SOURCE_AUTHORITIES["IDENTIFICATION_DOCUMENT"]
    if source_type in {"contract", "cadastral_annex"} or zone in {
        "party_identification", "signature_page", "signature_recognition",
        "property_recital", "object_clause", "rent_clause", "term_clause",
    }:
        return "CONTRACT_EXPLICIT", SOURCE_AUTHORITIES["CONTRACT_EXPLICIT"]
    return "RULE_RECOVERY", SOURCE_AUTHORITIES["RULE_RECOVERY"]


def evidence_from_candidate(candidate: Any) -> Evidence:
    source, authority = authority_for_candidate(candidate)
    return Evidence(
        field=str(getattr(candidate, "field", "") or ""),
        value=str(getattr(candidate, "normalized_value", "") or ""),
        source=source,
        page=getattr(candidate, "page", None),
        confidence=float(getattr(candidate, "final_score", 0.0) or 0.0),
        authority_score=authority,
        candidate_id=str(getattr(candidate, "candidate_id", "") or ""),
        zone=str(getattr(candidate, "source_zone", "") or ""),
        role=str(getattr(candidate, "contract_role", "") or ""),
    )


def evidence_graph(candidates: Iterable[Any]) -> dict[str, list[dict[str, Any]]]:
    graph: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        if str(getattr(candidate, "validation_status", "valid")) != "valid":
            continue
        item = evidence_from_candidate(candidate)
        if not item.field or not item.value:
            continue
        bucket = graph.setdefault(item.field, [])
        if not any(
            existing["value"] == item.value and existing["source"] == item.source
            and existing.get("page") == item.page and existing.get("role") == item.role
            for existing in bucket
        ):
            bucket.append(item.to_dict())
    for bucket in graph.values():
        bucket.sort(key=lambda item: (-int(item["authority_score"]), -float(item["confidence"]), item["page"] or 9999))
    return graph


def conflicts_from_graph(graph: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for field, facts in graph.items():
        values = {str(item["value"]).casefold(): str(item["value"]) for item in facts}
        if len(values) > 1:
            conflicts.append({
                "type": "evidence_value_conflict",
                "field": field,
                "values": list(values.values()),
                "sources": sorted({str(item["source"]) for item in facts}),
                "requires_review": True,
            })
    return conflicts


__all__ = ["Evidence", "SOURCE_AUTHORITIES", "authority_for_candidate", "evidence_from_candidate", "evidence_graph", "conflicts_from_graph"]
