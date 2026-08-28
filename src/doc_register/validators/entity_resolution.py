from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any

from ..models import ExtractionResult
from .name_quality import validate_name_field
from .validation_rules import compact_alphanumeric, normalize_text


PROPERTY_CODE_RE = re.compile(
    r"(?<![A-Z0-9])(?P<prefix>PR|VA)[ _-]?(?P<numbers>\d{1,4}(?:[ _-]+\d{1,4})*)",
    re.IGNORECASE,
)
FILENAME_NOISE_RE = re.compile(
    r"\b(?:CA|CAV|CAOPC|COPC|CRP|IBAN|NIB|BIC|SWIFT|SP\d+|OCR|PDF|"
    r"CONTRATO|ARRENDAMENTO|CADERNETA|PREDIAL|RATIFICACAO|RATIFICAÇÃO|"
    r"RECTIFICADO|RECTIFICADA|ASSINADO|ASSINADA)\b",
    re.IGNORECASE,
)
PROPERTY_NAME_PATTERNS = (
    re.compile(
        r"\bpr[eé]dio(?:\s+(?:r[uú]stico|urbano|misto))?\s+"
        r"(?:denominad[oa]|designad[oa])(?:\s+por)?\s+"
        r"[\"'“”«»]?(?P<value>[^\"'“”«».;]{2,160})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:denominad[oa]|designad[oa])(?:\s+por)?\s+"
        r"[\"'“”«»]?(?P<value>[^\"'“”«».;]{2,160})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bconhecid[oa]\s+por\s+[\"'“”«»]?(?P<value>[^\"'“”«».;]{2,160})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:sito|situado|localizado)\s+(?:no\s+)?(?:lugar|local)\s+de\s+"
        r"[\"'“”«»]?(?P<value>[^\"'“”«»\n.;]{2,120})",
        re.IGNORECASE,
    ),
)
PROPERTY_NAME_STOP_RE = re.compile(
    r"\b(?:sito|situado|localizado|freguesia|concelho|munic[ií]pio|distrito|inscrito|matriz|artigo|"
    r"sec(?:c|ç)[aã]o|com\s+[aá]rea|descrito|conservat[oó]ria|NIF|NIPC)\b",
    re.IGNORECASE,
)
GENERIC_PARTY_VALUES = {
    "SENHORIO", "SENHORIOS", "SENHORIO 1", "SENHORIO 2",
    "LOCADOR", "LOCADORES", "PROPRIETARIO", "PROPRIETARIA",
    "PROPRIETARIOS", "PROPRIETARIAS", "ARRENDATARIO", "ARRENDATARIA",
    "ARRENDATARIOS", "ARRENDATARIAS", "OUTORGANTE", "OUTORGANTES",
    "PRIMEIRO OUTORGANTE", "PRIMEIRA OUTORGANTE",
    "SEGUNDO OUTORGANTE", "SEGUNDA OUTORGANTE", "AS PARTES",
}
GENERIC_PROPERTY_NAMES = {
    "PREDIO", "PREDIO RUSTICO", "PREDIO URBANO", "PREDIO MISTO",
    "IMOVEL", "PROPRIEDADE", "TERRENO", "PARCELA", "LOCAL ARRENDADO",
    "PREDIO SOLAR", "PREDIO PARA INSTALACAO DE CENTRAL SOLAR",
    "LOCADO", "ARRENDADO",
}
PROPERTY_NAME_STOPWORDS = {"POR", "DE", "EM", "O", "A", "OS", "AS"}
KNOWN_PROPERTY_NAMES = {
    "AGUAXAIS": "Aguaxais",
}
PLACEHOLDER_NUMBERS = {"0", "00", "000", "123", "1234", "12345", "123456"}
PROPERTY_NUMBER_FORBIDDEN_RE = re.compile(
    r"\b(?:artigo|art\.|anexo|nif|nipc|nao\s+especificado|não\s+especificado|"
    r"decreto|cl[aá]usula|codigo|c[oó]digo)\b|%",
    re.IGNORECASE,
)
EVIDENCE_GATED_FIELDS = {
    "owner_name",
    "owner_tax_id",
    "property_name",
    "property_article",
    "property_section",
    "property_parish",
    "property_municipality",
    "property_district",
    "property_location",
    "property_address",
    "monthly_rent",
    "currency",
    "option_price",
    "purchase_price",
    "assignment_price",
}
CURRENCY_ALIASES = {
    "EUR": ("EUR", "EURO", "EUROS", "€"),
    "USD": ("USD", "DOLAR", "DOLARES", "$"),
    "GBP": ("GBP", "LIBRA", "LIBRAS", "£"),
}


