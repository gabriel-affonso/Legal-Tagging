from __future__ import annotations

from datetime import datetime
import re

from .detectors import (
    DeterministicSignals,
    has_payment_amount_context,
    is_bank_like_value,
    is_bank_name,
    is_generic_property_name,
)
from .models import ExtractionResult
from .schemas import OFFICIAL_CATEGORIES, REQUIRED_FOR_REVIEW


OWNER_ADDRESS_HINT_RE = re.compile(r"\b(?:domicilio fiscal|domic[ií]lio fiscal|morada fiscal|resid[eê]ncia|sede)\b", re.IGNORECASE)
PAYMENT_CLEAR_RE = re.compile(r"\b(?:comprovativo|pagamento|transfer[êe]ncia|valor pago|montante pago|benefici[aá]rio|ordenante)\b", re.IGNORECASE)


def validate_result(result: ExtractionResult, signals: DeterministicSignals, text: str) -> ExtractionResult:
    reasons: list[str] = []
    result.document_category = _normalize_category(result.document_category or signals.suggested_category)

    _apply_deterministic_values(result, signals)
    _normalize_dates(result)
    _normalize_currency(result)
    _normalize_money(result, text, reasons)
    _apply_negative_rules(result, text, reasons)
    _normalize_property_display_name(result, reasons)
    _apply_category_rules(result, text, reasons)
    _apply_review_rules(result, signals, reasons)

    result.processing_status = "processed"
    result.processed_ok = "yes"
    result.needs_review = "yes" if reasons else "no"
    result.review_reason = _join_notes(result.review_reason, ", ".join(_dedupe(reasons)))
    return result


def _apply_deterministic_values(result: ExtractionResult, signals: DeterministicSignals) -> None:
    result.iban = result.iban or _first(signals.ibans)
    result.nib = result.nib or _first(signals.nibs)
    result.bic_swift = result.bic_swift or _first(signals.bic_swifts)
    result.property_article = result.property_article or signals.property_article
    result.property_section = result.property_section or signals.property_section
    result.property_parish = result.property_parish or signals.property_parish
    result.property_municipality = result.property_municipality or signals.property_municipality
    result.property_district = result.property_district or signals.property_district
    result.owner_tax_id = result.owner_tax_id or _first(signals.tax_ids)


def _normalize_dates(result: ExtractionResult) -> None:
    for field_name in [
        "document_date",
        "signed_date",
        "contract_start_date",
        "contract_end_date",
        "payment_date",
    ]:
        setattr(result, field_name, _to_iso_date(getattr(result, field_name)))


def _normalize_currency(result: ExtractionResult) -> None:
    value = result.currency.strip().upper()
    if value in {"€", "EURO", "EUROS"}:
        result.currency = "EUR"
    elif value in {"$", "DOLLAR", "DOLLARS"}:
        result.currency = "USD"
    elif value in {"£", "POUND", "POUNDS"}:
        result.currency = "GBP"
    elif value in {"EUR", "USD", "GBP"}:
        result.currency = value

    if not result.currency:
        combined = " ".join([result.monthly_rent, result.payment_amount])
        if "€" in combined or "EUR" in combined.upper():
            result.currency = "EUR"


def _normalize_money(result: ExtractionResult, text: str, reasons: list[str]) -> None:
    raw_monthly_rent = result.monthly_rent
    raw_payment_amount = result.payment_amount
    result.monthly_rent = _stable_amount(result.monthly_rent)
    result.payment_amount = _stable_amount(result.payment_amount)
    if result.payment_amount and not (
        has_payment_amount_context(text, raw_payment_amount) or has_payment_amount_context(text, result.payment_amount)
    ):
        result.payment_amount = ""
        reasons.append("payment_amount_without_clear_payment_context")
    if result.monthly_rent and (is_bank_like_value(raw_monthly_rent) or is_bank_like_value(result.monthly_rent)):
        result.monthly_rent = ""
        reasons.append("monthly_rent_looked_like_bank_number")


