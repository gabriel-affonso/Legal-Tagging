from __future__ import annotations
import re
import unicodedata
from datetime import datetime
from typing import Any, Callable

from ..contract_types import canonical_contract_type, detect_contract_type
from .iban_recovery import is_valid_iban
from .name_quality import validate_name_field

CURRENT_YEAR = datetime.now().year
MAX_ACCEPTED_FUTURE_YEAR_OFFSET = 1
GENERIC_NAMES = {
    "SENHORIO", "SENHORIOS", "ARRENDATARIO", "ARRENDATARIA",
    "ARRENDATARIOS", "ARRENDATARIAS", "PROPRIETARIO", "PROPRIETARIA",
    "TITULAR", "TITULARES", "BENEFICIARIO", "BENEFICIARIA",
    "PRIMEIRO OUTORGANTE", "SEGUNDO OUTORGANTE", "OUTORGANTE", "OUTORGANTES",
}
GENERIC_PROPERTY_NAMES = {
    "PREDIO", "PREDIO RUSTICO", "PREDIO URBANO", "PREDIO MISTO",
    "IMOVEL", "PROPRIEDADE", "TERRENO", "PARCELA", "LOCAL ARRENDADO",
    "PREDIO SOLAR", "PREDIO PARA INSTALACAO DE CENTRAL SOLAR",
}
PROPERTY_NUMBER_PLACEHOLDERS = {"0", "00", "000", "123", "1234", "12345", "123456"}
CRITICAL_FIELDS: dict[str, list[str]] = {
    "lease_contract": ["lessor", "lessee", "signed_date", "property_article", "property_section"],
    "property_document": ["property_article", "property_section", "owner_name"],
    "bank_details": ["iban", "bank_account_holder"],
    "payment_proof": ["payment_date", "payer", "payee", "payment_amount"],
}
VALID_PROPERTY_SECTION_PATTERN = re.compile(r"^[A-Z]{1,3}$")
YEAR_PATTERN = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
IBAN_ALLOWED_PATTERN = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$")
MONEY_PATTERN = re.compile(r"^(?:EUR\s*)?(?:\d{1,3}(?:[.\s]\d{3})*|\d+)(?:,\d{1,2})?\s*(?:EUR|€)?$", re.I)
PROPERTY_NUMBER_FORBIDDEN_PATTERN = re.compile(
    r"\b(?:artigo|art\.|anexo|nif|nipc|nao\s+especificado|não\s+especificado|"
    r"decreto|cl[aá]usula|codigo|c[oó]digo)\b|%",
    re.IGNORECASE,
)

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
    category = _get(record, "document_category").lower()
    issues = [
        f"missing_{field}"
        for field in CRITICAL_FIELDS.get(category, [])
        if not _get(record, field)
    ]
    if category == "lease_contract" and requires_monthly_rent(record) and not _get(record, "monthly_rent"):
        issues.append("missing_monthly_rent")
    return issues

def validate_generic_names(record: Any) -> list[str]:
    issues=[]
    for field in ("lessor", "lessee", "owner_name", "bank_account_holder", "payer", "payee"):
        value=_get(record, field)
        if value and normalize_text(value) in GENERIC_NAMES:
            issues.append(f"generic_{field}")
    return issues

def validate_party_role_conflicts(record: Any) -> list[str]:
    if _get(record, "document_category").lower() != "lease_contract":
        return []
    lessor = normalize_text(_get(record, "lessor"))
    lessee = normalize_text(_get(record, "lessee"))
    owner = normalize_text(_get(record, "owner_name"))
    issues = []
    if lessor and lessee and lessor == lessee:
        issues.append("lessor_matches_lessee")
    if owner and lessee and owner == lessee:
        issues.append("owner_matches_lessee")
    return issues

def validate_name_quality(record: Any) -> list[str]:
    issues=[]
    for field in ("lessor", "lessee", "owner_name", "bank_account_holder", "payer", "payee"):
        issues.extend(validate_name_field(field, _get(record, field)))
    return issues

def validate_future_dates(record: Any) -> list[str]:
    issues=[]
    for field in ("signed_date", "document_date", "payment_date"):
        year=extract_year(_get(record, field))
        if year and year > CURRENT_YEAR + MAX_ACCEPTED_FUTURE_YEAR_OFFSET:
            issues.append(f"future_date_{field}")
    return issues

