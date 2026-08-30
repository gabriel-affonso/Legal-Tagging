"""Deterministic annex recovery and reconciliation for lease properties."""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
import unicodedata
from typing import Any

from .property_pipeline import (
    PROPERTY_SCHEMA,
    PropertyExtraction,
    is_valid_matrix_article,
    is_valid_matrix_section,
)


CADENETA_THRESHOLD = 60
CADENETA_CONTEXT_PAGES = 4


@dataclass(frozen=True)
class AnnexPage:
    page_number: int
    text: str
    caderneta_score: int


@dataclass(frozen=True)
class CadernetaValues:
    property_name: str = ""
    matrix_article: str = ""
    matrix_section: str = ""
    area_m2: int | float | None = None
    owner_name: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "property_name": self.property_name or None,
            "matrix_article": self.matrix_article or None,
            "matrix_section": self.matrix_section or None,
            "area_m2": self.area_m2,
            "owner_name": self.owner_name or None,
        }

    def validated(self) -> "CadernetaValues":
        property_name = self.property_name if len(self.property_name) > 2 else ""
        article = self.matrix_article.upper().replace(" ", "")
        section = self.matrix_section.upper().replace(" ", "")
        return CadernetaValues(
            property_name=property_name,
            matrix_article=article if _valid_article(article) else "",
            matrix_section=section if _valid_section(section) else "",
            area_m2=self.area_m2 if self.area_m2 and self.area_m2 > 0 else None,
            owner_name=_clean_owner_name(self.owner_name),
        )


def recovery_required(result: PropertyExtraction) -> bool:
    return (
        result.confidence < 80
        or not result.property_name
        or not result.matrix_article
        or not result.matrix_section
        or result.area_m2 is None
        or not result.owner_name
        or not _valid_article(result.matrix_article)
        or not _valid_section(result.matrix_section)
    )


def discover_caderneta_pages(text: str) -> list[AnnexPage]:
    """Return all identified caderneta pages for legacy single-pack callers."""
    return [page for group in discover_caderneta_groups(text) for page in group]


def discover_caderneta_groups(text: str) -> list[list[AnnexPage]]:
    """Identify each internal caderneta as a separate page group.

    A contract may contain several property packs.  Returning independent
    groups prevents fields from different cadernetas being merged into a
    single property decision.
    """
    pages = _split_pages(text)
    if not pages:
        return []
    scored_pages = [
        AnnexPage(page_number=page_number, text=page_text, caderneta_score=_caderneta_score(page_text))
        for page_number, page_text in pages
    ]
    title_indexes = sorted(
        index
        for index, page in enumerate(scored_pages)
        if _has_caderneta_title(page.text)
    )
    strong_indexes = sorted(
        index
        for index, page in enumerate(scored_pages)
        if page.caderneta_score >= CADENETA_THRESHOLD
    )
    anchors = title_indexes or _contiguous_group_starts(strong_indexes)
    groups: list[list[AnnexPage]] = []
    for position, start in enumerate(anchors):
        stop = anchors[position + 1] if position + 1 < len(anchors) else len(scored_pages)
        included = set(range(start, min(stop, start + CADENETA_CONTEXT_PAGES)))
        included.update(index for index in strong_indexes if start <= index < stop)
        group = [page for index, page in enumerate(scored_pages) if index in included]
        if group:
            groups.append(group)
    return groups


def _contiguous_group_starts(indexes: list[int]) -> list[int]:
    starts: list[int] = []
    previous: int | None = None
    for index in indexes:
        if previous is None or index > previous + 1:
            starts.append(index)
        previous = index
    return starts


def parse_caderneta(pages: list[AnnexPage]) -> CadernetaValues:
    text = "\n".join(page.text for page in pages)
    return CadernetaValues(
        property_name=_label_value(
            text,
            r"NOME\s*/?\s*LOCALIZACAO(?:\s+DO)?\s+PREDIO",
            r"ELEMENTOS\s+DO\s+PREDIO|ARTIGO\s+MATRICIAL|MATRIZ\s+PREDIAL|SECCAO|TITULARES",
        ),
        matrix_article=_label_value(
            text,
            r"ARTIGO\s+MATRICIAL(?:\s+N[Oº.]*)?",
            r"NOME\s*/?\s*LOCALIZACAO|ELEMENTOS\s+DO\s+PREDIO|SECCAO|TITULARES|AREA",
        ),
        matrix_section=_label_value(
            text,
            r"SECCAO",
            r"ARTIGO\s+MATRICIAL|NOME\s*/?\s*LOCALIZACAO|ELEMENTOS\s+DO\s+PREDIO|TITULARES|AREA",
        ).upper(),
        area_m2=_caderneta_area(text),
        owner_name=_caderneta_owner(text),
    ).validated()


