from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata
from typing import Any, Iterable

from .schemas import OFFICIAL_CATEGORIES


IBAN_CANDIDATE_RE = re.compile(r"\bPT50(?:[\s.-]*\d){21}\b", re.IGNORECASE)
BIC_RE = re.compile(r"\b[A-Z]{4}PT[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b", re.IGNORECASE)
DATE_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
    r"\d{1,2}\.\d{1,2}\.\d{2,4})\b"
)
MONEY_RE = re.compile(
    r"(?:(?:EUR|USD|GBP)\s*)?\b\d{1,3}(?:[ .]\d{3})*(?:,\d{2}|\.\d{2})?\b"
    r"\s*(?:EUR|USD|GBP|€|\$|£)",
    re.IGNORECASE,
)
CONTEXTUAL_NIF_RE = re.compile(
    r"\b(?:NIF|NIPC|n[uú]mero fiscal|n\.?\s*(?:de\s+)?contribuinte|contribuinte)"
    r"\D{0,20}(\d{9})\b",
    re.IGNORECASE,
)

# Captures only the identifier token. It deliberately stops before prose such
# as "da secção", which previously produced values like "440 da sec".
ARTICLE_RE = re.compile(
    r"\b(?:artigo(?:\s+matricial)?|art\.?)\s*(?:n[.ºo°]*\s*)?[:#-]?\s*"
    r"([0-9]{1,10}(?:\s*[-/]\s*[A-Z0-9]{1,10})?|[A-Z]{1,4}\s*-?\s*[0-9]{1,10})\b",
    re.IGNORECASE,
)
MATRIX_ARTICLE_RE = re.compile(
    r"\bmatriz(?:\s+predial)?(?:\s+r[uú]stica)?\s*(?:n[.ºo°]*\s*)?[:#-]?\s*"
    r"([0-9]{1,10})\b",
    re.IGNORECASE,
)
# Portuguese matrix sections are normally short alphabetic identifiers. Words
# such as CONFRONTA can no longer be accepted as a section.
SECTION_RE = re.compile(
    r"\bsec(?:c|ç)[aã]o\s*(?:n[.ºo°]*\s*)?[:#-]?\s*([A-Z]{1,3})\b",
    re.IGNORECASE,
)
ARTICLE_SECTION_RE = re.compile(
    r"\b(?:artigo|matriz)\s*(?:n[.ºo°]*\s*)?[:#-]?\s*([0-9]{1,10})"
    r"(?:\s*(?:,|-)?\s*sec(?:c|ç)[aã]o\s*[:#-]?\s*([A-Z]{1,3}))?\b",
    re.IGNORECASE,
)

# Place extraction is line-oriented. Capturing arbitrary 80-character spans was
# the source of truncated/merged values such as "oias Mogadouro" and "uro".
PARISH_LABEL_RE = re.compile(r"\b(?:freguesia|par[oó]quia)(?:\s+de)?\s*[:#-]?\s*(.+)$", re.IGNORECASE)
MUNICIPALITY_LABEL_RE = re.compile(r"\b(?:concelho|munic[ií]pio)(?:\s+de)?\s*[:#-]?\s*(.+)$", re.IGNORECASE)
DISTRICT_LABEL_RE = re.compile(r"\bdistrito(?:\s+de)?\s*[:#-]?\s*(.+)$", re.IGNORECASE)
CODED_PARISH_RE = re.compile(r"^\s*\d{1,3}\s*[-–:]\s*([A-ZÀ-ÖØ-Þ][A-ZÀ-ÖØ-Þ' .-]{2,60})\s*$", re.IGNORECASE)
CODED_MUNICIPALITY_RE = re.compile(r"^\s*\d{1,3}\s*[-–:]\s*([A-ZÀ-ÖØ-Þ][A-ZÀ-ÖØ-Þ' .-]{2,60})\s*$", re.IGNORECASE)
CODED_DISTRICT_RE = re.compile(r"^\s*\d{1,3}\s*[-–:]\s*([A-ZÀ-ÖØ-Þ][A-ZÀ-ÖØ-Þ' .-]{2,60})\s*$", re.IGNORECASE)

