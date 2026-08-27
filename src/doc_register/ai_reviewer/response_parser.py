from __future__ import annotations

import json
import re
from typing import Any

from .models import AIFieldProposal

ALLOWED_PROPOSAL_KEYS = {"field_name", "field", "proposed_value", "value", "evidence", "confidence"}
MAX_VALUE_CHARS = 300
MAX_EVIDENCE_CHARS = 1000


def parse_review_response(raw: dict[str, Any] | str, requested_fields: list[str]) -> list[AIFieldProposal]:
    payload = _payload(raw)
    unknown = set(payload) - {"proposals", "fields"}
    if unknown:
        raise ValueError(f"AI review response contains unexpected keys: {sorted(unknown)}")

    proposals_raw = payload.get("proposals")
    if proposals_raw is None and isinstance(payload.get("fields"), dict):
        proposals_raw = [
            {
                "field_name": field_name,
                "proposed_value": value.get("proposed_value", value.get("value", "")) if isinstance(value, dict) else value,
                "evidence": value.get("evidence", "") if isinstance(value, dict) else "",
                "confidence": value.get("confidence", 0.0) if isinstance(value, dict) else 0.0,
            }
            for field_name, value in payload["fields"].items()
        ]

    if not isinstance(proposals_raw, list):
        raise ValueError("AI review response must contain a proposals array")

    proposals: list[AIFieldProposal] = []
    seen: set[str] = set()
    requested = set(requested_fields)
    for item in proposals_raw:
        if not isinstance(item, dict):
            raise ValueError("Every AI review proposal must be an object")
        unexpected = set(item) - ALLOWED_PROPOSAL_KEYS
        if unexpected:
            raise ValueError(f"AI review proposal contains unexpected keys: {sorted(unexpected)}")

        field_name = _clean(item.get("field_name") or item.get("field"))
        if field_name not in requested:
            raise ValueError(f"AI review proposed an unrequested field: {field_name}")
        if field_name in seen:
            continue
        seen.add(field_name)

        proposed_value = _clean(item.get("proposed_value") or item.get("value"))
        evidence = _clean(item.get("evidence"))
        if len(proposed_value) > MAX_VALUE_CHARS:
            raise ValueError(f"AI review value for {field_name} is too long")
        if len(evidence) > MAX_EVIDENCE_CHARS:
            evidence = evidence[:MAX_EVIDENCE_CHARS].rstrip()

        proposals.append(
            AIFieldProposal(
                field_name=field_name,
                proposed_value=proposed_value,
                evidence=evidence,
                confidence=_confidence(item.get("confidence")),
            )
        )
    return proposals


def _payload(raw: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise ValueError("AI review response must be JSON text or an object")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            raise ValueError("AI review response did not contain JSON") from None
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("AI review JSON must be an object")
    return parsed


def _confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("AI review fields must be strings")
    return " ".join(value.strip().split())