@dataclass(frozen=True)
class EntityResolutionChange:
    field: str
    old_value: str
    new_value: str
    method: str
    reason: str


@dataclass
class EntityResolutionReport:
    property_codes: list[str] = field(default_factory=list)
    property_numbers: list[str] = field(default_factory=list)
    filename_owner_candidates: list[str] = field(default_factory=list)
    changes: list[EntityResolutionChange] = field(default_factory=list)
    blocked_values: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "property_codes": list(self.property_codes),
            "property_numbers": list(self.property_numbers),
            "filename_owner_candidates": list(self.filename_owner_candidates),
            "changes": [asdict(change) for change in self.changes],
            "blocked_values": list(self.blocked_values),
        }


def apply_entity_resolution(
    result: ExtractionResult,
    *,
    file_name: str = "",
    document_text: str = "",
) -> tuple[ExtractionResult, EntityResolutionReport]:
    """Resolve owner and property identity after extraction and AI review.

    Step 2.6 deliberately prefers auditable deterministic evidence over generic
    LLM strings. Suspicious values are cleared instead of being treated as facts.
    """
    report = EntityResolutionReport()
    report.property_codes = extract_property_codes(file_name)
    report.property_numbers = [_number_from_code(code) for code in report.property_codes]
    report.filename_owner_candidates = owner_candidates_from_filename(file_name)

    _sanitize_party_fields(result, report)
    _sanitize_property_name(result, document_text, report)
    _resolve_property_number(result, file_name, document_text, report)
    _resolve_lessor_from_filename(result, document_text, report)
    _resolve_contract_owner(result, report)
    _copy_property_location_to_name(result, report)
    _clear_unsupported_values(result, document_text, report)

    _attach_report(result, report)
    return result, report


def extract_property_codes(file_name: str) -> list[str]:
    output: list[str] = []
    for match in PROPERTY_CODE_RE.finditer(file_name or ""):
        prefix = match.group("prefix").upper()
        for raw_number in re.findall(r"\d{1,4}", match.group("numbers")):
            code = f"{prefix}{raw_number.zfill(3)}"
            if code not in output:
                output.append(code)
    return output


def owner_candidates_from_filename(file_name: str) -> list[str]:
    stem = (file_name or "").rsplit(".", 1)[0]
    stem = re.sub(r"__?[0-9a-fA-F]{12,64}(?:__ocr)?$", "", stem, flags=re.IGNORECASE)
    marker_match = re.search(
        r"(?:_CA(?:V|OPC)?_|_CAV_|CONTRATO[^_]*_)(.+)$",
        stem,
        flags=re.IGNORECASE,
    )
    if marker_match:
        stem = marker_match.group(1)
    stem = re.sub(r"[_-]+", " ", stem)
    stem = FILENAME_NOISE_RE.sub(" ", stem)
    stem = re.sub(r"\b\d{1,4}\b", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" ,;:-_")

    candidates: list[str] = []
    for raw in re.split(r"\s+(?:e|&)\s+|[;|]+", stem, flags=re.IGNORECASE):
        candidate = re.sub(r"\s+", " ", raw).strip(" ,;:-_")
        if _valid_party_candidate("owner_name", candidate) and candidate not in candidates:
            candidates.append(candidate[:180])
    return candidates


def is_generic_property_name(value: str) -> bool:
    normalized = normalize_text(value)
    return normalized in GENERIC_PROPERTY_NAMES


def is_invalid_property_number(value: str, document_text: str = "") -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    normalized = normalize_text(raw)
    compact = compact_alphanumeric(raw)
    digits = re.sub(r"\D", "", raw)
    if normalized in {"NAO ESPECIFICADO", "NAO APLICAVEL", "N A", "N D"}:
        return True
    if compact in PLACEHOLDER_NUMBERS:
        return True
    if PROPERTY_NUMBER_FORBIDDEN_RE.search(raw):
        return True
    if re.fullmatch(r"[A-Z]{1,4}", compact):
        return True
    if len(digits) == 9 and _nine_digit_number_has_tax_context(raw, document_text):
        return True
    if re.search(r"[A-Z]", compact) and not re.fullmatch(r"(?:PR|VA)\d{1,4}", compact):
        return True
    return False