def validate_contract_date_order(record: Any) -> list[str]:
    if _get(record, "document_category").lower() != "lease_contract":
        return []
    signed = _parse_date(_get(record, "signed_date"))
    start = _parse_date(_get(record, "contract_start_date"))
    end = _parse_date(_get(record, "contract_end_date"))
    issues = []
    if signed and start and start < signed:
        issues.append("contract_start_before_signed_date")
    if start and end and end <= start:
        issues.append("contract_end_not_after_start")
    return issues

def validate_property_section(record: Any) -> list[str]:
    value=_get(record, "property_section")
    return [] if not value or VALID_PROPERTY_SECTION_PATTERN.fullmatch(compact_alphanumeric(value)) else ["invalid_property_section"]

def validate_property_identity_fields(record: Any) -> list[str]:
    issues = []
    article = _get(record, "property_article")
    if article and not re.fullmatch(r"\d{1,8}", compact_alphanumeric(article)):
        issues.append("invalid_property_article_format")

    property_name = _get(record, "property_name")
    if property_name and normalize_text(property_name) in GENERIC_PROPERTY_NAMES:
        issues.append("generic_property_name")

    property_number = _get(record, "property_number")
    if not property_number:
        return issues
    normalized = normalize_text(property_number)
    compact = compact_alphanumeric(property_number)
    digits = re.sub(r"\D", "", property_number)
    if normalized in {"NAO ESPECIFICADO", "NAO APLICAVEL", "N A", "N D"}:
        issues.append("invalid_property_number")
    elif compact in PROPERTY_NUMBER_PLACEHOLDERS:
        issues.append("invalid_property_number")
    elif PROPERTY_NUMBER_FORBIDDEN_PATTERN.search(property_number):
        issues.append("invalid_property_number")
    elif re.fullmatch(r"[A-Z]{1,4}", compact):
        issues.append("invalid_property_number")
    elif len(digits) == 9:
        issues.append("property_number_matches_tax_id")
    elif re.search(r"[A-Z]", compact) and not re.fullmatch(r"(?:PR|VA)\d{1,4}", compact):
        issues.append("invalid_property_number")
    return issues

def validate_confidence(record: Any) -> list[str]:
    value=_get(record, "confidence").lower()
    if value == "low": return ["low_confidence"]
    if value and value not in {"low", "medium", "high"}: return ["invalid_confidence"]
    return []

def validate_ocr_quality(record: Any) -> list[str]:
    flags = {
        part.strip()
        for part in _get(record, "ocr_quality_flags").replace(",", ";").split(";")
        if part.strip()
    }
    return ["ocr_quality_low"] if "ocr_quality_low" in flags else []

def validate_iban(record: Any) -> list[str]:
    iban=compact_alphanumeric(_get(record, "iban"))
    if not iban: return []
    if not IBAN_ALLOWED_PATTERN.fullmatch(iban): return ["invalid_iban_format"]
    return [] if is_valid_iban(iban) else ["invalid_iban_checksum"]

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

def validate_contract_prices(record: Any) -> list[str]:
    if _get(record, "document_category").lower()!="lease_contract": return []
    issues=[]
    for field in ("option_price", "purchase_price", "assignment_price"):
        value=_get(record, field)
        if not value:
            continue
        compact=re.sub(r"[^A-Z0-9]", "", normalize_text(value))
        if compact.startswith("PT50") or len(re.sub(r"\D", "", value)) >= 16:
            issues.append(f"invalid_{field}_format")
        elif not MONEY_PATTERN.fullmatch(" ".join(value.split())):
            issues.append(f"invalid_{field}_format")
        elif int(re.sub(r"[^0-9]", "", value) or "0") == 0:
            issues.append(f"invalid_{field}_value")
    return issues

def validate_contract_amount_consistency(record: Any) -> list[str]:
    if _get(record, "document_category").lower()!="lease_contract":
        return []
    issues=[]
    monthly=_money_key(_get(record, "monthly_rent"))
    if monthly and not requires_monthly_rent(record):
        issues.append("suspicious_monthly_rent_for_contract_subtype")
    for field in ("option_price", "purchase_price", "assignment_price"):
        price=_money_key(_get(record, field))
        if monthly and price and monthly == price:
            issues.append(f"monthly_rent_conflicts_with_{field}")
    return issues

