from __future__ import annotations

from collections.abc import Iterable

# ============================================================
# PENALTIES
# ============================================================

PENALTIES: dict[str, int] = {
    # Lease contract critical fields
    "missing_lessor": 25,
    "missing_lessee": 25,
    "missing_signed_date": 20,
    "missing_monthly_rent": 15,
    "missing_property_article": 15,

    # Property document critical fields
    "missing_property_section": 10,
    "missing_owner_name": 15,

    # Bank document critical fields
    "missing_iban": 25,
    "missing_bank_account_holder": 20,

    # Payment proof critical fields
    "missing_payment_date": 15,
    "missing_payer": 15,
    "missing_payee": 15,
    "missing_payment_amount": 20,

    # General validation findings
    "future_date": 20,
    "generic_name": 20,
    "low_confidence": 10,
    "invalid_confidence": 5,

    # Property validation
    "invalid_property_section": 10,
    "invalid_property_article": 15,

    # Bank validation
    "invalid_iban": 25,

    # Portuguese tax ID validation
    "invalid_nif": 20,
    "invalid_or_multiple_owner_tax_id": 15,

    # Monthly rent validation
    "invalid_monthly_rent": 15,

    # Lessee validation
    "suspicious_lessee": 15,

    # Fallback for unknown findings
    "unknown_validation_issue": 5,
}

# ============================================================
# QUALITY BANDS
# ============================================================

QUALITY_BANDS: tuple[tuple[int, str], ...] = (
    (90, "EXCELLENT"),
    (80, "GOOD"),
    (60, "ATTENTION"),
    (40, "REVIEW_REQUIRED"),
    (0, "CRITICAL"),
)

# ============================================================
# PUBLIC FUNCTIONS
# ============================================================


def calculate_score(issues: Iterable[str]) -> tuple[int, str]:
    """Calculate a quality score and band from validation issue codes.

    Duplicate issue codes are penalized only once. Specific issue codes emitted
    by ``validation_rules.py`` are mapped to their corresponding penalty family.

    Examples:
        ``future_date_signed_date`` -> ``future_date``
        ``generic_lessor`` -> ``generic_name``
        ``invalid_iban_checksum`` -> ``invalid_iban``
    """

    score = 100
    normalized_issues = {
        str(issue).strip()
        for issue in issues
        if str(issue).strip()
    }

    for issue in normalized_issues:
        score -= get_penalty(issue)

    score = max(0, min(100, score))
    return score, determine_quality_band(score)


def determine_quality_band(score: int) -> str:
    """Return the configured quality band for a score from 0 to 100."""

    bounded_score = max(0, min(100, int(score)))

    for minimum_score, label in QUALITY_BANDS:
        if bounded_score >= minimum_score:
            return label

    return "CRITICAL"


def score_summary(score: int) -> str:
    """Return the quality-band label for dashboards and logs."""

    return determine_quality_band(score)


def apply_penalty(current_score: int, issue: str) -> int:
    """Apply one issue penalty to an existing score."""

    return max(0, min(100, int(current_score)) - get_penalty(issue))


def get_penalty(issue: str) -> int:
    """Return the applicable penalty for an exact or prefixed issue code."""

    normalized = str(issue or "").strip().lower()

    if not normalized:
        return 0

    if normalized in PENALTIES:
        return PENALTIES[normalized]

    if normalized.startswith("future_date_"):
        return PENALTIES["future_date"]

    if normalized.startswith("generic_"):
        return PENALTIES["generic_name"]

    if normalized.startswith("invalid_iban_"):
        return PENALTIES["invalid_iban"]

    if normalized.startswith("invalid_owner_tax_id_"):
        return PENALTIES["invalid_nif"]

    if normalized.startswith("invalid_monthly_rent_"):
        return PENALTIES["invalid_monthly_rent"]

    return PENALTIES["unknown_validation_issue"]


__all__ = [
    "PENALTIES",
    "QUALITY_BANDS",
    "calculate_score",
    "determine_quality_band",
    "score_summary",
    "apply_penalty",
    "get_penalty",
]


if __name__ == "__main__":
    example_issues = [
        "generic_lessor",
        "future_date_signed_date",
        "missing_monthly_rent",
    ]

    example_score, example_band = calculate_score(example_issues)
    print("Issues:", example_issues)
    print("Score:", example_score)
    print("Band:", example_band)
