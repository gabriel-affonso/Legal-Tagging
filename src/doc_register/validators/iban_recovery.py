from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any

from ..models import ExtractionResult


IBAN_LABEL_RE = re.compile(r"\bIBAN\b", re.IGNORECASE)
NIB_LABEL_RE = re.compile(r"\bNIB\b", re.IGNORECASE)
PT_IBAN_RE = re.compile(r"PT50\d{21}")
NIB_RE = re.compile(r"(?<!\d)\d{21}(?!\d)")

OCR_BANK_TRANSLATION = str.maketrans({
    "O": "0", "o": "0", "Q": "0", "D": "0",
    "I": "1", "i": "1", "l": "1", "|": "1",
    "S": "5", "s": "5",
    "B": "8",
    "Z": "2", "z": "2",
})


@dataclass(frozen=True)
class IbanRecoveryChange:
    field: str
    old_value: str
    new_value: str
    method: str
    evidence: str


@dataclass
class IbanRecoveryReport:
    attempted: bool = False
    changes: list[IbanRecoveryChange] = field(default_factory=list)
    candidate_count: int = 0
    rejected_candidates: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "changes": [asdict(change) for change in self.changes],
            "candidate_count": self.candidate_count,
            "rejected_candidates": list(self.rejected_candidates[:10]),
            "reason": self.reason,
        }


def recover_iban_fields(
    result: ExtractionResult,
    *,
    document_text: str,
) -> tuple[ExtractionResult, IbanRecoveryReport]:
    report = IbanRecoveryReport()
    if not document_text.strip():
        _attach_report(result, report)
        return result, report

    current_iban = normalize_account(result.iban)
    current_nib = normalize_account(result.nib)
    if current_iban and is_valid_iban(current_iban) and (not current_nib or is_valid_nib(current_nib)):
        report.reason = "existing_bank_identifiers_valid"
        _attach_report(result, report)
        return result, report

    report.attempted = True
    candidates = _bank_candidates(document_text)
    report.candidate_count = len(candidates)

    valid_ibans = _unique(
        candidate.value
        for candidate in candidates
        if candidate.kind == "iban" and is_valid_iban(candidate.value)
    )
    valid_nibs = _unique(
        candidate.value
        for candidate in candidates
        if candidate.kind == "nib" and is_valid_nib(candidate.value)
    )
    report.rejected_candidates = _unique(
        candidate.value
        for candidate in candidates
        if not (
            candidate.kind == "iban" and is_valid_iban(candidate.value)
            or candidate.kind == "nib" and is_valid_nib(candidate.value)
        )
    )

    if len(valid_ibans) == 1:
        _set_field(result, report, "iban", valid_ibans[0], "iban_checksum_recovered", _evidence_for(document_text, valid_ibans[0]))
        nib = valid_ibans[0][4:]
        if not current_nib or not is_valid_nib(current_nib):
            _set_field(result, report, "nib", nib, "iban_bban_recovered", _evidence_for(document_text, valid_ibans[0]))
    elif len(valid_ibans) > 1:
        report.reason = "multiple_valid_iban_candidates"

    if not normalize_account(result.iban) and len(valid_nibs) == 1:
        _set_field(result, report, "nib", valid_nibs[0], "nib_checksum_recovered", _evidence_for(document_text, valid_nibs[0]))
        _set_field(result, report, "iban", f"PT50{valid_nibs[0]}", "valid_nib_to_pt_iban", _evidence_for(document_text, valid_nibs[0]))
    elif len(valid_nibs) > 1 and not report.reason:
        report.reason = "multiple_valid_nib_candidates"

    if not report.reason:
        report.reason = "recovered" if report.changes else "no_unique_valid_candidate"
    _attach_report(result, report)
    return result, report


@dataclass(frozen=True)
class _Candidate:
    kind: str
    value: str