def _apply_negative_rules(result: ExtractionResult, text: str, reasons: list[str]) -> None:
    if result.payment_amount and is_bank_like_value(result.payment_amount):
        result.payment_amount = ""
        reasons.append("payment_amount_looked_like_bank_number")
    if result.property_name and is_bank_name(result.property_name):
        result.property_name = ""
        reasons.append("property_name_looked_like_bank_name")
    if result.property_display_name and is_generic_property_name(result.property_display_name):
        result.property_display_name = ""
        reasons.append("property_display_name_generic")
    if result.property_name and is_generic_property_name(result.property_name):
        result.property_name = ""
        reasons.append("property_name_generic")
    if result.bank_account_holder and result.property_name and _same_label(result.bank_account_holder, result.property_name):
        result.property_name = ""
        reasons.append("property_name_matched_bank_account_holder")
    if result.property_address and OWNER_ADDRESS_HINT_RE.search(result.property_address):
        result.owner_address = result.owner_address or result.property_address
        result.property_address = ""
        reasons.append("property_address_looked_like_owner_address")

    for field_name in ["property_name", "property_number", "property_display_name"]:
        value = getattr(result, field_name)
        if value and is_bank_like_value(value):
            setattr(result, field_name, "")
            reasons.append(f"{field_name}_looked_like_bank_number")

    if result.document_category == "bank_details" and not PAYMENT_CLEAR_RE.search(text):
        if result.payment_amount:
            result.payment_amount = ""
            reasons.append("bank_details_payment_amount_cleared")
        if result.property_name and not _has_property_context(text, result.property_name):
            result.property_name = ""
            reasons.append("bank_details_property_name_without_property_context")


def _normalize_property_display_name(result: ExtractionResult, reasons: list[str]) -> None:
    if result.property_display_name and not is_generic_property_name(result.property_display_name):
        return
    parts = [
        result.property_name,
        result.property_article and f"Artigo {result.property_article}",
        result.property_section and f"Secção {result.property_section}",
        result.property_number,
    ]
    result.property_display_name = " - ".join(part for part in parts if part)
    if result.property_display_name and is_generic_property_name(result.property_display_name):
        result.property_display_name = ""
        reasons.append("property_display_name_generic_after_build")


def _apply_category_rules(result: ExtractionResult, text: str, reasons: list[str]) -> None:
    if result.document_category == "property_document":
        if result.lessor or result.lessee:
            if not re.search(r"\b(?:senhorio|arrendador|arrendat[aá]rio|inquilino|locador|locat[aá]rio)\b", text, re.IGNORECASE):
                result.lessor = ""
                result.lessee = ""
                reasons.append("property_document_contract_parties_cleared")
    if result.document_category == "payment_proof":
        for field_name in REQUIRED_FOR_REVIEW["payment_proof"]:
            if not getattr(result, field_name).strip():
                reasons.append(f"missing_{field_name}")


def _apply_review_rules(result: ExtractionResult, signals: DeterministicSignals, reasons: list[str]) -> None:
    confidence = result.confidence.strip().lower()
    if confidence in {"", "low", "medium"}:
        reasons.append(f"confidence={confidence or 'blank'}")
    if result.document_category == "other":
        reasons.append("document_category=other")
    if signals.suggested_category != "other" and result.document_category != signals.suggested_category:
        reasons.append(f"category_changed_deterministic={signals.suggested_category}_llm={result.document_category}")
    native_chars = _int_or_zero(result.native_text_chars)
    ocr_chars = _int_or_zero(result.ocr_text_chars)
    if max(native_chars, ocr_chars) < 300:
        reasons.append("extracted_text_too_short")
    for field_name in REQUIRED_FOR_REVIEW.get(result.document_category, []):
        if not getattr(result, field_name).strip():
            reasons.append(f"missing_{field_name}")
    if result.extraction_notes.strip():
        reasons.append("extraction_notes")
    if result.iban and any(is_bank_like_value(getattr(result, field)) for field in ["payment_amount", "property_name", "property_number"]):
        reasons.append("iban_detected_in_suspicious_field")


def _normalize_category(category: str) -> str:
    value = category.strip().lower()
    if value == "identification":
        value = "identity_document"
    return value if value in OFFICIAL_CATEGORIES else "other"


def _to_iso_date(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%d.%m.%y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return raw


def _stable_amount(value: str) -> str:
    raw = value.strip()
    if not raw or is_bank_like_value(raw):
        return ""
    cleaned = re.sub(r"[^\d,.-]", "", raw)
    if not cleaned:
        return ""
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    return cleaned


def _has_property_context(text: str, value: str) -> bool:
    pattern = re.escape(value.strip())
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return False
    window = text[max(0, match.start() - 120) : min(len(text), match.end() + 120)]
    return bool(re.search(r"\b(?:im[oó]vel|pr[eé]dio|fra[cç][aã]o|artigo|morada|localiza[cç][aã]o)\b", window, re.IGNORECASE))


def _same_label(left: str, right: str) -> bool:
    return re.sub(r"\s+", " ", left.strip().lower()) == re.sub(r"\s+", " ", right.strip().lower())


def _join_notes(*notes: str) -> str:
    return " | ".join(note.strip() for note in notes if note and note.strip())


def _first(values: list[str]) -> str:
    return values[0] if values else ""


def _int_or_zero(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