PAYMENT_WORD_RE = re.compile(
    r"\b(?:comprovativo|pagamento|transfer[êe]ncia|montante|valor pago|"
    r"benefici[aá]rio|ordenante)\b",
    re.IGNORECASE,
)
# Avoid generic words such as "titular" and "conta" alone. They occur often in
# fiscal/property documents and previously caused false bank-term positives.
BANK_WORD_RE = re.compile(
    r"\b(?:IBAN|NIB|BIC|SWIFT|dados banc[aá]rios|conta banc[aá]ria|"
    r"institui[cç][aã]o banc[aá]ria|banco)\b",
    re.IGNORECASE,
)
PROPERTY_WORD_RE = re.compile(
    r"\b(?:caderneta|matriz|certid[aã]o|conservat[oó]ria|registo predial|CRP|"
    r"artigo matricial|pr[eé]dio)\b",
    re.IGNORECASE,
)
LEASE_WORD_RE = re.compile(
    r"\b(?:contrato\s+de\s+arrendamento|arrendamento|senhorio|arrendat[aá]rio|"
    r"renda|locador|locat[aá]rio)\b",
    re.IGNORECASE,
)

BANK_NAMES = {
    "activo bank", "activobank", "banco bpi", "banco ctt", "banco montepio",
    "banco santander", "bankinter", "bbva", "bcp", "caixa geral de depositos",
    "caixa geral de depósitos", "credito agricola", "crédito agrícola",
    "millennium", "novobanco", "santander",
}
GENERIC_PROPERTY_NAMES = {
    "averbamento", "certificate", "certidao", "certidão", "predial rustica",
    "predial rústica", "rural property",
}
STOP_PLACE_WORDS = {
    "artigo", "matriz", "seccao", "secção", "concelho", "municipio",
    "município", "distrito", "morada", "localizacao", "localização",
    "titular", "proprietario", "proprietário", "confronta", "confrontacoes",
    "confrontações",
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
    normalized_text = _normalize_ocr_text(text)

    iban_candidates = (_normalize_account(m.group(0)) for m in IBAN_CANDIDATE_RE.finditer(normalized_text))
    signals.ibans = _unique(value for value in iban_candidates if _is_valid_iban(value))
    signals.nibs = _detect_nibs(normalized_text, signals.ibans)
    signals.bic_swifts = _unique(m.group(0).upper() for m in BIC_RE.finditer(normalized_text))
    signals.tax_ids = _unique(
        m.group(1) for m in CONTEXTUAL_NIF_RE.finditer(normalized_text) if _is_valid_portuguese_tax_id(m.group(1))
    )
    signals.monetary_values = _unique(m.group(0).strip() for m in MONEY_RE.finditer(normalized_text))
    signals.dates = _unique(m.group(0) for m in DATE_RE.finditer(normalized_text))

    article, section = _extract_article_and_section(normalized_text)
    signals.property_article = article
    signals.property_section = section
    signals.property_parish = _extract_place(normalized_text, PARISH_LABEL_RE, "parish")
    signals.property_municipality = _extract_place(normalized_text, MUNICIPALITY_LABEL_RE, "municipality")
    signals.property_district = _extract_place(normalized_text, DISTRICT_LABEL_RE, "district")

    signals.has_bank_terms = bool(BANK_WORD_RE.search(normalized_text) or signals.ibans or signals.nibs or signals.bic_swifts)
    signals.has_payment_terms = bool(PAYMENT_WORD_RE.search(normalized_text))
    signals.has_property_terms = bool(PROPERTY_WORD_RE.search(normalized_text))
    signals.has_lease_terms = bool(LEASE_WORD_RE.search(normalized_text))
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
    return _normalize_label(value) in GENERIC_PROPERTY_NAMES


def has_payment_amount_context(text: str, amount: str) -> bool:
    if not amount.strip() or is_bank_like_value(amount):
        return False
    pattern = re.escape(amount.strip())
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return bool(MONEY_RE.search(amount))
    window = text[max(0, match.start() - 80):min(len(text), match.end() + 80)]
    return bool(re.search(
        r"\b(?:valor|montante|import[âa]ncia|total|pagamento|transfer[êe]ncia|renda|EUR|€)\b",
        window,
        re.IGNORECASE,
    ))


def _extract_article_and_section(text: str) -> tuple[str, str]:
    # Prefer a single labelled expression such as "MATRIZ 140 SECÇÃO J".
    for match in ARTICLE_SECTION_RE.finditer(text):
        article = _clean_identifier(match.group(1))
        section = _clean_section(match.group(2) or "")
        if article:
            return article, section or _first_valid_section(text)

    article = _first_group(ARTICLE_RE, text) or _first_group(MATRIX_ARTICLE_RE, text)
    return _clean_identifier(article), _first_valid_section(text)


def _first_valid_section(text: str) -> str:
    for match in SECTION_RE.finditer(text):
        value = _clean_section(match.group(1))
        if value:
            return value
    return ""


def _clean_identifier(value: str) -> str:
    value = re.sub(r"\s*([/-])\s*", r"\1", value.strip().upper())
    return value if re.fullmatch(r"(?:\d{1,10}(?:[-/][A-Z0-9]{1,10})?|[A-Z]{1,4}-?\d{1,10})", value) else ""


def _clean_section(value: str) -> str:
    value = value.strip().upper()
    if not re.fullmatch(r"[A-Z]{1,3}", value):
        return ""
    if value in {"ART", "DA", "DE", "DO", "DOS", "DAS"}:
        return ""
    return value


def _extract_place(text: str, labelled_pattern: re.Pattern[str], kind: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    for line in lines:
        match = labelled_pattern.search(line)
        if match:
            cleaned = _clean_place(match.group(1))
            if cleaned:
                return cleaned

    # Cadernetas often encode places as sequential coded rows. Only use this
    # conservative fallback when the surrounding document has strong labels.
    labels = {
        "parish": ("freguesia", CODED_PARISH_RE),
        "municipality": ("concelho", CODED_MUNICIPALITY_RE),
        "district": ("distrito", CODED_DISTRICT_RE),
    }
    label, coded_pattern = labels[kind]
    for index, line in enumerate(lines):
        if label in _normalize_label(line):
            for candidate in lines[index:index + 3]:
                match = coded_pattern.match(candidate)
                if match:
                    cleaned = _clean_place(match.group(1))
                    if cleaned:
                        return cleaned
    return ""


def _clean_place(value: str) -> str:
    value = value.strip(" .,:;-\t")
    value = re.split(
        r"\b(?:artigo|matriz|sec(?:c|ç)[aã]o|concelho|munic[ií]pio|distrito|morada|titular|confronta(?:ções|coes)?)\b",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" .,:;-\t")
    if not value or len(value) < 3 or len(value) > 80:
        return ""
    if not re.fullmatch(r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ' .-]*", value):
        return ""
    if _normalize_label(value) in {_normalize_label(v) for v in STOP_PLACE_WORDS}:
        return ""
    return re.sub(r"\s+", " ", value)


def _category_from_file_name(file_name: str, reasons: list[str]) -> str:
    compact = re.sub(r"\s+", "", file_name).lower()
    lowered = file_name.lower()
    checks = [
        ("property_document", "CadernetaPredial", "cadernetapredial" in compact),
        ("property_document", "CRP", re.search(r"(?:^|[_\-\s])crp(?:[_\-\s.]|$)", lowered) is not None),
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
    if _has_clear_payment(signals):
        return "payment_proof"
    if signals.has_bank_terms:
        return "bank_details"
    return "other"


def _has_clear_payment(signals: DeterministicSignals) -> bool:
    return signals.has_payment_terms and bool(signals.monetary_values) and bool(signals.dates)


def _detect_nibs(text: str, ibans: list[str]) -> list[str]:
    iban_digits = {iban[4:] for iban in ibans if iban.startswith("PT50")}
    values: list[str] = []
    for match in re.finditer(r"\bNIB\D{0,12}((?:\d[\s.-]*){21})", text, flags=re.IGNORECASE):
        candidate = _normalize_account(match.group(1))
        if _is_valid_nib(candidate) and candidate not in iban_digits:
            values.append(candidate)
    # Unlabelled 21-digit candidates are accepted only if checksum-valid.
    for match in re.finditer(r"(?<!\d)(?:\d[\s.-]*){21}(?!\d)", text):
        candidate = _normalize_account(match.group(0))
        if _is_valid_nib(candidate) and candidate not in iban_digits:
            values.append(candidate)
    return _unique(values)


def _is_valid_iban(value: str) -> bool:
    value = _normalize_account(value)
    if not re.fullmatch(r"PT50\d{21}", value):
        return False
    rearranged = value[4:] + "2529" + value[2:4]  # P=25, T=29
    remainder = 0
    for char in rearranged:
        remainder = (remainder * 10 + int(char)) % 97
    return remainder == 1


def _is_valid_nib(value: str) -> bool:
    if not re.fullmatch(r"\d{21}", value):
        return False
    # Portuguese NIB check digits occupy positions 9-10. Its 21 digits also
    # form the BBAN portion of a PT IBAN; conversion with PT50 is sufficient.
    return _is_valid_iban("PT50" + value)


def _is_valid_portuguese_tax_id(value: str) -> bool:
    if not re.fullmatch(r"\d{9}", value):
        return False
    digits = [int(ch) for ch in value]
    total = sum(digits[index] * (9 - index) for index in range(8))
    check = 11 - (total % 11)
    if check >= 10:
        check = 0
    return check == digits[8]


def _first_group(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return re.sub(r"\s+", " ", match.group(1)).strip(" .:-;\n\t") if match else ""


def _normalize_ocr_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\u00a0\u2007\u202f]", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text


def _normalize_account(value: str) -> str:
    return re.sub(r"[\s.-]", "", value).upper()


def _normalize_label(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.strip().lower())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value)


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = str(value).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result
