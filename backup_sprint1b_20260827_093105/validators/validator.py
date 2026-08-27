from __future__ import annotations

import re
from typing import Any, Iterable

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

# Existing pipeline messages that are explanatory notes, rather than stable
# machine-readable issue codes. They remain in review_reason, but do not alter
# validation_issues, score or validation_status by themselves.
ISSUE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

# ============================================================
# PUBLIC VALIDATOR
# ============================================================


def validate_result(
    result: ExtractionResult,
    signals: Any | None = None,
    document_text: str | None = None,
) -> ExtractionResult:
    """Validate and enrich one ``ExtractionResult``.

    This is the public entry point used by ``processor.py``::

        result = validate_result(result, signals, document_text)

    Sprint 1A performs deterministic validation only. The ``signals`` and
    ``document_text`` arguments are accepted to preserve compatibility with
    the existing processor and to support Sprint 1B recovery logic later.

    The function mutates and returns ``result`` by populating:

    - ``quality_score``
    - ``quality_band``
    - ``validation_status``
    - ``validation_issues``
    - ``review_priority``
    - ``needs_review``
    - ``review_reason``
    """

    # Reserved for Sprint 1B recovery and evidence gathering.
    _ = signals
    _ = document_text

    issues = _unique_sorted(run_all_validations(result))
    quality_score, quality_band = calculate_score(issues)
    review_priority = calculate_review_priority(issues)

    existing_reasons = parse_existing_review_reasons(result.review_reason)
    machine_reasons, narrative_reasons = _partition_existing_reasons(
        existing_reasons
    )

    # Keep legacy machine-readable reasons, but do not duplicate codes.
    all_machine_reasons = _unique_sorted([*machine_reasons, *issues])

    prior_review_required = _is_truthy_review_flag(result.needs_review)
    has_validation_issues = bool(all_machine_reasons)
    requires_review = (
        prior_review_required
        or has_validation_issues
        or bool(narrative_reasons)
    )

    result.quality_score = str(quality_score)
    result.quality_band = quality_band
    result.validation_issues = "; ".join(issues)
    result.review_priority = review_priority

    if result.processing_status.strip().lower() == "error":
        result.validation_status = "ERROR"
        result.needs_review = "yes"
        result.review_priority = "HIGH"
    elif requires_review:
        result.validation_status = "NEEDS_REVIEW"
        result.needs_review = "yes"
    else:
        result.validation_status = "AUTO_APPROVED"
        result.needs_review = "no"

    combined_reasons = [*all_machine_reasons, *narrative_reasons]
    result.review_reason = "; ".join(_unique_preserve_order(combined_reasons))

    return result

# ============================================================
# REVIEW PRIORITY
# ============================================================


def calculate_review_priority(issues: Iterable[str]) -> str:
    """Return ``HIGH``, ``MEDIUM``, ``LOW`` or ``NONE`` for issue codes."""

    normalized_issues = _unique_sorted(issues)
    if not normalized_issues:
        return "NONE"

    for issue in normalized_issues:
        if issue in HIGH_PRIORITY_ISSUES:
            return "HIGH"
        if issue.startswith("invalid_iban_"):
            return "HIGH"
        if issue.startswith("generic_lessor"):
            return "HIGH"
        if issue.startswith("generic_lessee"):
            return "HIGH"

    for issue in normalized_issues:
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
) -> list[str]:
    """Parse legacy comma/semicolon-separated review reasons safely."""

    if not review_reason:
        return []

    normalized = str(review_reason).replace(",", ";")
    return _unique_preserve_order(
        part.strip()
        for part in normalized.split(";")
        if part.strip()
    )


def _partition_existing_reasons(
    reasons: Iterable[str],
) -> tuple[list[str], list[str]]:
    """Split stable issue codes from human-readable legacy explanations."""

    machine_reasons: list[str] = []
    narrative_reasons: list[str] = []

    for reason in reasons:
        if ISSUE_CODE_PATTERN.fullmatch(reason):
            machine_reasons.append(reason)
        else:
            narrative_reasons.append(reason)

    return (
        _unique_preserve_order(machine_reasons),
        _unique_preserve_order(narrative_reasons),
    )

# ============================================================
# HELPERS
# ============================================================


def _is_truthy_review_flag(value: str | None) -> bool:
    return str(value or "").strip().lower() in {
        "yes",
        "true",
        "1",
        "sim",
    }


def _unique_sorted(values: Iterable[str]) -> list[str]:
    return sorted(
        {
            str(value).strip()
            for value in values
            if str(value).strip()
        }
    )


def _unique_preserve_order(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()

    for value in values:
        cleaned = str(value).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        output.append(cleaned)

    return output


__all__ = [
    "validate_result",
    "calculate_review_priority",
    "parse_existing_review_reasons",
    "HIGH_PRIORITY_ISSUES",
    "MEDIUM_PRIORITY_ISSUES",
]
