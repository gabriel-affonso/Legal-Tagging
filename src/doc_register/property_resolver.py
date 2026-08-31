"""Cadastral-first property extraction for Step 3.3."""
from __future__ import annotations

from dataclasses import dataclass
import re

from .document_structure import ANNEX_CADERNETA, ANNEX_CRP, PROPERTY_SECTION, DocumentStructure
from .property_intelligence import assess_cadastral_evidence, discover_caderneta_groups
from .source_ranking import RankedValue, source_score


PROPERTY_FIELDS = ("property_name", "property_article", "property_section", "property_parish", "property_total_area", "owner_name", "owner_tax_id")


def extract_property_candidates(structure: DocumentStructure, document_text: str) -> list[RankedValue]:
    """Return only field-specific candidates, with caderneta values first."""
    candidates: list[RankedValue] = []
    for pages in discover_caderneta_groups(document_text):
        evidence = assess_cadastral_evidence(pages)
        values = evidence.values
        raw = {
            "property_name": values.property_name,
            "property_article": values.matrix_article,
            "property_section": values.matrix_section,
            "property_total_area": f"{values.area_m2 / 10000:g} hectares" if values.area_m2 is not None else "",
            "owner_name": values.owner_name if evidence.owner_is_verified else "",
            "owner_tax_id": _owner_tax_id(annex_text := "\n".join(page.text for page in pages)) if evidence.owner_is_verified else "",
        }
        for field, value in raw.items():
            if value:
                candidates.append(RankedValue(
                    field, value, "CADERNETA", source_score(field, "CADERNETA"),
                    valid=evidence.is_structurally_valid,
                ))
        for field, value in _labelled_values(annex_text).items():
            if value:
                candidates.append(RankedValue(
                    field, value, "CADERNETA", source_score(field, "CADERNETA"),
                    valid=evidence.is_structurally_valid,
                ))

    for source, zones in (("CRP", structure.of_type(ANNEX_CRP)), ("CONTRACT", structure.of_type(PROPERTY_SECTION))):
        for zone in zones:
            for field, value in _labelled_values(zone.text).items():
                if value:
                    candidates.append(RankedValue(field, value, source, source_score(field, source), valid=True))
    return candidates


def _labelled_values(text: str) -> dict[str, str]:
    values = {
        "property_article": _capture(text, r"\bartigo\s+matricial(?:\s+n[.ºo°]+)?\s*[:#-]?\s*(\d{1,8})"),
        "property_section": _capture(text, r"\bsec(?:c|ç)[aã]o\s*[:#-]?\s*([A-Z]{1,3})").upper(),
        "property_name": _capture(text, r"\b(?:nome\s*/?\s*)?localiza[cç][aã]o(?:\s+do)?\s+pr[eé]dio\s*[:#-]?\s*([^\n,;.]{3,100})"),
        "property_parish": _capture(text, r"\bfreguesia\s+(?:de\s+)?([^,;\n.]{3,80})"),
    }
    return {key: value.strip(" ,;:-.\"'") for key, value in values.items()}


def _capture(text: str, pattern: str) -> str:
    match = re.search(pattern, text, re.I)
    return match.group(1).strip() if match else ""


def _owner_tax_id(text: str) -> str:
    holder_match = re.search(r"\btitulares?\b(?P<block>.{0,1000})", text, re.I | re.S)
    if not holder_match:
        return ""
    match = re.search(r"\b(?:NIF|NIPC)\s*(?:n[.ºo°]?\s*)?[:#-]?\s*(\d{9})\b", holder_match.group("block"), re.I)
    return match.group(1) if match else ""


__all__ = ["PROPERTY_FIELDS", "extract_property_candidates"]
