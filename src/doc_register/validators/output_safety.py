"""Final publication guard for malformed values that recovery could not prove."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import re
from typing import Any

from ..models import ExtractionResult
from .iban_recovery import is_valid_iban, is_valid_nib, normalize_account
from .name_quality import validate_name_field


@dataclass(frozen=True)
class OutputSafetyChange:
    field: str
    old_value: str
    new_value: str
    reason: str


@dataclass
class OutputSafetyReport:
    changes: list[OutputSafetyChange] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"changes": [asdict(item) for item in self.changes]}


def apply_output_safety(result: ExtractionResult, *, document_text: str = "") -> ExtractionResult:
    """Never publish a known-invalid identifier or entity.

    Clearing is intentional: uncertain OCR values are useful only in the raw
    audit payload, not as operational metadata in the register.
    """
    report = OutputSafetyReport()
    category = (result.document_category or "").lower()
    _sanitize_bank_identifiers(result, report)
    _sanitize_property_identifiers(result, report)
    _sanitize_tax_identifier(result, report)
    _sanitize_names(result, report)
    _sanitize_document_dates(result, report, document_text)
    _sanitize_property_context_fields(result, report, document_text)
    if category not in {"lease_contract", "property_document"}:
        _set(result, report, "property_number", "", "property_number_not_applicable")
    _attach(result, report)
    return result


def _sanitize_bank_identifiers(result: ExtractionResult, report: OutputSafetyReport) -> None:
    iban = normalize_account(result.iban)
    if iban and not is_valid_iban(iban):
        _set(result, report, "iban", "", "invalid_iban_not_published")
    elif iban:
        _set(result, report, "iban", iban, "iban_normalized")
    nib = normalize_account(result.nib)
    if nib and not is_valid_nib(nib):
        _set(result, report, "nib", "", "invalid_nib_not_published")
    elif nib:
        _set(result, report, "nib", nib, "nib_normalized")


def _sanitize_property_identifiers(result: ExtractionResult, report: OutputSafetyReport) -> None:
    article = re.sub(r"\s+", "", str(result.property_article or "").upper())
    if article and not re.fullmatch(r"\d{1,8}", article):
        _set(result, report, "property_article", "", "invalid_property_article_not_published")
    elif article:
        _set(result, report, "property_article", article, "property_article_normalized")
    section = re.sub(r"\s+", "", str(result.property_section or "").upper())
    if section and not re.fullmatch(r"[A-Z]{1,3}", section):
        _set(result, report, "property_section", "", "invalid_property_section_not_published")
    elif section:
        _set(result, report, "property_section", section, "property_section_normalized")


def _sanitize_tax_identifier(result: ExtractionResult, report: OutputSafetyReport) -> None:
    value = re.sub(r"\D", "", str(result.owner_tax_id or ""))
    if value and not _valid_tax_id(value):
        _set(result, report, "owner_tax_id", "", "invalid_owner_tax_id_not_published")
    elif value:
        _set(result, report, "owner_tax_id", value, "owner_tax_id_normalized")


def _sanitize_names(result: ExtractionResult, report: OutputSafetyReport) -> None:
    for field in ("lessor", "lessee", "owner_name", "bank_account_holder", "payer", "payee"):
        value = str(getattr(result, field, "") or "").strip()
        if value and validate_name_field(field, value):
            _set(result, report, field, "", "invalid_name_not_published")


def _sanitize_document_dates(result: ExtractionResult, report: OutputSafetyReport, document_text: str) -> None:
    for field in ("document_date", "signed_date", "payment_date"):
        value = str(getattr(result, field, "") or "").strip()
        year_match = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", value)
        if year_match and int(year_match.group(1)) > datetime.now().year + 1:
            _set(result, report, field, "", "future_date_not_published")
    document_date = str(result.document_date or "").strip()
    if document_date and document_text and not _date_has_document_context(document_date, document_text):
        _set(result, report, "document_date", "", "unlabelled_document_date_not_published")


def _sanitize_property_context_fields(result: ExtractionResult, report: OutputSafetyReport, document_text: str) -> None:
    category = (result.document_category or "").lower()
    if category != "lease_contract":
        return
    property_context = _step33_property_context(result)
    for field in ("property_address", "property_location"):
        value = str(getattr(result, field, "") or "").strip()
        if value and not _literal_supported(value, property_context):
            _set(result, report, field, "", "outside_property_evidence_zone")
    property_name = str(result.property_name or "").strip()
    display_name = str(result.property_display_name or "").strip()
    if display_name and display_name != property_name:
        _set(result, report, "property_display_name", property_name, "property_display_name_synced_to_authoritative_name")
    if not str(result.owner_name or "").strip() and str(result.owner_address or "").strip():
        _set(result, report, "owner_address", "", "owner_address_without_verified_owner")


def _date_has_document_context(value: str, text: str) -> bool:
    compact = re.sub(r"\D", "", value)
    if len(compact) != 8:
        return False
    patterns = {
        value,
        f"{compact[0:2]}/{compact[2:4]}/{compact[4:]}",
        f"{compact[4:]}-{compact[2:4]}-{compact[0:2]}",
    }
    for candidate in patterns:
        for match in re.finditer(re.escape(candidate), text, re.I):
            window = text[max(0, match.start() - 80):match.end() + 80]
            if re.search(r"\b(?:data\s+de\s+(?:emiss[aã]o|documento)|emitid[oa]|emiss[aã]o|certifica[cç][aã]o)\b", window, re.I):
                return True
    return False


def _step33_property_context(result: ExtractionResult) -> str:
    raw = result.raw_json if isinstance(result.raw_json, dict) else {}
    report = raw.get("step3_3_field_centric", {}) if isinstance(raw, dict) else {}
    structure = report.get("document_structure", {}) if isinstance(report, dict) else {}
    zones = structure.get("zones", []) if isinstance(structure, dict) else []
    allowed = {"PROPERTY_SECTION", "ANNEX_CADERNETA", "ANNEX_CRP"}
    return "\n".join(
        str(zone.get("text") or "")
        for zone in zones
        if isinstance(zone, dict) and zone.get("zone_type") in allowed
    )


def _literal_supported(value: str, context: str) -> bool:
    normalized_value = re.sub(r"\W+", "", value).upper()
    normalized_context = re.sub(r"\W+", "", context).upper()
    return bool(normalized_value and normalized_value in normalized_context)


def _valid_tax_id(value: str) -> bool:
    if not re.fullmatch(r"\d{9}", value):
        return False
    total = sum(int(value[index]) * (9 - index) for index in range(8))
    digit = 11 - total % 11
    if digit >= 10:
        digit = 0
    return digit == int(value[-1])


def _set(result: ExtractionResult, report: OutputSafetyReport, field: str, value: str, reason: str) -> None:
    old = str(getattr(result, field, "") or "")
    if old == value:
        return
    setattr(result, field, value)
    report.changes.append(OutputSafetyChange(field, old, value, reason))


def _attach(result: ExtractionResult, report: OutputSafetyReport) -> None:
    raw = result.raw_json if isinstance(result.raw_json, dict) else {}
    result.raw_json = dict(raw)
    result.raw_json["output_safety"] = report.to_dict()


__all__ = ["OutputSafetyReport", "apply_output_safety"]
