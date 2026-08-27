from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any, Callable

# ============================================================
# BUSINESS CONSTANTS
# ============================================================

CURRENT_YEAR = datetime.now().year
MAX_ACCEPTED_FUTURE_YEAR_OFFSET = 1

# ============================================================
# GENERIC / INVALID NAMES
# ============================================================

GENERIC_NAMES = {
    "SENHORIO",
    "SENHORIOS",
    "ARRENDATARIO",
    "ARRENDATARIA",
    "ARRENDATARIOS",
    "ARRENDATARIAS",
    "PROPRIETARIO",
    "PROPRIETARIA",
    "PROPRIETARIOS",
    "PROPRIETARIAS",
    "TITULAR",
    "TITULARES",
    "BENEFICIARIO",
    "BENEFICIARIA",
    "BENEFICIARIOS",
    "BENEFICIARIAS",
    "PRIMEIRO OUTORGANTE",
    "PRIMEIRA OUTORGANTE",
    "SEGUNDO OUTORGANTE",
    "SEGUNDA OUTORGANTE",
    "OUTORGANTE",
    "OUTORGANTES",
}

# ============================================================
# CRITICAL FIELDS
# ============================================================

CRITICAL_FIELDS: dict[str, list[str]] = {
    "lease_contract": [
        "lessor",
        "lessee",
        "signed_date",
        "property_article",
        "monthly_rent",
    ],
    "property_document": [
        "property_article",
        "property_section",
        "owner_name",
    ],
    "bank_details": [
        "iban",
        "bank_account_holder",
    ],
    "payment_proof": [
        "payment_date",
        "payer",
        "payee",
        "payment_amount",
    ],
}

# ============================================================
# PATTERNS
# ============================================================

VALID_PROPERTY_SECTION_PATTERN = re.compile(r"^[A-Z]{1,3}$")
YEAR_PATTERN = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
IBAN_ALLOWED_PATTERN = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$")
PORTUGUESE_NIF_PATTERN = re.compile(r"^\d{9}$")
MONEY_PATTERN = re.compile(
    r"^(?:EUR\s*)?(?:\d{1,3}(?:[.\s]\d{3})*|\d+)(?:,\d{1,2})?\s*(?:EUR|€)?$",
    re.IGNORECASE,
)

# Known corporate-name fragments. Sprint 1B can use this list to recover a
# lessee candidate, but Sprint 1A only validates an already extracted value.
KNOWN_LESSEE_FRAGMENTS = {
    "GESTO ENERGIA",
    "GESTAO ENERGIA",
    "Q ENERGY",
    "UTU ENERGIA",
}

# ============================================================
# BASIC HELPERS
# ============================================================


def _get_value(record: Any, field_name: str) -> str:
    """Read a field from either a dataclass/object or a dictionary."""
    if isinstance(record, dict):
        value = record.get(field_name, "")
    else:
        value = getattr(record, field_name, "")

    if value is None:
        return ""
    return str(value).strip()


def normalize_text(value: str) -> str:
    """Normalize accents, casing and repeated whitespace for comparisons."""
    decomposed = unicodedata.normalize("NFKD", str(value))
    without_accents = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )
    return " ".join(without_accents.upper().split())


def compact_alphanumeric(value: str) -> str:
    """Remove spaces and punctuation while preserving letters and digits."""
    return re.sub(r"[^A-Z0-9]", "", normalize_text(value))


def extract_year(value: str) -> int | None:
    """Return the first explicit four-digit year between 1900 and 2099."""
    if not value:
        return None

    match = YEAR_PATTERN.search(str(value))
    if not match:
        return None

    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None

# ============================================================
# SPRINT 1A VALIDATIONS
# ============================================================


def validate_missing_critical_fields(record: Any) -> list[str]:
    issues: list[str] = []
    category = _get_value(record, "document_category").lower()

    for field_name in CRITICAL_FIELDS.get(category, []):
        if not _get_value(record, field_name):
            issues.append(f"missing_{field_name}")

    return issues


def validate_generic_names(record: Any) -> list[str]:
    issues: list[str] = []

    for field_name in (
        "lessor",
        "lessee",
        "owner_name",
        "bank_account_holder",
        "payer",
        "payee",
    ):
        value = _get_value(record, field_name)
        if not value:
            continue

        normalized = normalize_text(value)
        if normalized in GENERIC_NAMES:
            issues.append(f"generic_{field_name}")

    return issues


def validate_future_dates(record: Any) -> list[str]:
    issues: list[str] = []
    maximum_year = CURRENT_YEAR + MAX_ACCEPTED_FUTURE_YEAR_OFFSET

    for field_name in (
        "signed_date",
        "document_date",
        "contract_start_date",
        "contract_end_date",
        "payment_date",
    ):
        value = _get_value(record, field_name)
        if not value:
            continue

        year = extract_year(value)
        if year is not None and year > maximum_year:
            issues.append(f"future_date_{field_name}")

    return issues


