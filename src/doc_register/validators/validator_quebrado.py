from __future__ import annotations

from typing import Any

from ..models import ExtractionResult
from .quality_score import calculate_score
from .validation_rules import run_all_validations


# ============================================================
# REVIEW PRIORITY RULES
# ============================================================

HIGH_PRIORITY_ISSUES = {
    "missing_lessor",
    "missing_lessee",
    "missing_iban",
    "missing_bank_account_holder",
    "invalid_iban_format",
    "invalid_iban_checksum",
    "generic_lessor",
    "generic_lessee",
}

MEDIUM_PRIORITY_ISSUES = {
    "missing_signed_date",
    "missing_monthly_rent",
    "missing_property_article",
    "missing_property_section",
    "missing_owner_name",
    "invalid_property_section",
    "invalid_owner_tax_id_format",
    "invalid_owner_tax_id_checksum",
    "invalid_or_multiple_owner_tax_id",
    "invalid_monthly_rent_format",
    "invalid_monthly_rent_value",
    "low_confidence",
}


# ============================================================
# PUBLIC VALIDATOR
# ============================================================

def validate_result(
    result: ExtractionResult,
    signals: Any | None = None,
    document_text: str | None = None,
) -> ExtractionResult:
    """
    Validate and enrich one ExtractionResult.

    This is the public entry point used by processor.py:

        result = validate_result(result, signals, document_text)

    The signals and document_text arguments are preserved for compatibility
    with the current processor and for the future Sprint 1B recovery layer.
    Sprint 1A does not alter extracted values using these arguments.

    The function:

    1. Runs deterministic validation rules.
    2. Calculates the data-quality score.
    3. Assigns the quality band.
    4. Assigns the review priority.
    5. Updates the existing review flags.
    6. Populates the Sprint 1A quality fields.
    """

    del signals
    del document_text

    issues = run_all_validations(result)

    quality_score, quality_band = calculate_score(issues)
    review_priority = calculate_review_priority(issues)

    existing_reasons = parse_existing_review_reasons(
        result.review_reason
    )

    all_reasons = sorted(
        set(existing_reasons).union(issues)
    )

    requires_review = bool(all_reasons)

    result.quality_score = str(quality_score)
    result.quality_band = quality_band
    result.validation_issues = "; ".join(issues)
    result.review_priority = review_priority

    if requires_review:
        result.validation_status = "NEEDS_REVIEW"
        result.needs_review = "yes"
        result.review_reason = "; ".join(all_reasons)

    else:
        result.validation_status = "AUTO_APPROVED"
        result.needs_review = "no"
        result.review_reason = ""

    return result


# ============================================================
# REVIEW PRIORITY
# ============================================================

def calculate_review_priority(
    issues: list[str],
) -> str:
    """
    Calculate review priority from validation issue codes.

    HIGH:
        Missing or invalid Tier 1 business fields.

    MEDIUM:
        Important data-quality issues that do not prevent basic
        document identification.

    LOW:
        Other validation findings.

    NONE:
        No validation findings.
    """

    if not issues:
        return "NONE"

    for issue in issues:
        if issue in HIGH_PRIORITY_ISSUES:
            return "HIGH"

        if issue.startswith("invalid_iban_"):
            return "HIGH"

        if issue.startswith("generic_lessor"):
            return "HIGH"

        if issue.startswith("generic_lessee"):
            return "HIGH"

    for issue in issues:
        if issue in MEDIUM_PRIORITY_ISSUES:
            return "MEDIUM"

        if issue.startswith("future_date_"):
            return "MEDIUM"

        if issue.startswith("invalid_owner_tax_id_"):
            return "MEDIUM"

        if issue.startswith("invalid_monthly_rent_"):
            return "MEDIUM"

    return "LOW"


# ============================================================
# EXISTING REVIEW REASONS
# ============================================================

def parse_existing_review_reasons(
    review_reason: str | None,
) -> list"""
    Convert the existing review_reason value into normalized issue codes.

    The existing pipeline may separate reasons with commas or semicolons.
    This helper preserves those findings without duplicating them.
    """

    if not review_reason:
        return []

    normalized = str(review_reason).replace(",", ";")

    reasons = []

    for part in normalized.split(";"):
        reason = part.strip()

        if reason:
            reasons.append(reason)

    return sorted(set(reasons))


__all__ = [
    "validate_result",
    "calculate_review_priority",
    "parse_existing_review_reasons",
    "HIGH_PRIORITY_ISSUES",
    "MEDIUM_PRIORITY_ISSUES",
]