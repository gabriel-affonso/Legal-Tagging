from __future__ import annotations
import re
import unicodedata
from datetime import datetime
from typing import Any, Callable

CURRENT_YEAR = datetime.now().year
MAX_ACCEPTED_FUTURE_YEAR_OFFSET = 1
GENERIC_NAMES = {
    "SENHORIO", "SENHORIOS", "ARRENDATARIO", "ARRENDATARIA",
    "ARRENDATARIOS", "ARRENDATARIAS", "PROPRIETARIO", "PROPRIETARIA",
    "TITULAR", "TITULARES", "BENEFICIARIO", "BENEFICIARIA",
    "PRIMEIRO OUTORGANTE", "SEGUNDO OUTORGANTE", "OUTORGANTE", "OUTORGANTES",
}
CRITICAL_FIELDS: dict[str, list[str]] = {
    "lease_contract": ["lessor", "lessee", "signed_date", "property_article", "monthly_rent"],
    "property_document": ["property_article", "property_section", "owner_name"],
    "bank_details": ["iban", "bank_account_holder"],
    "payment_proof": ["payment_date", "payer", "payee", "payment_amount"],
}
VALID_PROPERTY_SECTION_PATTERN = re.compile(r"^[A-Z]{1,3}$")
YEAR_PATTERN = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
IBAN_ALLOWED_PATTERN = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$")
MONEY_PATTERN = re.compile(r"^(?:EUR\s*)?(?:\d{1,3}(?:[.\s]\d{3})*|\d+)(?:,\d{1,2})?\s*(?:EUR|€)?$", re.I)

def _get(record: Any, field: str) -> str:
    value = record.get(field, "") if isinstance(record, dict) else getattr(record, field, "")
    return "" if value is None else str(value).strip()

def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value))
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).upper().split())

def compact_alphanumeric(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", normalize_text(value))

def extract_year(value: str) -> int | None:
    match = YEAR_PATTERN.search(str(value or ""))
    return int(match.group(1)) if match else None

def validate_missing_critical_fields(record: Any) -> list[str]:
    return [f"missing_{f}" for f in CRITICAL_FIELDS.get(_get(record, "document_category").lower(), []) if not _get(record, f)]

def validate_generic_names(record: Any) -> list[str]:
    issues=[]
    for field in ("lessor", "lessee", "owner_name", "bank_account_holder", "payer", "payee"):
        value=_get(record, field)
        if value and normalize_text(value) in GENERIC_NAMES:
            issues.append(f"generic_{field}")
    return issues

def validate_future_dates(record: Any) -> list[str]:
    issues=[]
    for field in ("signed_date", "document_date", "contract_start_date", "contract_end_date", "payment_date"):
        year=extract_year(_get(record, field))
        if year and year > CURRENT_YEAR + MAX_ACCEPTED_FUTURE_YEAR_OFFSET:
            issues.append(f"future_date_{field}")
    return issues

def validate_property_section(record: Any) -> list[str]:
    value=_get(record, "property_section")
    return [] if not value or VALID_PROPERTY_SECTION_PATTERN.fullmatch(compact_alphanumeric(value)) else ["invalid_property_section"]

def validate_confidence(record: Any) -> list[str]:
    value=_get(record, "confidence").lower()
    if value == "low": return ["low_confidence"]
    if value and value not in {"low", "medium", "high"}: return ["invalid_confidence"]
    return []

def validate_iban(record: Any) -> list[str]:
    iban=compact_alphanumeric(_get(record, "iban"))
    if not iban: return []
    if not IBAN_ALLOWED_PATTERN.fullmatch(iban): return ["invalid_iban_format"]
    rearranged=iban[4:]+iban[:4]
    numeric="".join(c if c.isdigit() else str(ord(c)-55) for c in rearranged)
    remainder=0
    for c in numeric: remainder=(remainder*10+int(c))%97
    return [] if remainder==1 else ["invalid_iban_checksum"]

def validate_portuguese_tax_id(record: Any) -> list[str]:
    raw=_get(record, "owner_tax_id")
    if not raw: return []
    compact=re.sub(r"[.\s-]", "", raw)
    candidates=re.findall(r"(?<!\d)\d{9}(?!\d)", compact)
    if len(candidates)!=1: return ["invalid_or_multiple_owner_tax_id"]
    nif=candidates[0]
    total=sum(int(nif[i])*(9-i) for i in range(8))
    digit=11-(total%11)
    if digit>=10: digit=0
    return [] if digit==int(nif[-1]) else ["invalid_owner_tax_id_checksum"]

def validate_known_lessee(record: Any) -> list[str]:
    if _get(record, "document_category").lower()!="lease_contract": return []
    value=_get(record, "lessee")
    if not value: return []
    if normalize_text(value) in GENERIC_NAMES: return ["generic_lessee"]
    return ["suspicious_lessee"] if len(normalize_text(value))<4 else []

def validate_monthly_rent(record: Any) -> list[str]:
    if _get(record, "document_category").lower()!="lease_contract": return []
    value=_get(record, "monthly_rent")
    if not value: return []
    if not MONEY_PATTERN.fullmatch(" ".join(value.split())): return ["invalid_monthly_rent_format"]
    digits=re.sub(r"[^0-9]", "", value)
    return ["invalid_monthly_rent_value"] if not digits or int(digits)==0 else []

ValidationFunction=Callable[[Any], list[str]]
VALIDATORS: tuple[ValidationFunction,...] = (
    validate_missing_critical_fields, validate_generic_names, validate_future_dates,
    validate_property_section, validate_confidence, validate_iban,
    validate_portuguese_tax_id, validate_known_lessee, validate_monthly_rent,
)
def run_all_validations(record: Any) -> list[str]:
    issues=[]
    for validator in VALIDATORS: issues.extend(validator(record))
    return sorted(set(issues))
