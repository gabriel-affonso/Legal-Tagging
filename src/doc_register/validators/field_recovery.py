from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any

from ..models import ExtractionResult
from .name_quality import GENERIC_NAME_LABELS, normalize_name_text, validate_name_field


OWNER_PATTERNS = (
    re.compile(r"\b(?:propriet[aá]rio|titular|sujeito\s+passivo|owner)(?:\s+do\s+pr[eé]dio)?\s*[:#-]?\s*(.+)$", re.IGNORECASE),
)
BANK_HOLDER_PATTERNS = (
    re.compile(r"\b(?:titular(?:\s+da\s+conta)?|account\s+holder|nome)(?:\s*[:#-])\s*(.+)$", re.IGNORECASE),
)


@dataclass(frozen=True)
class FieldRecoveryChange:
    field: str
    old_value: str
    new_value: str
    method: str
    evidence: str


@dataclass
class FieldRecoveryReport:
    attempted_fields: list[str] = field(default_factory=list)
    changes: list[FieldRecoveryChange] = field(default_factory=list)
    unresolved_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted_fields": list(self.attempted_fields),
            "changes": [asdict(change) for change in self.changes],
            "unresolved_fields": list(self.unresolved_fields),
        }


def recover_labelled_fields(
    result: ExtractionResult,
    *,
    document_text: str,
) -> tuple[ExtractionResult, FieldRecoveryReport]:
    report = FieldRecoveryReport()
    if not document_text.strip():
        _attach_report(result, report)
        return result, report

    category = (result.document_category or "").strip().lower()
    if category == "property_document":
        report.attempted_fields.append("owner_name")
        _recover_name_from_patterns(
            result,
            report,
            "owner_name",
            document_text,
            OWNER_PATTERNS,
            "labelled_property_owner",
        )
    if category == "bank_details":
        report.attempted_fields.append("bank_account_holder")
        _recover_name_from_patterns(
            result,
            report,
            "bank_account_holder",
            document_text,
            BANK_HOLDER_PATTERNS,
            "labelled_bank_holder",
        )

    report.unresolved_fields = [
        field_name
        for field_name in report.attempted_fields
        if _needs_recovery(field_name, str(getattr(result, field_name, "") or ""))
    ]
    _attach_report(result, report)
    return result, report


def _recover_name_from_patterns(
    result: ExtractionResult,
    report: FieldRecoveryReport,
    field_name: str,
    text: str,
    patterns: tuple[re.Pattern[str], ...],
    method: str,
) -> None:
    current = str(getattr(result, field_name, "") or "").strip()
    if not _needs_recovery(field_name, current):
        return

    for line in text.splitlines():
        cleaned_line = " ".join(line.split())
        for pattern in patterns:
            match = pattern.search(cleaned_line)
            if not match:
                continue
            candidate = _clean_name_candidate(match.group(1))
            if candidate and not validate_name_field(field_name, candidate):
                _set_field(result, report, field_name, candidate, method, cleaned_line)
                return


def _needs_recovery(field_name: str, value: str) -> bool:
    if not value.strip():
        return True
    normalized = normalize_name_text(value)
    return normalized in GENERIC_NAME_LABELS or bool(validate_name_field(field_name, value))


def _clean_name_candidate(value: str) -> str:
    value = re.split(
        r"\b(?:NIF|NIPC|contribuinte|morada|residente|com\s+sede|iban|nib|conta)\b",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    value = re.sub(r"\s+", " ", value).strip(" ,;:-")
    if not value:
        return ""
    return value[:180]


def _set_field(
    result: ExtractionResult,
    report: FieldRecoveryReport,
    field_name: str,
    value: str,
    method: str,
    evidence: str,
) -> None:
    current = str(getattr(result, field_name, "") or "").strip()
    if current == value:
        return
    setattr(result, field_name, value)
    report.changes.append(
        FieldRecoveryChange(
            field=field_name,
            old_value=current,
            new_value=value,
            method=method,
            evidence=evidence[:220],
        )
    )


def _attach_report(result: ExtractionResult, report: FieldRecoveryReport) -> None:
    raw = result.raw_json if isinstance(result.raw_json, dict) else {}
    result.raw_json = dict(raw)
    result.raw_json["field_recovery"] = report.to_dict()


__all__ = [
    "FieldRecoveryChange",
    "FieldRecoveryReport",
    "recover_labelled_fields",
]
