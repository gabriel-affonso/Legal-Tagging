"""Step 3.3 field-centric extraction engine.

It is deliberately a final publisher: upstream LLM output is input evidence,
never an authority that can overwrite a better documentary source.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

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
from .property_resolver import cadastral_property_groups, extract_property_candidates
from .source_ranking import RankedValue, source_score
from .validators.signature_date import normalize_date


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
    cadastral_properties: list[dict[str, object]] = field(default_factory=list)

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
            "version": "3.3.2",
            "active": self.active,
            "document_structure": self.structure.to_dict(),
            "party_extraction": self.parties.to_dict(),
            "field_candidates": {field: [_candidate_dict(item) for item in values] for field, values in self.candidates.items()},
            "field_evidence": evidence,
            "blocked": self.blocked,
            "ownership_issues": self.ownership_issues,
            "cadastral_properties": self.cadastral_properties,
            "confidence_dimensions": _cadastral_confidence_dimensions(self.cadastral_properties),
        }


def prepare_field_centric_extraction(document_text: str) -> Step33Report:
    structure = detect_document_structure(document_text)
    active = bool(structure.zones)
    report = Step33Report(active=active, structure=structure)
    if not active:
        return report
    report.parties = extract_parties(structure)
    for party in report.parties.roles:
        field = "lessor" if party.role == "lessor" else "lessee"
        _add(report, RankedValue(field, party.value, party.source, party.source_score, party.valid))
        if party.role == "lessee" and party.tax_id:
            _add(report, RankedValue(
                "lessee_tax_id", party.tax_id, party.source,
                source_score("lessee_tax_id", party.source), party.valid,
            ))
    report.cadastral_properties = cadastral_property_groups(structure, document_text)
    if _has_multi_property_ambiguity(report):
        for field in ("property_name", "property_article", "property_section", "property_total_area"):
            report.blocked.append({
                "field": field,
                "value": "",
                "reason": "multiple_properties_preserved_in_structured_output",
            })
    for candidate in extract_property_candidates(structure, document_text):
        _add(report, candidate)
    _extract_contract_values(report)
    _extract_signed_date(report)
    if structure.of_type(SIGNATURE_SECTION) and not report.candidates.get("signed_date"):
        report.blocked.append({
            "field": "signed_date",
            "value": "",
            "reason": "signature_date_not_machine_readable",
        })
    return report


def apply_field_centric_extraction(result: ExtractionResult, report: Step33Report) -> ExtractionResult:
    category = (result.document_category or "").lower()
    if not report.active or category not in {"lease_contract", "property_document"}:
        _attach(result, report)
        return result

    # Publish evidence-backed deterministic values.  Fields with no Step 3.3
    # candidate retain the Step 3.2 result, except ownership which must never
    # be guessed from a contract party or an LLM answer.
    for field, candidates in report.candidates.items():
        if category == "property_document" and field not in PROPERTY_DOCUMENT_FIELDS:
            continue
        if _has_multi_property_ambiguity(report) and field in {
            "property_name", "property_article", "property_section", "property_total_area",
        }:
            continue
        consensus = _resolve_field(field, candidates)
        if not consensus:
            continue
        if field in {"lessor", "lessee"} and not validate_entity(consensus.value)[0]:
            report.blocked.append({"field": field, "value": consensus.value, "reason": "entity_validation"})
            continue
        setattr(result, field, consensus.value)
        report.field_evidence[field] = consensus

    if _has_multi_property_ambiguity(report):
        for field in ("property_name", "property_article", "property_section", "property_total_area"):
            setattr(result, field, "")
            report.field_evidence.pop(field, None)

    if category == "lease_contract":
        _clear_invalid_contract_parties(result, report)
        _clear_unbacked_commercial_values(result, report)
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
        result.owner_tax_id = ""
        report.field_evidence.pop("owner_name", None)
    _attach(result, report)
    return result


def _extract_contract_values(report: Step33Report) -> None:
    for zone in report.structure.of_type(FINANCIAL_SECTION):
        money = r"(\d{1,3}(?:[.\s]\d{3})*(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
        for match in re.finditer(rf"\b(?:renda|contrapartida).{{0,180}}?{money}\s*(EUR|€|euros?)", zone.text, re.I | re.S):
            amount = _normalize_money(match.group(1))
            _add(report, RankedValue("rent_amount", amount, "CONTRACT", source_score("rent_amount", "CONTRACT")))
            _add(report, RankedValue("rent_currency", "EUR", "CONTRACT", source_score("rent_currency", "CONTRACT")))
            context = zone.text[max(0, match.start() - 160):match.end() + 360]
            if re.search(r"\b(?:renda\s+mensal|mensalmente|mensalidade)\b", context, re.I):
                _add(report, RankedValue("monthly_rent", f"{amount} EUR", "CONTRACT", source_score("monthly_rent", "CONTRACT")))
                _add(report, RankedValue("rent_frequency", "monthly", "CONTRACT", source_score("rent_frequency", "CONTRACT")))
                _add(report, RankedValue("rent_unit", "fixed_total", "CONTRACT", source_score("rent_unit", "CONTRACT")))
            elif re.search(r"\b(?:renda\s+anual|paga\s+anualmente|por\s+ano)\b", context, re.I):
                _add(report, RankedValue("rent_frequency", "annual", "CONTRACT", source_score("rent_frequency", "CONTRACT")))
                if re.search(r"\bpor\s+hectare\b|/\s*ha\b", context, re.I):
                    _add(report, RankedValue("rent_unit", "per_hectare", "CONTRACT", source_score("rent_unit", "CONTRACT")))
                    _add(report, RankedValue("annual_rent", f"{amount} EUR per_hectare", "CONTRACT", source_score("annual_rent", "CONTRACT")))
                if re.search(r"\b(?:efetivamente|efectivamente)\s+ocupad[oa]\b", context, re.I):
                    _add(report, RankedValue("rent_basis", "effectively_occupied_area", "CONTRACT", source_score("rent_basis", "CONTRACT")))
        for field, terms in (
            ("option_price", r"op[cç][aã]o\s+de\s+compra|pre[cç]o\s+da\s+op[cç][aã]o"),
            ("purchase_price", r"pre[cç]o\s+(?:de\s+)?compra|promessa\s+de\s+compra"),
            ("assignment_price", r"cess[aã]o|ced[eê]ncia"),
        ):
            match = re.search(rf"\b(?:{terms})\b.{{0,100}}?(\d+(?:[.,]\d{{1,2}})?)\s*(?:EUR|€|euros?)", zone.text, re.I)
            if match:
                _add(report, RankedValue(field, f"{match.group(1).replace(',', '.')} EUR", "CONTRACT", source_score(field, "CONTRACT")))


def _extract_signed_date(report: Step33Report) -> None:
    for source, zone_types in (("SIGNATURE", (SIGNATURE_SECTION,)), ("NOTARIZATION", (NOTARIZATION_SECTION,))):
        for zone in report.structure.of_type(*zone_types):
            match = re.search(r"\b(?:assinado|outorgado|feito)\b.{0,100}?(\d{1,2}[/-]\d{1,2}[/-](?:19|20)\d{2}|(?:19|20)\d{2}-\d{2}-\d{2})", zone.text, re.I | re.S)
            value = normalize_date(match.group(1)) if match else ""
            if not value:
                words = re.search(
                    r"\b(?:assinado|outorgado|feito)\b.{0,100}?\b(\d{1,2})\s+de\s+"
                    r"(janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\s+de\s+((?:19|20)\d{2})\b",
                    zone.text,
                    re.I | re.S,
                )
                value = _long_date(words) if words else ""
            if value:
                _add(report, RankedValue("signed_date", value, source, source_score("signed_date", source)))


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
    # Combining a lower-authority OCR reading with an authoritative identity
    # annex/notarization created plausible-looking duplicate parties.  Keep
    # every distinct entity within the best source tier only.
    selected = [item for item in valid if item.source_score == highest]
    names: list[str] = []
    for candidate in selected:
        if candidate.value not in names:
            names.append(candidate.value)
    primary = selected[0]
    return FieldEvidence("; ".join(names), min(1.0, (highest + min(15, (len(names) - 1) * 5)) / 100), primary.source, len(selected))


def _normalize_money(value: str) -> str:
    raw = re.sub(r"\s+", "", value)
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    return f"{float(raw):.2f}"


def _has_multi_property_ambiguity(report: Step33Report) -> bool:
    if len(report.cadastral_properties) <= 1:
        return False
    matched = sum(bool(item.get("matched_contract_identity")) for item in report.cadastral_properties)
    return matched != 1


def _cadastral_confidence_dimensions(properties: list[dict[str, object]]) -> dict[str, float]:
    if not properties:
        return {
            "extraction_confidence": 0.0,
            "identity_confidence": 0.0,
            "completeness_score": 0.0,
            "consistency_score": 0.0,
            "final_confidence": 0.0,
        }
    completeness: list[float] = []
    identity: list[float] = []
    keys: list[str] = []
    for item in properties:
        completeness.append(sum(bool(item.get(field)) for field in (
            "property_name", "property_article", "property_section", "property_total_area", "owner_name",
        )) / 5)
        identity.append(
            0.50 * bool(item.get("property_article"))
            + 0.35 * bool(item.get("property_section"))
            + 0.15 * bool(item.get("property_name"))
        )
        if item.get("matrix_key"):
            keys.append(str(item["matrix_key"]))
    extraction_score = 0.99
    identity_score = min(identity, default=0.0)
    completeness_score = min(completeness, default=0.0)
    consistency_score = 1.0 if len(keys) == len(properties) and len(keys) == len(set(keys)) else 0.6
    final = min(extraction_score, identity_score, completeness_score, consistency_score)
    return {
        "extraction_confidence": extraction_score,
        "identity_confidence": round(identity_score, 4),
        "completeness_score": round(completeness_score, 4),
        "consistency_score": consistency_score,
        "final_confidence": round(final, 4),
    }


def _long_date(match: re.Match[str]) -> str:
    import unicodedata
    months = {
        "JANEIRO": 1, "FEVEREIRO": 2, "MARCO": 3, "ABRIL": 4,
        "MAIO": 5, "JUNHO": 6, "JULHO": 7, "AGOSTO": 8,
        "SETEMBRO": 9, "OUTUBRO": 10, "NOVEMBRO": 11, "DEZEMBRO": 12,
    }
    folded = "".join(char for char in unicodedata.normalize("NFKD", match.group(2).upper()) if not unicodedata.combining(char))
    month = months.get(folded)
    if not month:
        return ""
    day, year = int(match.group(1)), int(match.group(3))
    try:
        from datetime import date
        parsed = date(year, month, day)
    except ValueError:
        return ""
    return parsed.isoformat()


def _clear_invalid_contract_parties(result: ExtractionResult, report: Step33Report) -> None:
    for field in ("lessor", "lessee"):
        value = str(getattr(result, field, "") or "").strip()
        if value and field not in report.field_evidence and not validate_entity(value)[0]:
            setattr(result, field, "")
            report.blocked.append({"field": field, "value": value[:180], "reason": "no_valid_party_evidence"})


def _clear_unbacked_commercial_values(result: ExtractionResult, report: Step33Report) -> None:
    for field in ("option_price", "purchase_price", "assignment_price"):
        value = str(getattr(result, field, "") or "").strip()
        if value and field not in report.field_evidence:
            setattr(result, field, "")
            report.blocked.append({"field": field, "value": value[:180], "reason": "no_field_specific_contract_evidence"})


PROPERTY_DOCUMENT_FIELDS = {
    "property_name", "property_article", "property_section", "property_parish",
    "property_total_area", "owner_name", "owner_tax_id",
}


def _candidate_dict(value: RankedValue) -> dict[str, object]:
    return {"value": value.value, "source": value.source.lower(), "source_score": value.source_score, "valid": value.valid}


def _attach(result: ExtractionResult, report: Step33Report) -> None:
    raw = result.raw_json if isinstance(result.raw_json, dict) else {}
    result.raw_json = dict(raw)
    result.raw_json["step3_3_field_centric"] = report.to_dict()


__all__ = ["Step33Report", "prepare_field_centric_extraction", "apply_field_centric_extraction"]
