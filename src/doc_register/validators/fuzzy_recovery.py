from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
import unicodedata
from typing import Any

from ..models import ExtractionResult
from .name_quality import validate_name_field


KNOWN_PLACES = {
    "PENAS ROIAS": "Penas Roias",
    "PENAS ROIAS MOGADOURO": "Penas Roias, Mogadouro",
    "MOGADOURO": "Mogadouro",
    "BRAGANCA": "Braganca",
}

PLACE_FIELDS = (
    "property_parish",
    "property_municipality",
    "property_district",
    "property_location",
)
NAME_FIELDS = ("lessor", "lessee", "owner_name")
NAME_LABEL_RE = re.compile(
    r"\b(?:senhorios?|arrendat[aá]ri[oa]s?|propriet[aá]ri[oa]s?|"
    r"sujeito\s+passivo|titular|outorgantes?|cedente|cession[aá]ri[oa])"
    r"\s*[:,-]?\s*(.{5,180})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FuzzyRecoveryChange:
    field: str
    old_value: str
    new_value: str
    method: str
    evidence: str


@dataclass
class FuzzyRecoveryReport:
    attempted_fields: list[str] = field(default_factory=list)
    changes: list[FuzzyRecoveryChange] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted_fields": list(self.attempted_fields),
            "changes": [asdict(change) for change in self.changes],
        }


def recover_fuzzy_fields(
    result: ExtractionResult,
    *,
    file_name: str = "",
    document_text: str,
) -> tuple[ExtractionResult, FuzzyRecoveryReport]:
    """Apply conservative OCR repairs using known local values and local evidence."""
    report = FuzzyRecoveryReport()
    if not document_text.strip():
        _attach_report(result, report)
        return result, report

    _recover_known_places(result, document_text, report)
    _recover_name_from_nearby_candidate(result, file_name, document_text, report)
    _attach_report(result, report)
    return result, report


def _recover_known_places(
    result: ExtractionResult,
    document_text: str,
    report: FuzzyRecoveryReport,
) -> None:
    normalized_document = _normalize(document_text)
    for field_name in PLACE_FIELDS:
        value = str(getattr(result, field_name, "") or "").strip()
        if not value:
            continue
        report.attempted_fields.append(field_name)
        normalized_value = _normalize(value)
        if not normalized_value or normalized_value in KNOWN_PLACES:
            continue
        for known_normalized, canonical in KNOWN_PLACES.items():
            if _close_enough(normalized_value, known_normalized) and (
                known_normalized in normalized_document
                or _close_enough(_best_document_place_token(normalized_document, known_normalized), known_normalized)
            ):
                _set_field(result, report, field_name, canonical, "known_place_fuzzy_ocr", value)
                break


def _recover_name_from_nearby_candidate(
    result: ExtractionResult,
    file_name: str,
    document_text: str,
    report: FuzzyRecoveryReport,
) -> None:
    source_candidates = _name_candidates(file_name, document_text)
    if not source_candidates:
        return
    for field_name in NAME_FIELDS:
        value = str(getattr(result, field_name, "") or "").strip()
        if not value:
            continue
        report.attempted_fields.append(field_name)
        value_has_issues = bool(validate_name_field(field_name, value))
        normalized_value = _normalize(value)
        if not normalized_value:
            continue
        for candidate, evidence in source_candidates:
            normalized_candidate = _normalize(candidate)
            if normalized_candidate == normalized_value:
                break
            if validate_name_field(field_name, candidate):
                continue
            candidate_from_file = normalized_candidate in _normalize(file_name)
            if _close_enough(normalized_value, normalized_candidate) and (
                value_has_issues or candidate_from_file
            ):
                _set_field(result, report, field_name, candidate, "known_name_fuzzy_ocr", evidence)
                break


def _name_candidates(file_name: str, document_text: str) -> list[tuple[str, str]]:
    output: list[tuple[str, str]] = []
    file_text = re.sub(r"[_-]+", " ", file_name.rsplit(".", 1)[0])
    for raw in re.split(r"\s+(?:e|&)\s+|[;|]+", file_text):
        candidate = _clean_name(raw)
        if candidate:
            output.append((candidate, file_name))

    for line in document_text.splitlines():
        cleaned_line = " ".join(line.split())
        match = NAME_LABEL_RE.search(cleaned_line)
        if not match:
            continue
        candidate = _clean_name(match.group(1))
        if candidate:
            output.append((candidate, cleaned_line))
    return _dedupe_candidates(output)


def _clean_name(value: str) -> str:
    value = re.sub(r"\b(?:PR\d+|SP\d+|CA|CAV|COPC|OCR|PDF)\b", " ", value, flags=re.IGNORECASE)
    value = re.split(
        r"\b(?:NIF|NIPC|contribuinte|residente|morada|com\s+sede|natural\s+de|iban|nib)\b",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    value = re.sub(r"\s+", " ", value).strip(" ,;:-_")
    if len(value) < 5:
        return ""
    return value[:180]


def _best_document_place_token(document: str, known: str) -> str:
    words = known.split()
    size = len(words)
    if not document or not words:
        return ""
    tokens = document.split()
    best = ""
    best_distance = 999
    for index in range(0, max(0, len(tokens) - size + 1)):
        candidate = " ".join(tokens[index:index + size])
        distance = _levenshtein(candidate, known)
        if distance < best_distance:
            best = candidate
            best_distance = distance
    return best


def _set_field(
    result: ExtractionResult,
    report: FuzzyRecoveryReport,
    field_name: str,
    value: str,
    method: str,
    evidence: str,
) -> None:
    old_value = str(getattr(result, field_name, "") or "").strip()
    if old_value == value:
        return
    setattr(result, field_name, value)
    report.changes.append(
        FuzzyRecoveryChange(
            field=field_name,
            old_value=old_value,
            new_value=value,
            method=method,
            evidence=evidence[:220],
        )
    )


def _close_enough(left: str, right: str) -> bool:
    if not left or not right:
        return False
    distance = _levenshtein(left, right)
    limit = max(1, min(3, int(max(len(left), len(right)) * 0.18)))
    return distance <= limit


def _levenshtein(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        for j, right_char in enumerate(right, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value))
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    plain = re.sub(r"[^A-Za-z0-9 ]", " ", plain)
    return " ".join(plain.upper().split())


def _dedupe_candidates(candidates: list[tuple[str, str]]) -> list[tuple[str, str]]:
    output: list[tuple[str, str]] = []
    seen: set[str] = set()
    for candidate, evidence in candidates:
        normalized = _normalize(candidate)
        if normalized not in seen:
            seen.add(normalized)
            output.append((candidate, evidence))
    return output


def _attach_report(result: ExtractionResult, report: FuzzyRecoveryReport) -> None:
    raw = result.raw_json if isinstance(result.raw_json, dict) else {}
    result.raw_json = dict(raw)
    result.raw_json["fuzzy_recovery"] = report.to_dict()


__all__ = [
    "FuzzyRecoveryChange",
    "FuzzyRecoveryReport",
    "recover_fuzzy_fields",
]
