"""Step 3.3 field-centric extraction engine.

It is deliberately a final publisher: upstream LLM output is input evidence,
never an authority that can overwrite a better documentary source.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Iterable

from .consensus_engine import FieldEvidence, resolve_consensus
from .document_structure import (
    ANNEX_CADERNETA, ANNEX_CRP, ANNEX_IDENTIFICATION, FINANCIAL_SECTION,
    NOTARIZATION_SECTION, PARTY_SECTION, PROPERTY_SECTION, SIGNATURE_SECTION,
    DocumentStructure, detect_document_structure,
)
from .entity_validator import validate_entity
from .models import ExtractionResult
from .ownership_validators import validate_ownership
from .party_extractor import PartyExtractionReport, extract_parties
from .property_resolver import extract_property_candidates
from .source_ranking import RankedValue, source_score


TRACKED_FIELDS = (
    "lessor", "lessee", "lessee_tax_id", "owner_name", "owner_tax_id",
    "property_name", "property_article", "property_section", "property_parish",
    "property_total_area", "rent_amount", "rent_currency", "monthly_rent",
    "annual_rent", "rent_frequency", "rent_unit", "rent_basis",
    "contract_start_date", "contract_end_date", "rent_payment_day",
    "option_price", "purchase_price", "assignment_price", "signed_date",
)


@dataclass
class Step33Report:
    active: bool = False
    structure: DocumentStructure = field(default_factory=DocumentStructure)
    parties: PartyExtractionReport = field(default_factory=PartyExtractionReport)
    candidates: dict[str, list[RankedValue]] = field(default_factory=dict)
    field_evidence: dict[str, FieldEvidence] = field(default_factory=dict)
    blocked: list[dict[str, str]] = field(default_factory=list)
    ownership_issues: list[str] = field(default_factory=list)

    def context_for_llm(self, *, max_chars: int) -> str:
        """Give the LLM only field-authorized zones, labelled by use."""
        property_zones = (ANNEX_CADERNETA, ANNEX_CRP)
        if not self.structure.of_type(*property_zones):
            property_zones = (PROPERTY_SECTION,)
        groups = (
            ("PARTIES — ENTITIES ONLY", (PARTY_SECTION, ANNEX_IDENTIFICATION, NOTARIZATION_SECTION, SIGNATURE_SECTION)),
            ("PROPERTY — CADASTRAL / CRP ONLY", property_zones),
            ("FINANCIAL — CONTRACT CLAUSES ONLY", (FINANCIAL_SECTION,)),
            ("SIGNATURE — DATE ONLY", (SIGNATURE_SECTION, NOTARIZATION_SECTION)),
        )
        chunks: list[str] = []
        remaining = max_chars
        for label, zone_types in groups:
            context = self.structure.context(*zone_types, max_chars=remaining)
            if not context:
                continue
            rendered = f"[{label}]\n{context}"[:remaining].rstrip()
            chunks.append(rendered)
            remaining -= len(rendered) + 2
            if remaining <= 0:
                break
        return "\n\n".join(chunks)

    def to_dict(self) -> dict[str, object]:
        evidence = {
            field: self.field_evidence.get(field, FieldEvidence("", 0.0, "", 0)).to_dict()
            for field in TRACKED_FIELDS
        }
        return {
            "version": "3.3",
            "active": self.active,
            "document_structure": self.structure.to_dict(),
            "party_extraction": self.parties.to_dict(),
            "field_candidates": {field: [_candidate_dict(item) for item in values] for field, values in self.candidates.items()},
            "field_evidence": evidence,
            "blocked": self.blocked,
            "ownership_issues": self.ownership_issues,
        }


def prepare_field_centric_extraction(document_text: str) -> Step33Report:
    structure = detect_document_structure(document_text)
    active = bool(structure.zones) and bool(re.search(r"\b(?:contrato\s+de\s+arrendamento|arrendat[aá]ri[oa]|senhorios?)\b", document_text or "", re.I))
    report = Step33Report(active=active, structure=structure)
    if not active:
        return report
    report.parties = extract_parties(structure)
    for party in report.parties.roles:
        field = "lessor" if party.role == "lessor" else "lessee"
        _add(report, RankedValue(field, party.value, party.source, party.source_score, party.valid))
    for candidate in extract_property_candidates(structure, document_text):
        _add(report, candidate)
    _extract_contract_values(report)
    _extract_signed_date(report)
    return report


def apply_field_centric_extraction(result: ExtractionResult, report: Step33Report) -> ExtractionResult:
    if not report.active or (result.document_category or "").lower() != "lease_contract":
        _attach(result, report)
        return result

    # Publish evidence-backed deterministic values.  Fields with no Step 3.3
    # candidate retain the Step 3.2 result, except ownership which must never
    # be guessed from a contract party or an LLM answer.
    for field, candidates in report.candidates.items():
        consensus = _resolve_field(field, candidates)
        if not consensus:
            continue
        if field in {"lessor", "lessee"} and not validate_entity(consensus.value)[0]:
            report.blocked.append({"field": field, "value": consensus.value, "reason": "entity_validation"})
            continue
        setattr(result, field, consensus.value)
        report.field_evidence[field] = consensus

    owner_sources = {item.source for item in report.candidates.get("owner_name", []) if item.valid}
    report.ownership_issues = validate_ownership(
        owner_name=result.owner_name,
        owner_tax_id=result.owner_tax_id,
        lessee_name=result.lessee,
        lessee_tax_id=result.lessee_tax_id,
        evidence_sources=owner_sources,
    )
    # Owner publication needs actual caderneta/CRP evidence.  A contract says
    # who lets the asset; it does not, by itself, prove cadastral ownership.
    owner_evidence = report.field_evidence.get("owner_name")
    if not owner_evidence or owner_evidence.source.upper() not in {"CADERNETA", "CRP"} or report.ownership_issues:
        if result.owner_name:
            report.blocked.append({"field": "owner_name", "value": result.owner_name, "reason": ";".join(report.ownership_issues or ["no_cadastral_evidence"])})
        result.owner_name = ""
        result.owner_tax_id = "" if "owner_tax_id_matches_lessee" in report.ownership_issues else result.owner_tax_id
        report.field_evidence.pop("owner_name", None)
    _attach(result, report)
    return result


def _extract_contract_values(report: Step33Report) -> None:
    for zone in report.structure.of_type(FINANCIAL_SECTION):
        for match in re.finditer(r"\b(?:renda|contrapartida)\D{0,80}?(\d+(?:[.,]\d{1,2})?)\s*(EUR|€|euros?)", zone.text, re.I):
            amount = match.group(1).replace(",", ".")
            _add(report, RankedValue("rent_amount", amount, "CONTRACT", source_score("rent_amount", "CONTRACT")))
            _add(report, RankedValue("rent_currency", "EUR", "CONTRACT", source_score("rent_currency", "CONTRACT")))
            if re.search(r"\bmensal", zone.text[match.end():match.end() + 80], re.I):
                _add(report, RankedValue("monthly_rent", f"{amount} EUR", "CONTRACT", source_score("monthly_rent", "CONTRACT")))


def _extract_signed_date(report: Step33Report) -> None:
    for source, zone_types in (("SIGNATURE", (SIGNATURE_SECTION,)), ("NOTARIZATION", (NOTARIZATION_SECTION,))):
        for zone in report.structure.of_type(*zone_types):
            match = re.search(r"\b(?:assinado|outorgado|feito)\b.{0,80}?(\d{1,2}[/-]\d{1,2}[/-](?:19|20)\d{2}|(?:19|20)\d{2}-\d{2}-\d{2})", zone.text, re.I)
            if match:
                _add(report, RankedValue("signed_date", match.group(1), source, source_score("signed_date", source)))


def _add(report: Step33Report, candidate: RankedValue) -> None:
    values = report.candidates.setdefault(candidate.field, [])
    if not any(item.value == candidate.value and item.source == candidate.source for item in values):
        values.append(candidate)


def _resolve_field(field: str, candidates: list[RankedValue]) -> FieldEvidence | None:
    if field != "lessor":
        return resolve_consensus(field, candidates)
    valid = [item for item in candidates if item.valid and validate_entity(item.value)[0]]
    if not valid:
        return None
    highest = max(item.source_score for item in valid)
    selected = [item for item in valid if item.source_score >= highest - 10]
    names: list[str] = []
    for candidate in selected:
        if candidate.value not in names:
            names.append(candidate.value)
    primary = selected[0]
    return FieldEvidence("; ".join(names), min(1.0, (highest + min(15, (len(names) - 1) * 5)) / 100), primary.source, len(selected))


def _candidate_dict(value: RankedValue) -> dict[str, object]:
    return {"value": value.value, "source": value.source.lower(), "source_score": value.source_score, "valid": value.valid}


def _attach(result: ExtractionResult, report: Step33Report) -> None:
    raw = result.raw_json if isinstance(result.raw_json, dict) else {}
    result.raw_json = dict(raw)
    result.raw_json["step3_3_field_centric"] = report.to_dict()


__all__ = ["Step33Report", "prepare_field_centric_extraction", "apply_field_centric_extraction"]
