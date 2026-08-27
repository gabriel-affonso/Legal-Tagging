from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from .schemas import OFFICIAL_CATEGORIES


IBAN_RE = re.compile(r"\bPT50(?:[\s.-]*\d){21}\b", re.IGNORECASE)
BIC_RE = re.compile(r"\b[A-Z]{4}PT[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b", re.IGNORECASE)
DATE_RE = re.compile(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}\.\d{1,2}\.\d{2,4})\b")
MONEY_RE = re.compile(
    r"(?:(?:EUR|USD|GBP)\s*)?\b\d{1,3}(?:[ .]\d{3})*(?:,\d{2}|\.\d{2})?\b\s*(?:EUR|USD|GBP|€|\$|£)",
    re.IGNORECASE,
)
CONTEXTUAL_NIF_RE = re.compile(
    r"\b(?:NIF|NIPC|numero fiscal|n\.?\s*contribuinte|contribuinte)\D{0,20}(\d{9})\b",
    re.IGNORECASE,
)
ARTICLE_RE = re.compile(
    r"\b(?:artigo(?:\s+matricial)?|art\.?)\D{0,20}([A-Z0-9][A-Z0-9./ -]{0,30})",
    re.IGNORECASE,
)
SECTION_RE = re.compile(r"\bsec(?:c|ç)[aã]o\D{0,20}([A-Z0-9-]{1,12})", re.IGNORECASE)
PARISH_RE = re.compile(r"\bfreguesia(?:\s+de)?\D{0,8}([A-ZÀ-ÿ][A-ZÀ-ÿ' -]{2,80})", re.IGNORECASE)
MUNICIPALITY_RE = re.compile(r"\b(?:concelho|municipio|munic[ií]pio)(?:\s+de)?\D{0,8}([A-ZÀ-ÿ][A-ZÀ-ÿ' -]{2,80})", re.IGNORECASE)
DISTRICT_RE = re.compile(r"\bdistrito(?:\s+de)?\D{0,8}([A-ZÀ-ÿ][A-ZÀ-ÿ' -]{2,80})", re.IGNORECASE)

PAYMENT_WORD_RE = re.compile(r"\b(?:comprovativo|pagamento|transfer[êe]ncia|montante|valor pago|benefici[aá]rio|ordenante)\b", re.IGNORECASE)
BANK_WORD_RE = re.compile(r"\b(?:IBAN|NIB|BIC|SWIFT|titular|conta|banco|dados banc[aá]rios)\b", re.IGNORECASE)
PROPERTY_WORD_RE = re.compile(r"\b(?:caderneta|matriz|certid[aã]o|conservat[oó]ria|registo predial|CRP|artigo matricial|pr[eé]dio)\b", re.IGNORECASE)
LEASE_WORD_RE = re.compile(r"\b(?:contrato|arrendamento|senhorio|arrendat[aá]rio|renda|locador|locat[aá]rio)\b", re.IGNORECASE)

BANK_NAMES = {
    "activo bank",
    "activobank",
    "banco bpi",
    "banco ctt",
    "banco montepio",
    "banco santander",
    "bankinter",
    "bbva",
    "bcp",
    "caixa geral de depositos",
    "caixa geral de depósitos",
    "credito agricola",
    "crédito agrícola",
    "millennium",
    "novobanco",
    "santander",
}

GENERIC_PROPERTY_NAMES = {
    "averbamento",
    "certificate",
    "certidao",
    "certidão",
    "predial rustica",
    "predial rústica",
    "rural property",
}


@dataclass
class DeterministicSignals:
    file_category: str = ""
    suggested_category: str = "other"
    category_reasons: list[str] = field(default_factory=list)
    ibans: list[str] = field(default_factory=list)
    nibs: list[str] = field(default_factory=list)
    bic_swifts: list[str] = field(default_factory=list)
    tax_ids: list[str] = field(default_factory=list)
    monetary_values: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    property_article: str = ""
    property_section: str = ""
    property_parish: str = ""
    property_municipality: str = ""
    property_district: str = ""
    has_bank_terms: bool = False
    has_payment_terms: bool = False
    has_property_terms: bool = False
    has_lease_terms: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_category": self.file_category,
            "suggested_category": self.suggested_category,
            "category_reasons": self.category_reasons,
            "ibans": self.ibans,
            "nibs": self.nibs,
            "bic_swifts": self.bic_swifts,
            "tax_ids": self.tax_ids,
            "monetary_values": self.monetary_values,
            "dates": self.dates,
            "property_article": self.property_article,
            "property_section": self.property_section,
            "property_parish": self.property_parish,
            "property_municipality": self.property_municipality,
            "property_district": self.property_district,
            "has_bank_terms": self.has_bank_terms,
            "has_payment_terms": self.has_payment_terms,
            "has_property_terms": self.has_property_terms,
            "has_lease_terms": self.has_lease_terms,
        }


