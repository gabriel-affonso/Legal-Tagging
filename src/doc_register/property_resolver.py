"""Cadastral-first property extraction for Step 3.3."""
from __future__ import annotations

from dataclasses import dataclass
import re

from .document_structure import ANNEX_CADERNETA, ANNEX_CRP, PROPERTY_SECTION, DocumentStructure
from .property_intelligence import assess_cadastral_evidence, discover_caderneta_groups, matrix_key
from .source_ranking import RankedValue, source_score


PROPERTY_FIELDS = ("property_name", "property_article", "property_section", "property_parish", "property_total_area", "owner_name", "owner_tax_id")


def extract_property_candidates(structure: DocumentStructure, document_text: str) -> list[RankedValue]:
    """Return only field-specific candidates, with caderneta values first."""
    candidates: list[RankedValue] = []
    groups = discover_caderneta_groups(document_text)
    selected_groups = _select_caderneta_groups(groups, structure)
    for pages in selected_groups:
        evidence = assess_cadastral_evidence(pages)
        values = evidence.values
        annex_text = "\n".join(page.text for page in pages)
        labelled_annex = _labelled_values(annex_text)
        raw = {
            "property_name": values.property_name,
            "property_article": values.matrix_article,
            "property_section": values.matrix_section,
            "property_total_area": f"{values.area_m2 / 10000:g} hectares" if values.area_m2 is not None else "",
            "owner_name": values.owner_name if evidence.owner_is_verified else "",
            "owner_tax_id": _owner_tax_id(annex_text) if evidence.owner_is_verified else "",
        }
        # A labelled location is narrower than the broad caderneta parser and
        # must win when the parser also swallowed the parish line.
        if labelled_annex.get("property_name"):
            raw["property_name"] = labelled_annex["property_name"]
        for field, value in raw.items():
            if value:
                candidates.append(RankedValue(
                    field, value, "CADERNETA", source_score(field, "CADERNETA"),
                    valid=evidence.is_structurally_valid,
                ))
        for field, value in labelled_annex.items():
            if value:
                candidates.append(RankedValue(
                    field, value, "CADERNETA", source_score(field, "CADERNETA"),
                    valid=evidence.is_structurally_valid,
                ))

    # A contract may intentionally lease several properties.  The flat
    # register columns cannot safely hold three article/section/name tuples,
    # but facts common to every verified caderneta (notably the title holder)
    # are still deterministic and may be published.
    if groups and not selected_groups:
        verified = [
            (pages, assess_cadastral_evidence(pages))
            for pages in groups
            if assess_cadastral_evidence(pages).is_structurally_valid
        ]
        if len(verified) > 1:
            owner_names = {
                evidence.values.owner_name.strip()
                for _, evidence in verified
                if evidence.owner_is_verified and evidence.values.owner_name.strip()
            }
            owner_tax_ids = {
                _owner_tax_id("\n".join(page.text for page in pages))
                for pages, evidence in verified
                if evidence.owner_is_verified
            } - {""}
            if len(owner_names) == 1:
                value = next(iter(owner_names))
                candidates.append(RankedValue("owner_name", value, "CADERNETA", source_score("owner_name", "CADERNETA")))
            if len(owner_tax_ids) == 1:
                value = next(iter(owner_tax_ids))
                candidates.append(RankedValue("owner_tax_id", value, "CADERNETA", source_score("owner_tax_id", "CADERNETA")))

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
        "property_name": _capture(text, r"\b(?:nome\s*/?\s*)?localiza[cç][aã]o(?:\s+do)?\s+pr[eé]dio[ \t]*[\[\]|:;#-]*[ \t]*(?:\n[ \t]*)?([^\n,;.\[\]]{3,100})"),
        "property_parish": _capture(text, r"\bfreguesia\s*(?:de\s+)?[:#-]?\s*(?:\d{1,3}\s*-\s*)?([^,;\n.]{3,80})"),
        "owner_name": _capture(text, r"(?m)^[ \t]*(?:nome|titular|propriet[aá]rio)[ \t]*[:#-][ \t]*([^\n,;.]{8,180})"),
        "owner_tax_id": _capture(text, r"\b(?:NIF|NIPC|identifica[cç][aã]o\s+fiscal)\s*(?:n[.ºo°]?\s*)?[:#-]?\s*(\d{9})"),
    }
    return {key: value.strip(" ,;:-.\"'") for key, value in values.items()}


