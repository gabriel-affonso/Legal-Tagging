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
        r"(?:denominad[oa]|designad[oa])\s+[\"'“”«»]?(?P<value>[^\"'“”«»\n.;]{2,120})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:denominad[oa]|designad[oa]|conhecid[oa]\s+por)\s+"
        r"[\"'“”«»]?(?P<value>[^\"'“”«»\n.;]{2,120})",
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
}
PLACEHOLDER_NUMBERS = {"0", "00", "000", "123", "1234", "12345", "123456"}
PROPERTY_NUMBER_FORBIDDEN_RE = re.compile(
    r"\b(?:artigo|art\.|anexo|nif|nipc|nao\s+especificado|não\s+especificado|"
    r"decreto|cl[aá]usula|codigo|c[oó]digo)\b|%",
    re.IGNORECASE,
)


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
    _resolve_contract_owner(result, report)

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
    recovered = recover_property_name_from_text(document_text)
    if current and is_generic_property_name(current):
        if recovered:
            _set_field(result, report, "property_name", recovered, "property_name_context", "generic_property_name_replaced")
        else:
            _clear_field(result, report, "property_name", "generic_property_name")
        return
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
        if _valid_party_candidate("lessor", lessor) and normalize_text(lessor) != normalize_text(lessee):
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

    if not owner and _valid_party_candidate("lessor", lessor):
        _set_field(
            result,
            report,
            "owner_name",
            lessor,
            "lessor_as_owner_candidate",
            "lease_lessor_is_best_available_owner_candidate",
        )


def _clean_property_name(value: str) -> str:
    candidate = PROPERTY_NAME_STOP_RE.split(value, maxsplit=1)[0]
    candidate = candidate.split(",", 1)[0]
    candidate = re.sub(r"\s+", " ", candidate).strip(" ,;:-_\"'“”«»")
    candidate = re.sub(r"^(?:o|a|os|as)\s+", "", candidate, flags=re.IGNORECASE)
    if not candidate or len(candidate) < 3 or len(candidate) > 80:
        return ""
    if is_generic_property_name(candidate):
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
