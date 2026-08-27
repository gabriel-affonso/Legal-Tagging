from __future__ import annotations
from collections.abc import Iterable

PENALTIES: dict[str, int] = {
    "missing_lessor": 25, "missing_lessee": 25, "missing_signed_date": 20,
    "missing_monthly_rent": 15, "missing_property_article": 15,
    "missing_property_section": 10, "missing_owner_name": 15,
    "missing_iban": 25, "missing_bank_account_holder": 20,
    "missing_payment_date": 15, "missing_payer": 15,
    "missing_payee": 15, "missing_payment_amount": 20,
    "future_date": 20, "generic_name": 20, "low_confidence": 10,
    "invalid_confidence": 5, "invalid_property_section": 10,
    "invalid_property_article": 15, "invalid_iban": 25,
    "invalid_nif": 20, "invalid_or_multiple_owner_tax_id": 15,
    "invalid_monthly_rent": 15, "suspicious_lessee": 15,
    "invalid_name": 20, "ocr_corrupted_party_name": 25,
    "ocr_quality_low": 20,
    "cross_field_conflict": 15,
    "unknown_validation_issue": 5,
}
QUALITY_BANDS: tuple[tuple[int, str], ...] = (
    (90, "EXCELLENT"), (80, "GOOD"), (60, "ATTENTION"),
    (40, "REVIEW_REQUIRED"), (0, "CRITICAL"),
)

def calculate_score(issues: Iterable[str]) -> tuple[int, str]:
    score = 100
    for issue in {str(i).strip() for i in issues if str(i).strip()}:
        score -= get_penalty(issue)
    score = max(0, min(100, score))
    return score, determine_quality_band(score)

def determine_quality_band(score: int) -> str:
    bounded = max(0, min(100, int(score)))
    for minimum, label in QUALITY_BANDS:
        if bounded >= minimum:
            return label
    return "CRITICAL"

def score_summary(score: int) -> str:
    return determine_quality_band(score)

def apply_penalty(current_score: int, issue: str) -> int:
    return max(0, min(100, int(current_score)) - get_penalty(issue))

def get_penalty(issue: str) -> int:
    normalized = str(issue or "").strip().lower()
    if not normalized:
        return 0
    if normalized in PENALTIES:
        return PENALTIES[normalized]
    mappings = (
        ("future_date_", "future_date"), ("generic_", "generic_name"),
        ("invalid_iban_", "invalid_iban"),
        ("invalid_owner_tax_id_", "invalid_nif"),
        ("invalid_monthly_rent_", "invalid_monthly_rent"),
        ("invalid_lessor_", "invalid_name"),
        ("invalid_lessee_", "invalid_name"),
        ("invalid_owner_name_", "invalid_name"),
        ("invalid_bank_account_holder_", "invalid_name"),
        ("invalid_payer_", "invalid_name"),
        ("invalid_payee_", "invalid_name"),
        ("monthly_rent_conflicts_", "cross_field_conflict"),
        ("property_article_conflicts_", "cross_field_conflict"),
        ("property_section_conflicts_", "cross_field_conflict"),
        ("suspicious_monthly_rent_for_contract_subtype", "cross_field_conflict"),
    )
    for prefix, family in mappings:
        if normalized.startswith(prefix):
            return PENALTIES[family]
    return PENALTIES["unknown_validation_issue"]

__all__ = ["PENALTIES", "QUALITY_BANDS", "calculate_score", "determine_quality_band", "score_summary", "apply_penalty", "get_penalty"]