def validate_final_resolution_conflicts(record: Any) -> list[str]:
    raw = record.get("raw_json", {}) if isinstance(record, dict) else getattr(record, "raw_json", {})
    if not isinstance(raw, dict):
        return []
    resolution = raw.get("step2_7_final_resolution", {})
    if not isinstance(resolution, dict):
        return []
    conflicts = resolution.get("conflicts", [])
    if not isinstance(conflicts, list):
        return []
    return sorted({
        str(item.get("type") or "")
        for item in conflicts
        if isinstance(item, dict) and item.get("type") and not _superseded_final_conflict(record, item)
    })


def _superseded_final_conflict(record: Any, conflict: dict[str, Any]) -> bool:
    conflict_type = str(conflict.get("type") or "")
    raw = record.get("raw_json", {}) if isinstance(record, dict) else getattr(record, "raw_json", {})
    report = raw.get("step3_3_field_centric", {}) if isinstance(raw, dict) else {}
    evidence = report.get("field_evidence", {}) if isinstance(report, dict) else {}
    if not isinstance(evidence, dict):
        return False
    if conflict_type == "contract_caderneta_field_conflict":
        field = str(conflict.get("field") or "")
        item = evidence.get(field, {})
        return isinstance(item, dict) and str(item.get("source") or "").lower() in {"caderneta", "crp"}
    if conflict_type == "multiple_internal_cadernetas_unresolved":
        fields = ("property_article", "property_section", "property_name")
        return any(
            isinstance(evidence.get(field), dict)
            and str(evidence[field].get("source") or "").lower() == "caderneta"
            for field in fields
        )
    if conflict_type in {"ownership_source_conflict", "cadastral_owner_matches_lessee"}:
        return not _get(record, "owner_name")
    return False

def requires_monthly_rent(record: Any) -> bool:
    if _get(record, "document_category").lower() != "lease_contract":
        return False
    # Step 2.8 represents non-monthly rent explicitly.  An annual/per-hectare
    # obligation is complete evidence of rent, not a missing monthly amount.
    if _get(record, "rent_amount") and _get(record, "rent_frequency").lower() not in {"", "monthly"}:
        return False
    subtype_text = " ".join(
        _get(record, field)
        for field in ("document_type", "document_subtype", "contract_type")
    )
    canonical = (
        canonical_contract_type(_get(record, "contract_type"))
        or canonical_contract_type(_get(record, "document_subtype"))
        or detect_contract_type(subtype_text)
    )
    if canonical is not None:
        return canonical.requires_monthly_rent

    normalized = normalize_text(subtype_text)
    non_rent_markers = {
        "OPCAO", "OPTION", "CPCV", "PROMESSA", "COMPRA", "AQUISICAO",
        "RATIFICACAO", "HABILITACAO", "HERDEIROS", "REPRESENTACAO",
    }
    if any(marker in normalized for marker in non_rent_markers):
        return False
    rent_markers = {"ARRENDAMENTO", "LOCACAO", "LEASE", "RENDA", "RENT"}
    if any(marker in normalized for marker in rent_markers):
        return True
    return True

ValidationFunction=Callable[[Any], list[str]]
VALIDATORS: tuple[ValidationFunction,...] = (
    validate_missing_critical_fields, validate_generic_names, validate_future_dates,
    validate_contract_date_order, validate_name_quality, validate_property_section, validate_confidence,
    validate_ocr_quality, validate_iban,
    validate_portuguese_tax_id, validate_known_lessee, validate_monthly_rent,
    validate_contract_prices, validate_contract_amount_consistency,
    validate_final_resolution_conflicts,
    validate_party_role_conflicts, validate_property_identity_fields,
)
def run_all_validations(record: Any) -> list[str]:
    issues=[]
    for validator in VALIDATORS: issues.extend(validator(record))
    return sorted(set(issues))

def _money_key(value: str) -> str:
    if not value:
        return ""
    return re.sub(r"[^0-9]", "", value)


def _parse_date(value: str) -> datetime | None:
    value = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None
