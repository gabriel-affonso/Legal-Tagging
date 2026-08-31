"""Cross-document, per-field evidence consensus for Step 3.3."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re
import unicodedata
from typing import Iterable

from .source_ranking import RankedValue, select_ranked


@dataclass(frozen=True)
class FieldEvidence:
    value: str
    confidence: float
    source: str
    evidence_count: int

    def to_dict(self) -> dict[str, object]:
        return {"value": self.value, "confidence": round(self.confidence, 3), "source": self.source.lower(), "evidence_count": self.evidence_count}


def resolve_consensus(field: str, candidates: Iterable[RankedValue]) -> FieldEvidence | None:
    candidates = [item for item in candidates if item.value and item.valid]
    if not candidates:
        return None
    by_value: dict[str, list[RankedValue]] = defaultdict(list)
    for candidate in candidates:
        by_value[_normal(candidate.value)].append(candidate)
    representatives: list[tuple[float, RankedValue, int]] = []
    for matches in by_value.values():
        selected = select_ranked(field, matches)
        if not selected:
            continue
        # Source authority leads; corroboration is bounded so three weak OCR
        # repetitions can never beat an authoritative cadastral value.
        corroboration = min(15, (len(matches) - 1) * 8)
        score = min(100, selected.source_score + corroboration)
        representatives.append((score, selected, len(matches)))
    if not representatives:
        return None
    score, selected, evidence_count = max(representatives, key=lambda item: (item[0], item[1].source_score, len(item[1].value)))
    return FieldEvidence(selected.value, score / 100, selected.source, evidence_count)


def _normal(value: str) -> str:
    plain = "".join(char for char in unicodedata.normalize("NFKD", value) if not unicodedata.combining(char))
    return re.sub(r"\W+", "", plain).upper()


__all__ = ["FieldEvidence", "resolve_consensus"]