def _sanitize_party_fields(
    result: ExtractionResult,
    report: EntityResolutionReport,
) -> None:
    for field_name in ("lessor", "lessee", "owner_name"):
        value = str(getattr(result, field_name, "") or "").strip()
        if not value:
            continue
        if normalize_text(value) in GENERIC_PARTY_VALUES:
            _clear_field(result, report, field_name, "generic_party_label")


def _sanitize_property_name(
    result: ExtractionResult,
    document_text: str,
    report: EntityResolutionReport,
) -> None:
    current = str(result.property_name or "").strip()
    if current and is_generic_property_name(current):
        recovered = recover_property_name_from_text(document_text)
        if recovered:
            _set_field(result, report, "property_name", recovered, "property_name_context", "generic_property_name_replaced")
        else:
            _clear_field(result, report, "property_name", "generic_property_name")
        return
    if current and _clean_property_name(current) != current:
        cleaned_current = _clean_property_name(current)
        if cleaned_current:
            _set_field(
                result,
                report,
                "property_name",
                cleaned_current,
                "property_name_cleanup",
                "trimmed_generic_connector_or_location_tail",
            )
            current = cleaned_current
        else:
            _clear_field(result, report, "property_name", "invalid_property_name")
            current = ""
    recovered = recover_property_name_from_text(document_text)
    if not current and recovered:
        _set_field(result, report, "property_name", recovered, "property_name_context", "missing_property_name_recovered")


def recover_property_name_from_text(document_text: str) -> str:
    if not document_text.strip():
        return ""
    for pattern in PROPERTY_NAME_PATTERNS:
        for match in pattern.finditer(document_text):
            candidate = _clean_property_name(match.group("value"))
            if candidate:
                return candidate
    return ""


def _resolve_lessor_from_filename(
    result: ExtractionResult,
    document_text: str,
    report: EntityResolutionReport,
) -> None:
    if (result.document_category or "").strip().lower() != "lease_contract":
        return
    current = str(result.lessor or "").strip()
    if current and _valid_party_candidate("lessor", current):
        return
    if not report.filename_owner_candidates:
        return
    if not _has_lessor_role_markers(document_text):
        return
    value = "; ".join(report.filename_owner_candidates[:4])
    _set_field(
        result,
        report,
        "lessor",
        value,
        "filename_owner_candidates_confirmed_by_lessor_roles",
        "lessor_roles_present_but_ocr_name_unresolved",
    )


def _resolve_property_number(
    result: ExtractionResult,
    file_name: str,
    document_text: str,
    report: EntityResolutionReport,
) -> None:
    current = str(result.property_number or "").strip()
    if current and is_invalid_property_number(current, document_text):
        _clear_field(result, report, "property_number", "invalid_property_number")
        current = ""

    if not report.property_numbers:
        return

    resolved = "; ".join(report.property_numbers)
    if current == resolved:
        return
    if not current or len(report.property_numbers) == 1 or current == str(result.property_article or "").strip():
        _set_field(
            result,
            report,
            "property_number",
            resolved,
            "filename_property_code",
            f"property_code_from_filename:{';'.join(report.property_codes)}",
        )


def _resolve_contract_owner(
    result: ExtractionResult,
    report: EntityResolutionReport,
) -> None:
    if (result.document_category or "").strip().lower() != "lease_contract":
        return

    owner = str(result.owner_name or "").strip()
    lessor = str(result.lessor or "").strip()
    lessee = str(result.lessee or "").strip()

    if owner and lessee and normalize_text(owner) == normalize_text(lessee):
        if (
            _valid_party_candidate("lessor", lessor)
            and normalize_text(lessor) != normalize_text(lessee)
            and not _field_was_set_by(report, "lessor", "filename_owner_candidates_confirmed_by_lessor_roles")
        ):
            _set_field(
                result,
                report,
                "owner_name",
                lessor,
                "lessor_as_owner_candidate",
                "owner_matched_lessee",
            )
        else:
            _clear_field(result, report, "owner_name", "owner_matched_lessee")
        return