def _bank_candidates(text: str) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for match in IBAN_LABEL_RE.finditer(text):
        window = text[match.start(): min(len(text), match.end() + 120)]
        candidates.extend(_extract_candidates_from_window(window, prefer_iban=True))
    for match in NIB_LABEL_RE.finditer(text):
        window = text[match.start(): min(len(text), match.end() + 100)]
        candidates.extend(_extract_candidates_from_window(window, prefer_iban=False))

    # Full-document fallback accepts only explicit PT50 candidates, still checksum gated.
    normalized = _bank_normalized(text)
    candidates.extend(_Candidate("iban", match.group(0)) for match in PT_IBAN_RE.finditer(normalized))
    return _unique_candidates(candidates)


def _extract_candidates_from_window(window: str, *, prefer_iban: bool) -> list[_Candidate]:
    normalized = _bank_normalized(window)
    candidates = [_Candidate("iban", match.group(0)) for match in PT_IBAN_RE.finditer(normalized)]
    for match in NIB_RE.finditer(normalized):
        kind = "iban" if prefer_iban and f"PT50{match.group(0)}" in normalized else "nib"
        candidates.append(_Candidate(kind, match.group(0)))

    # Some OCR outputs split or corrupt the PT50 prefix. Keep only enough
    # repair to reconstruct candidates that can be verified by checksum.
    compact = re.sub(r"[^A-Z0-9]", "", window).translate(OCR_BANK_TRANSLATION).upper()
    for match in PT_IBAN_RE.finditer(compact):
        candidates.append(_Candidate("iban", match.group(0)))
    return _unique_candidates(candidates)


def _bank_normalized(value: str) -> str:
    compact = re.sub(r"[^A-Za-z0-9|]", "", value)
    return compact.translate(OCR_BANK_TRANSLATION).upper()


def normalize_account(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def is_valid_iban(value: str) -> bool:
    value = normalize_account(value)
    if not re.fullmatch(r"PT50\d{21}", value):
        return False
    rearranged = value[4:] + "2529" + value[2:4]
    remainder = 0
    for char in rearranged:
        remainder = (remainder * 10 + int(char)) % 97
    return remainder == 1


def is_valid_nib(value: str) -> bool:
    value = normalize_account(value)
    if not re.fullmatch(r"\d{21}", value):
        return False
    return is_valid_iban(f"PT50{value}")


def _set_field(
    result: ExtractionResult,
    report: IbanRecoveryReport,
    field_name: str,
    value: str,
    method: str,
    evidence: str,
) -> None:
    current = str(getattr(result, field_name, "") or "").strip()
    if normalize_account(current) == normalize_account(value):
        return
    setattr(result, field_name, value)
    report.changes.append(
        IbanRecoveryChange(
            field=field_name,
            old_value=current,
            new_value=value,
            method=method,
            evidence=evidence[:220],
        )
    )


def _evidence_for(text: str, value: str) -> str:
    normalized_value = normalize_account(value)
    compact_text = re.sub(r"\s+", " ", text)
    for label in ("IBAN", "NIB"):
        match = re.search(rf"\b{label}\b.{{0,140}}", compact_text, flags=re.IGNORECASE)
        if match and normalized_value[-10:] in normalize_account(match.group(0)):
            return match.group(0)
    return normalized_value


def _attach_report(result: ExtractionResult, report: IbanRecoveryReport) -> None:
    raw = result.raw_json if isinstance(result.raw_json, dict) else {}
    result.raw_json = dict(raw)
    result.raw_json["iban_recovery"] = report.to_dict()


def _unique(values):
    output = []
    for value in values:
        if value and value not in output:
            output.append(value)
    return output


def _unique_candidates(values: list[_Candidate]) -> list[_Candidate]:
    output: list[_Candidate] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        key = (value.kind, value.value)
        if value.value and key not in seen:
            seen.add(key)
            output.append(value)
    return output


__all__ = [
    "IbanRecoveryChange",
    "IbanRecoveryReport",
    "is_valid_iban",
    "is_valid_nib",
    "normalize_account",
    "recover_iban_fields",
]