def _capture(text: str, pattern: str) -> str:
    match = re.search(pattern, text, re.I)
    return match.group(1).strip() if match else ""


def _owner_tax_id(text: str) -> str:
    holder_match = re.search(r"\btitulares?\b(?P<block>.{0,1200})", text, re.I | re.S)
    scope = holder_match.group("block") if holder_match else text
    match = re.search(
        r"\b(?:NIF|NIPC|identifica[cç][aã]o\s+fiscal)\s*(?:n[.ºo°]?\s*)?[:#-]?\s*(\d{9})\b",
        scope,
        re.I,
    )
    return match.group(1) if match else ""


def _select_caderneta_groups(groups, structure: DocumentStructure):
    """Use one caderneta only when it can be tied to the contract identity.

    A merged document often carries unrelated cadernetas.  Publishing values
    from all of them was the direct cause of article/section conflicts in the
    Step 3.2 run.
    """
    if len(groups) <= 1:
        return groups
    verified = [
        (pages, assess_cadastral_evidence(pages))
        for pages in groups
        if assess_cadastral_evidence(pages).is_structurally_valid
    ]
    if len(verified) <= 1:
        return [pages for pages, _ in verified]
    contract_text = "\n".join(zone.text for zone in structure.of_type(PROPERTY_SECTION))
    contract_keys = _contract_matrix_keys(contract_text)
    if not contract_keys:
        contract_values = _labelled_values(contract_text)
        key = matrix_key(contract_values.get("property_article", ""), contract_values.get("property_section", ""))
        contract_keys = {key} if key else set()
    if not contract_keys:
        return []
    matching = [pages for pages, evidence in verified if evidence.matrix_key in contract_keys]
    return matching if len(matching) == 1 else []


def cadastral_property_groups(structure: DocumentStructure, document_text: str) -> list[dict[str, object]]:
    """Return aligned multi-property facts for audit and structured consumers."""
    contract_text = "\n".join(zone.text for zone in structure.of_type(PROPERTY_SECTION))
    contract_keys = _contract_matrix_keys(contract_text)
    groups: list[dict[str, object]] = []
    for pages in discover_caderneta_groups(document_text):
        evidence = assess_cadastral_evidence(pages)
        if not evidence.is_structurally_valid:
            continue
        values = evidence.values
        text = "\n".join(page.text for page in pages)
        groups.append({
            "matrix_key": evidence.matrix_key,
            "property_name": values.property_name,
            "property_article": values.matrix_article,
            "property_section": values.matrix_section,
            "property_total_area": f"{values.area_m2 / 10000:g} hectares" if values.area_m2 is not None else "",
            "owner_name": values.owner_name if evidence.owner_is_verified else "",
            "owner_tax_id": _owner_tax_id(text) if evidence.owner_is_verified else "",
            "pages": [page.page_number for page in pages],
            "matched_contract_identity": evidence.matrix_key in contract_keys,
        })
    return groups


def _contract_matrix_keys(text: str) -> set[str]:
    keys: set[str] = set()
    for match in re.finditer(
        r"\bartigo(?:\s+matricial)?\s+(\d{1,8})(?:\s*\([^)]*\))?.{0,90}?\bsec(?:c|ç)[aã]o\s+([A-Z]{1,3})\b",
        text,
        re.I | re.S,
    ):
        key = matrix_key(match.group(1), match.group(2))
        if key:
            keys.add(key)
    return keys


__all__ = ["PROPERTY_FIELDS", "extract_property_candidates", "cadastral_property_groups"]