def validate_property_section(record: Any) -> list[str]:
    value = _get_value(record, "property_section")
    if not value:
        return []

    normalized = compact_alphanumeric(value)
    if not VALID_PROPERTY_SECTION_PATTERN.fullmatch(normalized):
        return ["invalid_property_section"]

    return []


def validate_confidence(record: Any) -> list[str]:
    confidence = _get_value(record, "confidence").lower()

    if confidence == "low":
        return ["low_confidence"]
    if confidence and confidence not in {"low", "medium", "high"}:
        return ["invalid_confidence"]

    return []

# ============================================================
# SPRINT 1B-READY VALIDATIONS
# ============================================================


def validate_iban(record: Any) -> list[str]:
    """Validate the extracted IBAN only when a value is present."""
    iban = compact_alphanumeric(_get_value(record, "iban"))
    if not iban:
        return []

    if not IBAN_ALLOWED_PATTERN.fullmatch(iban):
        return ["invalid_iban_format"]

    # ISO 13616 MOD-97 validation.
    rearranged = iban[4:] + iban[:4]
    numeric = "".join(
        character if character.isdigit() else str(ord(character) - 55)
        for character in rearranged
    )

    remainder = 0
    for character in numeric:
        remainder = (remainder * 10 + int(character)) % 97

    return [] if remainder == 1 else ["invalid_iban_checksum"]


def validate_portuguese_tax_id(record: Any) -> list[str]:
    """Validate owner_tax_id when it contains one Portuguese NIF."""
    raw_value = _get_value(record, "owner_tax_id")
    if not raw_value:
        return []

    # Multiple owners/NIFs should be reviewed separately in a later sprint.
    candidates = re.findall(r"(?<!\d)\d{9}(?!\d)", re.sub(r"[.\s-]", "", raw_value))
    if len(candidates) != 1:
        return ["invalid_or_multiple_owner_tax_id"]

    nif = candidates[0]
    if not PORTUGUESE_NIF_PATTERN.fullmatch(nif):
        return ["invalid_owner_tax_id_format"]

    total = sum(int(nif[index]) * (9 - index) for index in range(8))
    check_digit = 11 - (total % 11)
    if check_digit >= 10:
        check_digit = 0

    return [] if check_digit == int(nif[-1]) else ["invalid_owner_tax_id_checksum"]


def validate_known_lessee(record: Any) -> list[str]:
    """Flag generic lessee values and suspiciously short corporate names."""
    category = _get_value(record, "document_category").lower()
    if category != "lease_contract":
        return []

    lessee = _get_value(record, "lessee")
    if not lessee:
        return []  # Missing value is already handled by the critical-field rule.

    normalized = normalize_text(lessee)
    if normalized in GENERIC_NAMES:
        return ["generic_lessee"]

    if len(normalized) < 4:
        return ["suspicious_lessee"]

    return []


def validate_monthly_rent(record: Any) -> list[str]:
    """Validate monthly_rent formatting without inventing or converting rent."""
    category = _get_value(record, "document_category").lower()
    if category != "lease_contract":
        return []

    value = _get_value(record, "monthly_rent")
    if not value:
        return []  # Missing value is handled by the critical-field rule.

    normalized = " ".join(value.split())
    if not MONEY_PATTERN.fullmatch(normalized):
        return ["invalid_monthly_rent_format"]

    digits = re.sub(r"[^0-9]", "", normalized)
    if not digits or int(digits) == 0:
        return ["invalid_monthly_rent_value"]

    return []

# ============================================================
# MASTER VALIDATOR
# ============================================================


ValidationFunction = Callable[[Any], list[str]]

VALIDATORS: tuple[ValidationFunction, ...] = (
    validate_missing_critical_fields,
    validate_generic_names,
    validate_future_dates,
    validate_property_section,
    validate_confidence,
    validate_iban,
    validate_portuguese_tax_id,
    validate_known_lessee,
    validate_monthly_rent,
)


def run_all_validations(record: Any) -> list[str]:
    """Run every deterministic rule and return unique, sorted issue codes."""
    issues: list[str] = []

    for validator in VALIDATORS:
        issues.extend(validator(record))

    return sorted(set(issues))


__all__ = [
    "CURRENT_YEAR",
    "CRITICAL_FIELDS",
    "GENERIC_NAMES",
    "KNOWN_LESSEE_FRAGMENTS",
    "run_all_validations",
    "validate_missing_critical_fields",
    "validate_generic_names",
    "validate_future_dates",
    "validate_property_section",
    "validate_confidence",
    "validate_iban",
    "validate_portuguese_tax_id",
    "validate_known_lessee",
    "validate_monthly_rent",
    "normalize_text",
    "compact_alphanumeric",
    "extract_year",
]