def detect_signals(file_name: str, text: str) -> DeterministicSignals:
    signals = DeterministicSignals()
    signals.file_category = _category_from_file_name(file_name, signals.category_reasons)
    signals.ibans = _unique(_normalize_account(match.group(0)) for match in IBAN_RE.finditer(text))
    signals.nibs = _detect_nibs(text, signals.ibans)
    signals.bic_swifts = _unique(match.group(0).upper() for match in BIC_RE.finditer(text))
    signals.tax_ids = _unique(match.group(1) for match in CONTEXTUAL_NIF_RE.finditer(text))
    signals.monetary_values = _unique(match.group(0).strip() for match in MONEY_RE.finditer(text))
    signals.dates = _unique(match.group(0) for match in DATE_RE.finditer(text))
    signals.property_article = _first_group(ARTICLE_RE, text)
    signals.property_section = _first_group(SECTION_RE, text)
    signals.property_parish = _clean_place(_first_group(PARISH_RE, text))
    signals.property_municipality = _clean_place(_first_group(MUNICIPALITY_RE, text))
    signals.property_district = _clean_place(_first_group(DISTRICT_RE, text))

    signals.has_bank_terms = bool(BANK_WORD_RE.search(text) or signals.ibans or signals.nibs or signals.bic_swifts)
    signals.has_payment_terms = bool(PAYMENT_WORD_RE.search(text))
    signals.has_property_terms = bool(PROPERTY_WORD_RE.search(text))
    signals.has_lease_terms = bool(LEASE_WORD_RE.search(text))
    signals.suggested_category = _suggest_category(signals)
    return signals


def is_bank_like_value(value: str) -> bool:
    normalized = _normalize_account(value)
    if normalized.startswith("PT50") and len(normalized) == 25:
        return True
    digits = re.sub(r"\D", "", value)
    return len(digits) >= 16 and not MONEY_RE.search(value)


def is_bank_name(value: str) -> bool:
    cleaned = _normalize_label(value)
    return any(name in cleaned for name in BANK_NAMES)


def is_generic_property_name(value: str) -> bool:
    cleaned = _normalize_label(value)
    return cleaned in GENERIC_PROPERTY_NAMES


def has_payment_amount_context(text: str, amount: str) -> bool:
    if not amount.strip() or is_bank_like_value(amount):
        return False
    pattern = re.escape(amount.strip())
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return bool(MONEY_RE.search(amount))
    start = max(0, match.start() - 80)
    end = min(len(text), match.end() + 80)
    window = text[start:end]
    return bool(
        re.search(r"\b(?:valor|montante|import[âa]ncia|total|pagamento|transfer[êe]ncia|renda|EUR|€)\b", window, re.IGNORECASE)
    )


def _category_from_file_name(file_name: str, reasons: list[str]) -> str:
    compact = re.sub(r"\s+", "", file_name).lower()
    lowered = file_name.lower()
    checks = [
        ("property_document", "CadernetaPredial", "cadernetapredial" in compact),
        ("property_document", "CRP", re.search(r"\bcrp\b", lowered, re.IGNORECASE) is not None),
        ("bank_details", "IBAN/NIB/BIC/SWIFT", any(token in lowered for token in ["iban", "nib", "bic", "swift"])),
        ("lease_contract", "_CA_/_CAV_/Contrato/Arrendamento", any(token in lowered for token in ["_ca_", "_cav_", "contrato", "arrendamento"])),
        ("payment_proof", "comprovativo/transferencia/pagamento", any(token in lowered for token in ["comprovativo", "transferencia", "transferência", "pagamento"])),
    ]
    for category, reason, matched in checks:
        if matched:
            reasons.append(f"filename:{reason}")
            return category
    return ""


def _suggest_category(signals: DeterministicSignals) -> str:
    if signals.file_category in OFFICIAL_CATEGORIES:
        return signals.file_category
    if signals.has_lease_terms:
        return "lease_contract"
    if signals.has_property_terms:
        return "property_document"
    if signals.has_bank_terms and not _has_clear_payment(signals):
        return "bank_details"
    if _has_clear_payment(signals):
        return "payment_proof"
    return "other"


def _has_clear_payment(signals: DeterministicSignals) -> bool:
    return signals.has_payment_terms and bool(signals.monetary_values) and bool(signals.dates)


def _detect_nibs(text: str, ibans: list[str]) -> list[str]:
    iban_digits = {iban[4:] for iban in ibans if iban.startswith("PT50")}
    values = [
        _normalize_account(match.group(1))
        for match in re.finditer(r"\bNIB\D{0,12}((?:\d[\s.-]*){21})", text, flags=re.IGNORECASE)
    ]
    for match in re.finditer(r"(?<!\d)(?:\d[\s.-]*){21}(?!\d)", text):
        normalized = _normalize_account(match.group(0))
        if len(normalized) == 21 and normalized not in iban_digits:
            values.append(normalized)
    return _unique(values)


def _first_group(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip(" .:-;\n\t")


def _clean_place(value: str) -> str:
    value = re.split(r"\b(?:concelho|distrito|artigo|sec(?:c|ç)[aã]o|morada)\b", value, flags=re.IGNORECASE)[0]
    return value.strip(" .:-;\n\t")


def _normalize_account(value: str) -> str:
    return re.sub(r"[\s.-]", "", value).upper()


def _normalize_label(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _unique(values) -> list[str]:
    seen = set()
    result = []
    for value in values:
        cleaned = str(value).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result