def _copy_property_location_to_name(
    result: ExtractionResult,
    report: EntityResolutionReport,
) -> None:
    if (result.document_category or "").strip().lower() != "property_document":
        return
    if str(result.property_name or "").strip():
        return
    location = str(result.property_location or "").strip()
    candidate = _clean_property_name(location)
    if candidate:
        _set_field(
            result,
            report,
            "property_name",
            candidate,
            "property_location_as_property_name",
            "property_document_location_used_as_business_property_name",
        )


def _clear_unsupported_values(
    result: ExtractionResult,
    document_text: str,
    report: EntityResolutionReport,
) -> None:
    if not document_text.strip():
        return
    for field_name in EVIDENCE_GATED_FIELDS:
        value = str(getattr(result, field_name, "") or "").strip()
        if not value:
            continue
        if field_name == "currency":
            if _currency_has_evidence(value, document_text):
                continue
        elif _value_has_evidence(value, field_name, document_text):
            continue
        _clear_field(result, report, field_name, "value_without_document_evidence")


def _clean_property_name(value: str) -> str:
    candidate = PROPERTY_NAME_STOP_RE.split(value, maxsplit=1)[0]
    candidate = candidate.split(",", 1)[0]
    candidate = re.sub(r"\s+", " ", candidate).strip(" ,;:-_\"'“”«»")
    candidate = re.sub(r"^(?:o|a|os|as)\s+", "", candidate, flags=re.IGNORECASE)
    candidate = _known_property_name(candidate) or candidate
    if not candidate or len(candidate) < 3 or len(candidate) > 80:
        return ""
    if is_generic_property_name(candidate):
        return ""
    if normalize_text(candidate) in PROPERTY_NAME_STOPWORDS:
        return ""
    if validate_name_field("owner_name", candidate) == [] and normalize_text(candidate).startswith("PROPRIEDADE DE "):
        return ""
    if not re.fullmatch(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9][A-Za-zÀ-ÖØ-öø-ÿ0-9' ./-]*", candidate):
        return ""
    return candidate


def _valid_party_candidate(field_name: str, value: str) -> bool:
    candidate = str(value or "").strip()
    if not candidate:
        return False
    if normalize_text(candidate) in GENERIC_PARTY_VALUES:
        return False
    return not validate_name_field(field_name, candidate)


def _has_lessor_role_markers(document_text: str) -> bool:
    normalized = normalize_text(document_text)
    if not normalized:
        return False
    role_hits = len(re.findall(r"\bSENHORIO(?:S|\s+\d+)?\b", normalized))
    if role_hits >= 2:
        return True
    return bool(re.search(r"\b(?:PRIMEIR[OA] OUTORGANTE|LOCADOR(?:ES)?)\b", normalized))


def _known_property_name(value: str) -> str:
    compact = re.sub(r"[^A-Z0-9]", "", normalize_text(value))
    for known, canonical in KNOWN_PROPERTY_NAMES.items():
        if compact == known:
            return canonical
        if _levenshtein(compact, known) <= 2:
            return canonical
    return ""


def _value_has_evidence(value: str, field_name: str, document_text: str) -> bool:
    normalized_value = normalize_text(value)
    normalized_document = normalize_text(document_text)
    if not normalized_value or not normalized_document:
        return False
    parts = [part.strip() for part in str(value).split(";") if part.strip()]
    if len(parts) > 1:
        return all(_value_has_evidence(part, field_name, document_text) for part in parts)
    if normalized_value in normalized_document:
        return True

    compact_value = compact_alphanumeric(value)
    compact_document = compact_alphanumeric(document_text)
    if compact_value and compact_value in compact_document:
        return True

    if field_name in {
        "property_name",
        "property_parish",
        "property_municipality",
        "property_district",
        "property_location",
    } and _fuzzy_place_has_evidence(value, document_text):
        return True

    if field_name in {"monthly_rent", "option_price", "purchase_price", "assignment_price"}:
        return _money_has_evidence(value, document_text)
    return False


def _fuzzy_place_has_evidence(value: str, document_text: str) -> bool:
    normalized_value = _normalize_words_for_fuzzy(value)
    value_tokens = normalized_value.split()
    if not value_tokens:
        return False
    document_tokens = _normalize_words_for_fuzzy(document_text).split()
    size = len(value_tokens)
    best = 999
    for index in range(0, max(0, len(document_tokens) - size + 1)):
        candidate = " ".join(document_tokens[index:index + size])
        best = min(best, _levenshtein(candidate, normalized_value))
        if best <= max(1, min(3, int(len(normalized_value) * 0.18))):
            return True
    return False


def _normalize_words_for_fuzzy(value: str) -> str:
    normalized = normalize_text(value)
    normalized = re.sub(r"[^A-Z0-9 ]", " ", normalized)
    return " ".join(normalized.split())


def _money_has_evidence(value: str, document_text: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if not digits:
        return False
    document_digits = re.sub(r"\D", "", document_text)
    if digits in document_digits:
        return True
    if digits.endswith("00") and digits[:-2] and digits[:-2] in document_digits:
        return True
    return False


def _currency_has_evidence(value: str, document_text: str) -> bool:
    normalized_currency = normalize_text(value)
    aliases = CURRENCY_ALIASES.get(normalized_currency, (normalized_currency,))
    normalized_document = normalize_text(document_text)
    return any(normalize_text(alias) in normalized_document for alias in aliases if alias)


def _nine_digit_number_has_tax_context(value: str, document_text: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if not digits:
        return False
    if re.search(r"\b(?:NIF|NIPC|CONTRIBUINTE)\D{0,30}" + re.escape(digits), document_text, re.IGNORECASE):
        return True
    if re.search(re.escape(digits) + r"\D{0,30}\b(?:NIF|NIPC|CONTRIBUINTE)\b", document_text, re.IGNORECASE):
        return True
    return True


def _number_from_code(code: str) -> str:
    match = re.search(r"\d{1,4}$", code)
    if not match:
        return ""
    number = match.group(0).lstrip("0")
    return number or "0"


def _field_was_set_by(
    report: EntityResolutionReport,
    field_name: str,
    method: str,
) -> bool:
    return any(
        change.field == field_name and change.method == method
        for change in report.changes
    )


def _levenshtein(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for index, left_char in enumerate(left, start=1):
        current = [index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    previous[right_index] + 1,
                    current[right_index - 1] + 1,
                    previous[right_index - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def _set_field(
    result: ExtractionResult,
    report: EntityResolutionReport,
    field_name: str,
    value: str,
    method: str,
    reason: str,
) -> None:
    cleaned = str(value or "").strip()
    old = str(getattr(result, field_name, "") or "").strip()
    if old == cleaned:
        return
    setattr(result, field_name, cleaned)
    report.changes.append(
        EntityResolutionChange(
            field=field_name,
            old_value=old,
            new_value=cleaned,
            method=method,
            reason=reason,
        )
    )


def _clear_field(
    result: ExtractionResult,
    report: EntityResolutionReport,
    field_name: str,
    reason: str,
) -> None:
    old = str(getattr(result, field_name, "") or "").strip()
    if not old:
        return
    setattr(result, field_name, "")
    report.blocked_values.append(
        {
            "field": field_name,
            "blocked_value": old,
            "reason": reason,
        }
    )
    report.changes.append(
        EntityResolutionChange(
            field=field_name,
            old_value=old,
            new_value="",
            method="blocked_suspicious_value",
            reason=reason,
        )
    )


def _attach_report(result: ExtractionResult, report: EntityResolutionReport) -> None:
    raw = result.raw_json if isinstance(result.raw_json, dict) else {}
    result.raw_json = dict(raw)
    result.raw_json["step2_6_entity_resolution"] = report.to_dict()
    if report.changes:
        fields = ", ".join(change.field for change in report.changes)
        note = f"Step 2.6 entity resolution: {fields}"
        result.extraction_notes = " | ".join(
            part for part in (result.extraction_notes, note) if part
        )


__all__ = [
    "EntityResolutionChange",
    "EntityResolutionReport",
    "apply_entity_resolution",
    "extract_property_codes",
    "is_generic_property_name",
    "is_invalid_property_number",
    "owner_candidates_from_filename",
    "recover_property_name_from_text",
]
