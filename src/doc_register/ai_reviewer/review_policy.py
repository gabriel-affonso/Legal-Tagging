from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import ExtractionResult
from ..validators.validation_rules import requires_monthly_rent

ELIGIBLE_ISSUES_TO_FIELDS = {
    "missing_lessor": "lessor",
    "generic_lessor": "lessor",
    "missing_lessee": "lessee",
    "generic_lessee": "lessee",
    "missing_signed_date": "signed_date",
    "future_date_signed_date": "signed_date",
    "missing_property_article": "property_article",
    "missing_property_section": "property_section",
    "invalid_property_section": "property_section",
    "missing_monthly_rent": "monthly_rent",
    "invalid_monthly_rent_format": "monthly_rent",
}

@dataclass(frozen=True)
class AIReviewPlan:
    eligible: bool
    status: str
    fields: list[str]
    issues: list[str]
    reason: str


def should_review(result: ExtractionResult, *, document_text: str) -> AIReviewPlan:
    status = (result.validation_status or "").strip().upper()
    if status == "AUTO_APPROVED":
        return AIReviewPlan(False, "NOT_REQUIRED", [], [], "already_auto_approved")

    technical_reason = _technical_reason(result, document_text)
    if technical_reason:
        return AIReviewPlan(False, "NOT_ELIGIBLE", [], [], technical_reason)

    issues = _current_issues(result)
    fields = eligible_review_fields(result, issues)
    if not fields:
        return AIReviewPlan(False, "NOT_ELIGIBLE", [], issues, "no_eligible_issues")

    return AIReviewPlan(True, "PENDING", fields, issues, "eligible_issues")


def eligible_review_fields(result: ExtractionResult, issues: list[str] | None = None) -> list[str]:
    selected: list[str] = []
    for issue in issues if issues is not None else _current_issues(result):
        field_name = ELIGIBLE_ISSUES_TO_FIELDS.get(issue)
        if not field_name:
            continue
        if field_name == "monthly_rent" and not requires_monthly_rent(result):
            continue
        if field_name not in selected:
            selected.append(field_name)
    return selected


def _technical_reason(result: ExtractionResult, document_text: str) -> str:
    if (result.processing_status or "").strip().lower() == "error":
        return "TECHNICAL_ERROR"
    if not document_text.strip():
        return "NEEDS_OCR"

    native_chars = _as_int(result.native_text_chars)
    ocr_chars = _as_int(result.ocr_text_chars)
    if native_chars == 0 and ocr_chars == 0 and not document_text.strip():
        return "NEEDS_OCR"
    return ""


def _current_issues(result: ExtractionResult) -> list[str]:
    values: list[str] = []
    for raw in (result.validation_issues, result.review_reason):
        if not raw:
            continue
        for part in str(raw).replace(",", ";").split(";"):
            clean = part.strip()
            if clean and clean not in values:
                values.append(clean)
    return values


def _as_int(value: Any) -> int:
    try:
        return int(str(value or "0").strip())
    except ValueError:
        return 0
