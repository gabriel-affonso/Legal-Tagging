from __future__ import annotations

import re
from typing import Any, Iterable

from ..models import ExtractionResult
from .critical_recovery import recover_critical_fields
from .quality_score import calculate_score
from .validation_rules import run_all_validations

HIGH_PRIORITY_ISSUES = {
    "missing_lessor", "missing_lessee", "missing_iban",
    "missing_bank_account_holder", "invalid_iban_format",
    "invalid_iban_checksum", "generic_lessor", "generic_lessee",
}
MEDIUM_PRIORITY_ISSUES = {
    "missing_signed_date", "missing_monthly_rent", "missing_property_article",
    "missing_property_section", "missing_owner_name", "invalid_property_section",
    "invalid_owner_tax_id_format", "invalid_owner_tax_id_checksum",
    "invalid_or_multiple_owner_tax_id", "invalid_monthly_rent_format",
    "invalid_monthly_rent_value", "low_confidence",
}
ISSUE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def validate_result(
    result: ExtractionResult,
    signals: Any | None = None,
    document_text: str | None = None,
    *,
    file_name: str = "",
    ollama_url: str = "http://localhost:11434",
    ollama_model: str = "qwen3:8b",
    recovery_ai_enabled: bool = False,
    recovery_timeout_seconds: int = 180,
) -> ExtractionResult:
    """Recover critical fields, validate, score and enrich one result."""
    _ = signals
    if document_text:
        result, _report = recover_critical_fields(
            result,
            file_name=file_name,
            document_text=document_text,
            ollama_url=ollama_url,
            ollama_model=ollama_model,
            ai_enabled=recovery_ai_enabled,
            timeout_seconds=recovery_timeout_seconds,
        )

    issues = _unique_sorted(run_all_validations(result))
    quality_score, quality_band = calculate_score(issues)
    review_priority = calculate_review_priority(issues)
    existing = parse_existing_review_reasons(result.review_reason)
    machine, narrative = _partition_existing_reasons(existing)
    all_machine = _unique_sorted([*machine, *issues])
    requires_review = _truthy(result.needs_review) or bool(all_machine) or bool(narrative)

    result.quality_score = str(quality_score)
    result.quality_band = quality_band
    result.validation_issues = "; ".join(issues)
    result.review_priority = review_priority

    if (result.processing_status or "").strip().lower() == "error":
        result.validation_status = "ERROR"
        result.needs_review = "yes"
        result.review_priority = "HIGH"
    elif requires_review:
        result.validation_status = "NEEDS_REVIEW"
        result.needs_review = "yes"
    else:
        result.validation_status = "AUTO_APPROVED"
        result.needs_review = "no"

    result.review_reason = "; ".join(_unique_order([*all_machine, *narrative]))
    return result


def calculate_review_priority(issues: Iterable[str]) -> str:
    values = _unique_sorted(issues)
    if not values:
        return "NONE"
    if any(i in HIGH_PRIORITY_ISSUES or i.startswith(("invalid_iban_", "generic_lessor", "generic_lessee")) for i in values):
        return "HIGH"
    if any(i in MEDIUM_PRIORITY_ISSUES or i.startswith(("future_date_", "invalid_owner_tax_id_", "invalid_monthly_rent_")) for i in values):
        return "MEDIUM"
    return "LOW"


def parse_existing_review_reasons(value: str | None) -> list[str]:
    if not value:
        return []
    return _unique_order(part.strip() for part in str(value).replace(",", ";").split(";") if part.strip())


def _partition_existing_reasons(reasons: Iterable[str]) -> tuple[list[str], list[str]]:
    machine, narrative = [], []
    for reason in reasons:
        (machine if ISSUE_CODE_PATTERN.fullmatch(reason) else narrative).append(reason)
    return _unique_order(machine), _unique_order(narrative)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"yes", "true", "1", "sim"}


def _unique_sorted(values: Iterable[str]) -> list[str]:
    return sorted({str(v).strip() for v in values if str(v).strip()})


def _unique_order(values: Iterable[str]) -> list[str]:
    output, seen = [], set()
    for value in values:
        clean = str(value).strip()
        if clean and clean not in seen:
            seen.add(clean)
            output.append(clean)
    return output


__all__ = ["validate_result", "calculate_review_priority", "parse_existing_review_reasons"]