def reconcile_property(
    contract: PropertyExtraction,
    caderneta: CadernetaValues,
    pages: list[AnnexPage],
) -> PropertyExtraction:
    """Consolidate contract context and caderneta evidence without guessing."""
    contract_values = _values_from_result(contract)
    caderneta_values = caderneta.as_dict()
    prior_validation = list(contract.audit.get("validation_results", []))
    validation: list[str] = []
    recovered_fields: list[str] = []
    evidence: dict[str, dict[str, Any]] = {}
    final = dict(contract_values)
    agreements = 0

    for field in PROPERTY_SCHEMA:
        contract_value = contract_values[field]
        caderneta_value = caderneta_values[field]
        if contract_value not in ("", None) and caderneta_value not in ("", None):
            if _same_value(contract_value, caderneta_value):
                agreements += 1
                evidence[field] = {
                    "value": caderneta_value,
                    "source": "contract_and_caderneta",
                    "confidence": 99,
                }
                final[field] = caderneta_value
            elif field in {"property_name", "area_m2"}:
                # Caderneta values are structured and authoritative for these fields.
                final[field] = caderneta_value
                evidence[field] = {
                    "value": caderneta_value,
                    "source": "caderneta_predial",
                    "confidence": 99,
                }
                recovered_fields.append(field)
                validation.append(f"{field}_contract_caderneta_mismatch")
            else:
                # Preserve the contract value, but do not claim reconciliation.
                evidence[field] = {
                    "value": contract_value,
                    "source": "contract_clause",
                    "confidence": 60,
                }
                validation.append(f"{field}_contract_caderneta_mismatch")
        elif caderneta_value not in ("", None):
            final[field] = caderneta_value
            recovered_fields.append(field)
            evidence[field] = {
                "value": caderneta_value,
                "source": "caderneta_predial",
                "confidence": 99,
            }
        elif contract_value not in ("", None):
            evidence[field] = {
                "value": contract_value,
                "source": "contract_clause",
                "confidence": 85,
            }
        else:
            evidence[field] = {"value": None, "source": None, "confidence": 0}

    owner_name = caderneta.owner_name
    if owner_name:
        evidence["owner_name"] = {
            "value": owner_name,
            "source": "caderneta_predial",
            "confidence": 99,
        }
        recovered_fields.append("owner_name")
    else:
        evidence["owner_name"] = {"value": None, "source": None, "confidence": 0}

    confidence_before = contract.confidence
    confidence_after = (
        _confidence_v3(final, has_agreement=agreements > 0)
        if recovered_fields or agreements
        else contract.confidence
    )
    audit = dict(contract.audit)
    audit.update({
        "contract_values": contract_values,
        "caderneta_values": caderneta_values,
        "caderneta_pages": [page.page_number for page in pages],
        "recovered_fields": recovered_fields,
        "validation_results": _unique_results(
            prior_validation + validation + ([] if prior_validation or validation else ["contract_and_caderneta_consistent"])
        ),
        "confidence_before_recovery": confidence_before,
        "confidence_after_recovery": confidence_after,
    })
    return replace(
        contract,
        status="needs_review" if validation else contract.status,
        reason="contract_caderneta_mismatch" if validation else contract.reason,
        property_name=str(final["property_name"] or ""),
        matrix_article=str(final["matrix_article"] or ""),
        matrix_section=str(final["matrix_section"] or ""),
        area_m2=final["area_m2"],
        owner_name=owner_name,
        confidence=confidence_after,
        candidate_score=max(contract.candidate_score, max((page.caderneta_score for page in pages), default=0)),
        evidence_model=evidence,
        audit=audit,
    )


def recover_from_annexes(contract: PropertyExtraction, annex_text: str) -> PropertyExtraction:
    pages = discover_caderneta_pages(annex_text)
    if not pages:
        audit = dict(contract.audit)
        prior_validation = list(audit.get("validation_results", []))
        audit.update({
            "contract_values": _values_from_result(contract),
            "caderneta_values": {},
            "caderneta_pages": [],
            "recovered_fields": [],
            "validation_results": _unique_results(prior_validation + ["caderneta_not_found"]),
            "confidence_before_recovery": contract.confidence,
            "confidence_after_recovery": contract.confidence,
        })
        return replace(contract, confidence=audit["confidence_after_recovery"], audit=audit)
    return reconcile_property(contract, parse_caderneta(pages), pages)


def _split_pages(text: str) -> list[tuple[int, str]]:
    matches = list(re.finditer(r"\[Page\s+(\d+)\]", text, re.IGNORECASE))
    if not matches:
        return [(1, text)] if text.strip() else []
    pages: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        page_text = text[match.end():end].strip()
        if page_text:
            pages.append((int(match.group(1)), page_text))
    return pages


def _caderneta_score(text: str) -> int:
    normalized = _fold(text)
    indicators = (
        (r"\bactualizacao\s+(?:de\s+)?caderneta\s+predial\s+rustica\b", 70),
        (r"\bcaderneta\s+predial\s+rustica\b", 45),
        (r"\bmodelo\s*[-:]?\s*[AB]\b", 30),
        (r"\bcaderneta\s+predial\b", 30),
        (r"\bidentificacao\s+do\s+predio\b", 20),
        (r"\bartigo\s+matricial\b", 20),
        (r"\bseccao\b", 10),
        (r"\belementos\s+do\s+predio\b", 10),
        (r"\btitulares\b", 10),
    )
    return sum(weight for pattern, weight in indicators if re.search(pattern, normalized, re.IGNORECASE))


def _has_caderneta_title(text: str) -> bool:
    normalized = _fold(text)
    return bool(re.search(
        r"\b(?:actualizacao\s+(?:de\s+)?)?caderneta\s+predial\s+rustica\b",
        normalized,
        re.IGNORECASE,
    )) and bool(re.search(r"\bmodelo\s*[-:]?\s*[AB]\b", normalized, re.IGNORECASE))


def _label_value(text: str, label: str, stop: str) -> str:
    normalized = _fold(text)
    match = re.search(
        rf"{label}\s*[:\-]?\s*(.+?)(?={stop}|$)",
        normalized,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    # Accent folding preserves one character per source character, so these
    # offsets retain the original spelling/capitalization in the output.
    value = re.sub(r"\s+", " ", text[match.start(1):match.end(1)]).strip(" .,:;-\n")
    return value[:160]


def _caderneta_area(text: str) -> int | float | None:
    normalized = _fold(text)
    hectare = re.search(r"area\s+total\s*\(\s*ha\s*\)\s*[:\-]?\s*([\d.,]+)", normalized, re.IGNORECASE)
    if hectare:
        number = _decimal_number(hectare.group(1))
        if number is not None:
            converted = number * 10_000
            return int(converted) if converted.is_integer() else converted
    square_meters = re.search(r"area(?:\s+total)?\s*(?:m2|m²)\s*[:\-]?\s*([\d.,]+)", normalized, re.IGNORECASE)
    if square_meters:
        return _decimal_number(square_meters.group(1))
    return None


def _caderneta_owner(text: str) -> str:
    normalized = _fold(text)
    holder = re.search(r"\btitulares?\b(?P<value>.*?)(?=\b(?:elementos\s+para\s+a\s+validacao|emitido\s+via|codigo\s+de\s+validacao)\b|$)", normalized, re.IGNORECASE | re.DOTALL)
    scope = text[holder.start("value"):holder.end("value")] if holder else text
    for pattern in (
        r"\bnome\s*[:\-]\s*(.+?)(?=\b(?:morada|tipo\s+de\s+titular|parte|documento|entidade)\b|$)",
        r"\b(?:titular|propriet[aá]rio|sujeito\s+passivo)\s*[:\-]\s*(.+?)(?=\b(?:morada|tipo\s+de\s+titular|parte|documento|entidade)\b|$)",
    ):
        match = re.search(pattern, scope, re.IGNORECASE | re.DOTALL)
        if match:
            value = re.sub(r"\s+", " ", match.group(1)).strip(" .,:;-\n")
            cleaned = _clean_owner_name(value)
            if cleaned:
                return cleaned
    return ""


def _clean_owner_name(value: str) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip(" .,:;-\n")
    if len(value) < 4:
        return ""
    normalized = _fold(value)
    if normalized in {"NOME", "TITULAR", "TITULARES", "PROPRIETARIO", "PROPRIETARIOS"}:
        return ""
    if re.search(r"\b(?:morada|tipo\s+de\s+titular|parte|documento|entidade)\b", normalized, re.IGNORECASE):
        return ""
    return value[:160]


def _decimal_number(value: str) -> float | None:
    raw = value.replace(" ", "")
    if not re.fullmatch(r"\d+(?:[.,]\d+)?", raw):
        return None
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return None


def _values_from_result(result: PropertyExtraction) -> dict[str, Any]:
    return {
        "property_name": result.property_name,
        "matrix_article": result.matrix_article,
        "matrix_section": result.matrix_section,
        "area_m2": result.area_m2,
    }


def _confidence_v3(values: dict[str, Any], *, has_agreement: bool) -> int:
    weights = {"property_name": 30, "matrix_article": 30, "matrix_section": 20, "area_m2": 20}
    score = sum(weight for field, weight in weights.items() if values[field] not in ("", None))
    return min(100, score + (10 if has_agreement else 0))


def _same_value(first: Any, second: Any) -> bool:
    if isinstance(first, str) or isinstance(second, str):
        return _fold(str(first)).replace(" ", "") == _fold(str(second)).replace(" ", "")
    return first == second


def _unique_results(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _valid_article(value: str) -> bool:
    return is_valid_matrix_article(value)


def _valid_section(value: str) -> bool:
    return is_valid_matrix_section(value)


def _fold(value: str) -> str:
    return "".join(
        character for character in unicodedata.normalize("NFD", value).upper()
        if unicodedata.category(character) != "Mn"
    )


__all__ = [
    "AnnexPage",
    "CadernetaValues",
    "discover_caderneta_pages",
    "discover_caderneta_groups",
    "parse_caderneta",
    "recover_from_annexes",
    "recovery_required",
]
