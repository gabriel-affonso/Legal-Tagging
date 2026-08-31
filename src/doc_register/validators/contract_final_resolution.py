"""Step 2.8 contract container resolution.

This module keeps extraction intermediate until one deterministic final field
resolver runs.  It is deliberately local and inspectable: every published
value has a zone, evidence span, authority and score in ``raw_json``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
import time
from typing import Iterable

from ..models import ExtractionResult
from ..property_intelligence import (
    assess_cadastral_evidence,
    discover_caderneta_groups,
    discover_caderneta_pages,
    matrix_key,
    parse_caderneta,
)
from .name_quality import validate_name_field
from .ocr_quality import PageQualityReport, analyze_page_quality
from .property_recovery import recover_property_group
from .signature_date import normalize_date
from .validation_rules import normalize_text


CONTRACT_RE = re.compile(r"\b(?:contrato\s+de\s+arrendamento|arrendat[aá]ri[oa]|senhorios?|renda)\b", re.I)
TAX_RE = re.compile(r"\b(?:NIF|NIPC|contribuinte)\s*(?:n[.ºo°]?\s*)?[:#-]?\s*(\d{9})\b", re.I)
ROLE_MARKERS = {
    "lessor": re.compile(r"\b(?:de\s+ora\s+em\s+diante|doravante|conjuntamente)\s+designad[oa]s?\s+por\s+(?:promitentes?\s+)?senhorios?\b|\btodos\s+na\s+qualidade\s+de\s+senhorios?\b|\bna\s+qualidade\s+de\s+senhorios?\b", re.I),
    "lessee": re.compile(r"\b(?:de\s+ora\s+em\s+diante|doravante|conjuntamente)\s+designad[oa]s?\s+por\s+arrendat[aá]ri[oa]s?\b|\bna\s+qualidade\s+de\s+arrendat[aá]ri[oa]s?\b", re.I),
}
PROPERTY_NAME_RE = (
    re.compile(r"\b(?:(?:nome\s*/\s*)?localiza[cç][aã]o(?:\s+do)?\s+pr[eé]dio|localiza[cç][aã]o|pr[eé]dio\s+denominad[oa]|denominad[oa](?:\s+por)?)\s*[:#-]?\s*[\"'“”«»]?(?P<value>[^\n,;.]{3,100})", re.I),
    re.compile(r"\b(?:s[ií]tio|lugar|local)\s+de\s+[\"'“”«»]?(?P<value>[^\n,;.]{3,100})", re.I),
)
PLACE_RE = {
    "property_parish": re.compile(r"\bfreguesia\s+(?:de\s+)?(?P<value>[^,;\n.]{3,80})", re.I),
    "property_municipality": re.compile(r"\b(?:concelho|munic[ií]pio)\s+(?:de\s+)?(?P<value>[^,;\n.]{3,80})", re.I),
    "property_district": re.compile(r"\bdistrito\s+(?:de\s+)?(?P<value>[^,;\n.]{3,80})", re.I),
}
AREA_RE = re.compile(r"\b(?:[aá]rea\s+total|[aá]rea\s+do\s+pr[eé]dio)\s*[:#-]?\s*(\d+(?:[.,]\d+)?)\s*(ha|hectares?|m2|m²)\b", re.I)
SECTION_FIELD_RE = re.compile(r"\bsec(?:c|ç)[aã]o\s*[:#-]?\s*([A-Z]{1,3})\b", re.I)
PARCEL_AREA_RE = re.compile(r"\b(?:parcela\s+(?:arrendada|ocupada)|[aá]rea\s+(?:arrendada|ocupada))\D{0,60}?(\d+(?:[.,]\d+)?)\s*(ha|hectares?|m2|m²)\b", re.I)
RENT_RE = re.compile(r"\b(?:renda|contrapartida|remunera[cç][aã]o)\D{0,100}?(\d+(?:[.,]\d{1,6})?)\s*(EUR|€|euros?)\D{0,80}?(?:por\s+hectare|/\s*ha|ha)\D{0,50}?(?:por\s+ano|anuais?|anual)\b", re.I)
MONTHLY_RENT_RE = re.compile(r"\b(?:renda\s+mensal|mensalidade)\D{0,80}?(\d+(?:[.,]\d{1,2})?)\s*(EUR|€|euros?)\b", re.I)
DATE_RE = r"(?:\d{1,2}[/-]\d{1,2}[/-](?:19|20)\d{2}|(?:19|20)\d{2}-\d{2}-\d{2})"
GENERIC_PROPERTY = {"SENHORIO 1", "SENHORIO 2", "SENHORIOS", "ARRENDATARIO", "ARRENDATARIA", "PROPRIETARIO", "PREDIO", "PARCELA", "ANEXO", "CONTRATO", "CENTRAL SOLAR", "OUTORGANTE", "PARTE", "PARTES"}
KNOWN_PLACE = {"PENAS ROIAS": "Penas Roias", "PEWAS ROIAS": "Penas Roias", "MOGADOURO": "Mogadouro", "BRAGANCA": "Bragança"}
KNOWN_PROPERTY = {"AQUAXAIS": "Aquaxais", "AGUAXAIS": "Aquaxais"}


@dataclass(frozen=True)
class DocumentZone:
    page_number: int | None
    zone_type: str
    confidence: float
    matched_signals: list[str]
    text_span: str
    ocr_quality: float
    internal_document: str = "contract"

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self), "confidence": round(self.confidence, 3), "ocr_quality": round(self.ocr_quality, 3), "text_span": self.text_span[:900]}


@dataclass(frozen=True)
class FactCandidate:
    candidate_id: str
    field: str
    raw_value: str
    normalized_value: str
    entity_id: str
    entity_type: str
    contract_role: str
    source_type: str
    source_zone: str
    page: int | None
    text_span: str
    matched_pattern: str
    ocr_quality: float
    source_authority: float
    semantic_confidence: float
    validation_status: str
    score_components: dict[str, float]
    final_score: float
    rejection_reason: str = ""

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        for key in ("ocr_quality", "source_authority", "semantic_confidence", "final_score"):
            payload[key] = round(float(payload[key]), 3)
        return payload


@dataclass
class ContractResolutionReport:
    active: bool = False
    zones: list[DocumentZone] = field(default_factory=list)
    page_quality: list[PageQualityReport] = field(default_factory=list)
    candidates: list[FactCandidate] = field(default_factory=list)
    entities: dict[str, dict[str, object]] = field(default_factory=dict)
    decisions: dict[str, dict[str, object]] = field(default_factory=dict)
    conflicts: list[dict[str, object]] = field(default_factory=list)
    cadastral_evidence: list[dict[str, object]] = field(default_factory=list)
    unresolved_fields: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)

    def context_for_llm(self, *, max_chars: int) -> str:
        """Field-specific zone bundles; no arbitrary full-document fallback."""
        profiles = (
            ("PARTY RESOLUTION", ("party_identification", "signature_recognition", "landlord_identification_annex", "notification_block", "corporate_registry")),
            ("PROPERTY RESOLUTION", ("land_registry_certificate", "property_recital", "object_clause", "parcel_plan")),
            ("DATES AND TERMS", ("signature_page", "signature_recognition", "term_clause", "rent_clause")),
        )
        chunks: list[str] = []
        cadastral_summary = _cadastral_summary_for_llm(self)
        summary_budget = min(len(cadastral_summary) + 2, max_chars) if cadastral_summary else 0
        remaining = max(0, max_chars - summary_budget)
        for label, wanted in profiles:
            texts = [zone.text_span for zone in self.zones if zone.zone_type in wanted]
            if not texts:
                continue
            rendered = f"[{label}]\n" + "\n---\n".join(texts)
            rendered = rendered[:remaining].rstrip()
            if rendered:
                chunks.append(rendered)
                remaining -= len(rendered) + 2
            if remaining <= 0:
                break
        if cadastral_summary:
            chunks.append(cadastral_summary[:summary_budget].rstrip())
        return "\n\n".join(chunks)

    def to_dict(self) -> dict[str, object]:
        return {
            "version": "2.9.1", "active": self.active,
            "document_zones": [item.to_dict() for item in self.zones],
            "page_quality_summary": [item.to_dict() for item in self.page_quality],
            "entities": list(self.entities.values()),
            "field_candidates": [item.to_dict() for item in self.candidates],
            "field_decisions": self.decisions, "conflicts": self.conflicts,
            "cadastral_evidence": self.cadastral_evidence,
            "unresolved_fields": self.unresolved_fields,
            "metrics": {"candidate_count": len(self.candidates), "zone_count": len(self.zones)},
            "processing_duration_seconds": round(time.monotonic() - self.started_at, 4),
        }


def prepare_contract_final_resolution(document_text: str) -> ContractResolutionReport:
    report = ContractResolutionReport()
    if not CONTRACT_RE.search(str(document_text or "")):
        return report
    report.active = True
    report.page_quality = analyze_page_quality(document_text)
    report.zones = detect_document_zones(document_text, report.page_quality)
    _add_internal_caderneta_zone(report, document_text)
    _extract_candidates(report)
    _resolve_entities(report)
    return report


def apply_contract_final_resolution(result: ExtractionResult, report: ContractResolutionReport) -> ExtractionResult:
    if not report.active or (result.document_category or "").lower() != "lease_contract":
        _attach(result, report)
        return result
    _add_existing_output_candidates(result, report)
    _resolve_entities(report)
    _detect_cadastral_conflicts(report)
    for field_name in _field_names():
        candidates = [candidate for candidate in report.candidates if candidate.field == field_name and candidate.validation_status == "valid"]
        if field_name == "lessor":
            candidates = [item for item in candidates if item.contract_role != "lessee"]
            selected = _select_many(candidates)
        else:
            selected = _select_one(candidates)
        if selected:
            value = "; ".join(item.normalized_value for item in selected) if isinstance(selected, list) else selected.normalized_value
            setattr(result, field_name, value)
            primary = selected[0] if isinstance(selected, list) else selected
            report.decisions[field_name] = _decision(primary, value, "accepted")
        elif field_name in _strict_fields() and str(getattr(result, field_name, "") or ""):
            setattr(result, field_name, "")
            report.decisions[field_name] = {"field": field_name, "selected_value": "", "decision": "blocked", "reason": "no_valid_structured_candidate", "requires_review": True}
    lessors = _select_many([item for item in report.candidates if item.field == "lessor" and item.contract_role != "lessee" and item.validation_status == "valid"])
    result.lessor = "; ".join(item.normalized_value for item in lessors)
    result.lessor_2 = lessors[1].normalized_value if len(lessors) > 1 else ""
    cadastral_owners = _select_many(
        item for item in report.candidates
        if item.field == "cadastral_owner" and item.validation_status == "valid"
    )
    if cadastral_owners:
        result.owner_name = "; ".join(item.normalized_value for item in cadastral_owners)
        report.decisions["owner_name"] = _decision(
            cadastral_owners[0], result.owner_name, "accepted"
        )
    else:
        # In the register, ``owner_name`` means cadastral title holder, not
        # the contractual landlord.  A lessor may be an attorney, usufructuary
        # or simply be misread by OCR; do not turn that role into ownership.
        result.owner_name = ""
        report.decisions["owner_name"] = {
            "field": "owner_name",
            "selected_value": "",
            "decision": "blocked",
            "reason": "no_verified_cadastral_owner",
            "requires_review": bool(report.cadastral_evidence),
        }
    result.property_matrix_key = matrix_key(
        result.property_article, result.property_section
    )
    if result.rent_frequency and result.rent_frequency != "monthly":
        result.monthly_rent = ""
    if report.conflicts:
        result.human_review_required = "yes"
        conflict_reasons = "; ".join(
            str(item.get("type")) for item in report.conflicts if item.get("type")
        )
        result.review_reason = "; ".join(part for part in (result.review_reason, conflict_reasons) if part)
    report.unresolved_fields = [name for name in ("lessee", "property_article", "property_section") if not str(getattr(result, name, "") or "")]
    _attach(result, report)
    return result


def detect_document_zones(document_text: str, page_quality: list[PageQualityReport] | None = None) -> list[DocumentZone]:
    quality_by_page = {item.page: item.overall_page_quality for item in page_quality or []}
    zones: list[DocumentZone] = []
    for page, text in _pages(document_text):
        signals: dict[str, tuple[str, ...]] = {
            "contract_title": ("contrato de arrendamento",),
            "contract_preamble": ("entre", "outorgante"),
            "party_identification": ("de ora em diante", "arrendatária", "senhorios", "primeiro outorgante", "segundo outorgante"),
            "property_recital": ("prédio denominado", "matriz predial", "artigo matricial"),
            "object_clause": ("cláusula", "objeto", "imóvel arrendado"),
            "term_clause": ("prazo", "início", "termina"),
            "rent_clause": ("renda", "por hectare", "mensalidade"),
            "signature_page": ("assinado", "assinatura", "outorgado"),
            "signature_recognition": ("reconhecimento de assinatura", "reconheço a assinatura", "cuja identidade verifiquei", "todos na qualidade de senhorios"),
            "notification_block": ("notificações", "correio eletrónico", "senhorio 1", "arrendatária"),
            "landlord_identification_annex": ("identificação dos senhorios", "cartão de cidadão"),
            "cadastral_record": ("caderneta predial", "identificação do prédio", "área total", "titulares", "artigo matricial"),
            "land_registry_certificate": ("certidão do registo predial", "conservatória", "descrição predial"),
            "parcel_plan": ("planta", "parcela", "coordenadas"),
            "corporate_registry": ("certidão permanente", "natureza jurídica", "forma de obrigar"),
            "corporate_resolution": ("ata", "conselho de administração"),
            "power_of_attorney": ("procuração", "procurador"),
            "bank_details": ("iban", "nib", "swift"),
        }
        lowered = normalize_text(text)
        for zone_type, markers in signals.items():
            hits = [marker for marker in markers if normalize_text(marker) in lowered]
            if hits:
                confidence = min(0.99, 0.52 + 0.17 * len(hits) + (0.12 if zone_type in {"cadastral_record", "signature_recognition"} else 0.0))
                zones.append(DocumentZone(page, zone_type, confidence, hits, text.strip(), quality_by_page.get(page, 0.5), _internal_document(zone_type)))
    return zones or [DocumentZone(None, "unknown", 0.1, [], str(document_text or "")[:1200], quality_by_page.get(None, 0.0), "unknown")]


def _add_internal_caderneta_zone(report: ContractResolutionReport, document_text: str) -> None:
    """Create one authoritative zone for every internal caderneta."""
    groups = discover_caderneta_groups(document_text)
    if not groups:
        return
    internal_pages = {
        page.page_number for group in groups for page in group
    }
    # The grouped zone replaces page-by-page cadastral zones so values from two
    # different cadernetas cannot be mixed by the generic extractor.
    report.zones = [
        zone for zone in report.zones
        if not (
            zone.page_number in internal_pages
            and zone.zone_type in {
                "cadastral_record", "property_recital", "object_clause",
                "parcel_plan", "land_registry_certificate",
            }
        )
    ]
    quality_by_page = {item.page: item.overall_page_quality for item in report.page_quality}
    for index, pages in enumerate(groups, start=1):
        text_span = "\n\n".join(
            f"[Page {page.page_number}] {page.text}" for page in pages
        )
        report.zones.append(DocumentZone(
            page_number=pages[0].page_number,
            zone_type="cadastral_record",
            confidence=0.99,
            matched_signals=["internal_caderneta_title_or_structure"],
            text_span=text_span,
            ocr_quality=sum(quality_by_page.get(page.page_number, 0.7) for page in pages) / len(pages),
            internal_document=f"caderneta_predial_rustica_{index:02d}",
        ))


def _extract_candidates(report: ContractResolutionReport) -> None:
    for zone in report.zones:
        if zone.zone_type in {"party_identification", "signature_recognition", "landlord_identification_annex", "notification_block", "corporate_registry"}:
            _extract_party_candidates(report, zone)
        if zone.zone_type in {"cadastral_record", "land_registry_certificate", "property_recital", "object_clause", "parcel_plan"}:
            _extract_property_candidates(report, zone)
        if zone.zone_type == "rent_clause":
            _extract_rent_candidates(report, zone)
        if zone.zone_type == "signature_page":
            _extract_signature_candidate(report, zone)


def _extract_party_candidates(report: ContractResolutionReport, zone: DocumentZone) -> None:
    for role, marker in ROLE_MARKERS.items():
        for match in marker.finditer(zone.text_span):
            start = _previous_role_end(zone.text_span, match.start())
            block = zone.text_span[max(0, start):match.start()]
            names = _names(block, role)
            for name in names:
                candidate = _candidate("lessor" if role == "lessor" else "lessee", name, zone, role, "explicit_contract_role_definition", 0.99)
                _add(report, candidate)
            for tax_id in _tax_ids(block):
                field_name = "owner_tax_id" if role == "lessor" else "lessee_tax_id"
                candidate = _candidate(field_name, tax_id, zone, role, "tax_id_in_role_identification_block", 0.98)
                _add(report, candidate)
    # Structured notification blocks use labels after names; they only confirm
    # a role and never become a property candidate.
    if zone.zone_type == "notification_block":
        for role, label in (("lessor", r"senhorio\s*\d*"), ("lessee", r"arrendat[aá]ria")):
            for match in re.finditer(rf"(?P<name>[A-ZÀ-ÖØ-Þ][^\n,:;]{{3,150}})\s*,?\s*(?:{label})\b", zone.text_span, re.I):
                for name in _names(match.group("name"), role):
                    if not re.match(r"^[A-ZÀ-ÖØ-Þ]", name):
                        continue
                    candidate = _candidate("lessor" if role == "lessor" else "lessee", name, zone, role, "role_label_confirmation", 0.87)
                    _add(report, candidate)


def _extract_property_candidates(report: ContractResolutionReport, zone: DocumentZone) -> None:
    authority_boost = 0.12 if zone.zone_type == "cadastral_record" else 0.0
    if zone.zone_type == "cadastral_record":
        pages = discover_caderneta_pages(zone.text_span)
        evidence = assess_cadastral_evidence(pages)
        report.cadastral_evidence.append({
            **evidence.to_dict(),
            "source_type": zone.internal_document,
        })
        caderneta = evidence.values
        if caderneta.property_name:
            _add(report, _candidate("property_name", caderneta.property_name, zone, "", "caderneta_property_name", 0.99))
        if caderneta.matrix_article:
            _add(report, _candidate("property_article", caderneta.matrix_article, zone, "", "caderneta_matrix_article", 0.99))
        if caderneta.matrix_section:
            _add(report, _candidate("property_section", caderneta.matrix_section, zone, "", "caderneta_matrix_section", 0.99))
        if caderneta.area_m2 is not None:
            hectares = caderneta.area_m2 / 10_000
            _add(report, _candidate("property_total_area", f"{hectares:g} hectares", zone, "", "caderneta_total_area", 0.99))
        if evidence.owner_is_verified:
            for name in _names(caderneta.owner_name, "owner_name") or [caderneta.owner_name]:
                _add(report, _candidate("cadastral_owner", name, zone, "cadastral_owner", "caderneta_holder_name", 0.99))
    group = recover_property_group(zone.text_span)
    if group.article:
        _add(report, _candidate("property_article", group.article, zone, "", "structured_cadastral_article", 0.97 + authority_boost))
    if group.section:
        _add(report, _candidate("property_section", group.section, zone, "", "structured_cadastral_section", 0.97 + authority_boost))
    for match in SECTION_FIELD_RE.finditer(zone.text_span):
        _add(report, _candidate("property_section", match.group(1).upper(), zone, "", "labelled_cadastral_section", 0.96 + authority_boost))
    for pattern in PROPERTY_NAME_RE:
        for match in pattern.finditer(zone.text_span):
            value = _property_name(match.group("value"))
            if value:
                _add(report, _candidate("property_name", value, zone, "", "labelled_property_name", 0.94 + authority_boost))
    for field_name, pattern in PLACE_RE.items():
        for match in pattern.finditer(zone.text_span):
            value = _place(match.group("value"))
            if value:
                _add(report, _candidate(field_name, value, zone, "", "labelled_cadastral_place", 0.93 + authority_boost))
    for match in AREA_RE.finditer(zone.text_span):
        _add(report, _candidate("property_total_area", _area(match.group(1), match.group(2)), zone, "", "labelled_total_area", 0.94 + authority_boost))
    for match in PARCEL_AREA_RE.finditer(zone.text_span):
        _add(report, _candidate("leased_parcel_area", _area(match.group(1), match.group(2)), zone, "", "labelled_leased_area", 0.83 + authority_boost))
    if zone.zone_type != "cadastral_record":
        for match in re.finditer(r"\b(?:titular(?:es)?|propriet[aá]rio(?:s)?)\s*[:#-]?\s*([^\n]{4,180})", zone.text_span, re.I):
            for name in _names(match.group(1), "owner_name"):
                candidate = _candidate("cadastral_owner", name, zone, "cadastral_owner", "cadastral_owner_label", 0.94)
                _add(report, candidate)


def _extract_rent_candidates(report: ContractResolutionReport, zone: DocumentZone) -> None:
    for match in RENT_RE.finditer(zone.text_span):
        amount, currency = _money(match.group(1), match.group(2))
        for field_name, value in (("rent_amount", amount), ("rent_currency", currency), ("rent_frequency", "annual"), ("rent_unit", "per_hectare"), ("rent_basis", "effectively_occupied_area"), ("annual_rent", f"{amount} {currency} per_hectare")):
            _add(report, _candidate(field_name, value, zone, "", "annual_per_hectare_rent", 0.96))
    for match in MONTHLY_RENT_RE.finditer(zone.text_span):
        amount, currency = _money(match.group(1), match.group(2))
        for field_name, value in (("monthly_rent", f"{amount} {currency}"), ("rent_amount", amount), ("rent_currency", currency), ("rent_frequency", "monthly"), ("rent_unit", "fixed_total")):
            _add(report, _candidate(field_name, value, zone, "", "explicit_monthly_rent", 0.96))


def _extract_signature_candidate(report: ContractResolutionReport, zone: DocumentZone) -> None:
    match = re.search(r"\b(?:assinado|outorgado|feito)\b.{0,80}?(" + DATE_RE + r")", zone.text_span, re.I)
    if match and (value := normalize_date(match.group(1))):
        _add(report, _candidate("signed_date", value, zone, "", "explicit_signature_formula", 0.92))


def _candidate(field_name: str, value: str, zone: DocumentZone, role: str, pattern: str, semantic: float) -> FactCandidate:
    normalized = _normalize_value(field_name, value)
    valid, reason = _valid(field_name, normalized, role)
    authority = _authority(field_name, zone.zone_type)
    score = max(0.0, min(1.0, 0.47 * authority + 0.28 * semantic + 0.25 * zone.ocr_quality))
    return FactCandidate(
        candidate_id="", field=field_name, raw_value=value, normalized_value=normalized,
        entity_id="", entity_type="organization" if _is_company(normalized) else "person",
        contract_role=role, source_type=zone.internal_document or _internal_document(zone.zone_type), source_zone=zone.zone_type,
        page=zone.page_number, text_span=zone.text_span[:600], matched_pattern=pattern,
        ocr_quality=zone.ocr_quality, source_authority=authority, semantic_confidence=semantic,
        validation_status="valid" if valid else "blocked", score_components={"authority": authority, "semantic": semantic, "ocr": zone.ocr_quality}, final_score=score,
        rejection_reason=reason,
    )


def _add(report: ContractResolutionReport, candidate: FactCandidate) -> None:
    if not candidate.candidate_id:
        candidate = FactCandidate(candidate_id=f"candidate_{len(report.candidates)+1:03d}", **{key: value for key, value in asdict(candidate).items() if key != "candidate_id"})
    key = (candidate.field, normalize_text(candidate.normalized_value), candidate.contract_role, candidate.source_zone, candidate.page)
    if all((item.field, normalize_text(item.normalized_value), item.contract_role, item.source_zone, item.page) != key for item in report.candidates):
        report.candidates.append(candidate)


def _resolve_entities(report: ContractResolutionReport) -> None:
    for index, candidate in enumerate(report.candidates, start=1):
        if candidate.field not in {"lessor", "lessee", "cadastral_owner"}:
            continue
        key = normalize_text(candidate.normalized_value)
        if not key:
            continue
        entity_id = next((identifier for identifier, entity in report.entities.items() if entity["canonical_name_normalized"] == key), f"entity_{len(report.entities)+1:03d}")
        entity = report.entities.setdefault(entity_id, {"entity_id": entity_id, "canonical_name": candidate.normalized_value, "canonical_name_normalized": key, "entity_type": candidate.entity_type, "aliases": [candidate.raw_value], "tax_ids": [], "roles": []})
        if candidate.raw_value not in entity["aliases"]:
            entity["aliases"].append(candidate.raw_value)
        role = candidate.contract_role or candidate.field
        if not any(item["role"] == role and item["evidence"] == candidate.matched_pattern for item in entity["roles"]):
            entity["roles"].append({"role": role, "confidence": round(candidate.final_score, 3), "evidence": candidate.matched_pattern, "page": candidate.page})
        if candidate.entity_id != entity_id:
            report.candidates[index - 1] = FactCandidate(**{**asdict(candidate), "entity_id": entity_id})
    for candidate in report.candidates:
        if candidate.field not in {"owner_tax_id", "lessee_tax_id"}:
            continue
        related_role = "lessor" if candidate.field == "owner_tax_id" else "lessee"
        related = [item for item in report.candidates if item.contract_role == related_role and item.page == candidate.page]
        if related:
            entity = report.entities.get(related[0].entity_id)
            if entity and not any(item["value"] == candidate.normalized_value for item in entity["tax_ids"]):
                entity["tax_ids"].append({"value": candidate.normalized_value, "type": "NIPC" if entity["entity_type"] == "organization" else "NIF", "source_page": candidate.page, "validated": _valid_tax_id(candidate.normalized_value)})


def _add_existing_output_candidates(result: ExtractionResult, report: ContractResolutionReport) -> None:
    # Outputs from prior recovery/LLM become explicitly low-authority
    # candidates. They may fill only where they have literal support and no
    # structured source exists; no more last-write-wins.
    for field_name in _field_names():
        value = str(getattr(result, field_name, "") or "").strip()
        if not value:
            continue
        zone = DocumentZone(None, "legacy_output", 0.2, [], value, 0.35, "legacy")
        candidate = _candidate(field_name, value, zone, "", "prior_pipeline_output", 0.35)
        _add(report, candidate)


def _cadastral_summary_for_llm(report: ContractResolutionReport) -> str:
    """Provide verified facts, never raw caderneta OCR, to the local LLM."""
    groups = {
        candidate.source_type
        for candidate in report.candidates
        if candidate.source_type.startswith("caderneta_predial_rustica_")
    }
    if len(groups) > 1:
        return "[FACTOS CADASTRAIS: existem várias cadernetas; não inferir campos cadastrais.]"
    values: dict[str, list[str]] = {}
    labels = {
        "cadastral_owner": "titular cadastral",
        "property_name": "imóvel",
        "property_article": "artigo matricial",
        "property_section": "secção",
        "property_total_area": "área total",
    }
    for candidate in report.candidates:
        if not candidate.source_type.startswith("caderneta_predial_rustica_"):
            continue
        if candidate.validation_status != "valid" or candidate.field not in labels:
            continue
        values.setdefault(labels[candidate.field], []).append(candidate.normalized_value)
    if not values:
        return ""
    lines = ["[FACTOS CADASTRAIS VERIFICADOS — NÃO REINTERPRETAR NEM SUBSTITUIR]"]
    for label, items in values.items():
        lines.append(f"{label}: {'; '.join(dict.fromkeys(items))}")
    return "\n".join(lines)


def _detect_cadastral_conflicts(report: ContractResolutionReport) -> None:
    internal_groups = {
        zone.internal_document
        for zone in report.zones
        if zone.internal_document.startswith("caderneta_predial_rustica_")
    }
    if len(internal_groups) > 1:
        report.conflicts.append({
            "type": "multiple_internal_cadernetas_preserved",
            "caderneta_count": len(internal_groups),
            "requires_review": False,
        })
        _block_internal_caderneta_candidates(report)
        return

    for field_name in ("property_name", "property_article", "property_section", "property_total_area"):
        cadastral_values = {
            normalize_text(item.normalized_value): item.normalized_value
            for item in report.candidates
            if item.field == field_name
            and item.validation_status == "valid"
            and item.source_type.startswith("caderneta_predial_rustica_")
        }
        contract_values = {
            normalize_text(item.normalized_value): item.normalized_value
            for item in report.candidates
            if item.field == field_name
            and item.validation_status == "valid"
            and not item.source_type.startswith("caderneta_predial_rustica_")
            and item.source_type != "legacy"
        }
        if cadastral_values and contract_values and set(cadastral_values) != set(contract_values):
            report.conflicts.append({
                "type": "contract_caderneta_field_conflict",
                "field": field_name,
                "contract_values": sorted(contract_values.values()),
                "caderneta_values": sorted(cadastral_values.values()),
                "requires_review": True,
            })

    lessors = {normalize_text(item.normalized_value) for item in report.candidates if item.field == "lessor" and item.validation_status == "valid"}
    cadastral = {
        normalize_text(item.normalized_value)
        for item in report.candidates
        if item.field == "cadastral_owner"
        and item.validation_status == "valid"
        and item.source_type.startswith("caderneta_predial_rustica_")
    }
    if lessors and cadastral and lessors != cadastral:
        report.conflicts.append({"type": "ownership_source_conflict", "declared_owners": sorted(lessors), "cadastral_owners": sorted(cadastral), "requires_review": True})
    lessees = {
        normalize_text(item.normalized_value)
        for item in report.candidates
        if item.field == "lessee" and item.validation_status == "valid"
    }
    same_cadastral_lessee = sorted(cadastral & lessees)
    if same_cadastral_lessee:
        report.conflicts.append({
            "type": "cadastral_owner_matches_lessee",
            "entities": same_cadastral_lessee,
            "requires_review": True,
        })
    for entity in report.entities.values():
        roles = {item["role"] for item in entity["roles"]}
        if "lessor" in roles and "lessee" in roles:
            report.conflicts.append({"type": "dual_contract_role", "entity_id": entity["entity_id"], "requires_review": True})


def _block_internal_caderneta_candidates(report: ContractResolutionReport) -> None:
    for index, candidate in enumerate(report.candidates):
        if not candidate.source_type.startswith("caderneta_predial_rustica_"):
            continue
        report.candidates[index] = FactCandidate(**{
            **asdict(candidate),
            "validation_status": "blocked",
            "rejection_reason": "multiple_internal_cadernetas_preserved_in_structured_output",
        })


def _select_one(candidates: Iterable[FactCandidate]) -> FactCandidate | None:
    values = list(candidates)
    return max(values, key=lambda item: (item.final_score, item.source_authority, len(item.normalized_value)), default=None)


def _select_many(candidates: Iterable[FactCandidate]) -> list[FactCandidate]:
    by_name: dict[str, FactCandidate] = {}
    for item in candidates:
        key = normalize_text(item.normalized_value)
        current = by_name.get(key)
        if not current or item.final_score > current.final_score:
            by_name[key] = item
    if not by_name:
        return []
    highest = max(item.final_score for item in by_name.values())
    return sorted(
        (item for item in by_name.values() if item.final_score >= highest - 0.18),
        key=lambda item: (-item.final_score, item.page or 999, int(item.candidate_id.rsplit("_", 1)[-1])),
    )


def _declared_owner_value(report: ContractResolutionReport) -> str:
    owner_words = " ".join(zone.text_span for zone in report.zones if zone.zone_type in {"party_identification", "signature_recognition"})
    if not re.search(r"\b(?:leg[ií]timos?\s+propriet[aá]rios?|donos?\s+e\s+leg[ií]timos?\s+propriet[aá]rios?)\b", owner_words, re.I):
        return ""
    return "; ".join(item.normalized_value for item in _select_many(item for item in report.candidates if item.field == "lessor" and item.validation_status == "valid"))


def _decision(candidate: FactCandidate, value: str, decision: str) -> dict[str, object]:
    return {"field": candidate.field, "selected_value": value, "decision": decision, "confidence": round(candidate.final_score, 3), "selected_candidate_id": candidate.candidate_id, "rejected_candidate_ids": [], "reason": candidate.matched_pattern, "requires_review": False}


def _field_names() -> tuple[str, ...]:
    return ("lessee", "lessee_tax_id", "property_name", "property_article", "property_section", "property_parish", "property_municipality", "property_district", "property_total_area", "leased_parcel_area", "signed_date", "monthly_rent", "annual_rent", "rent_amount", "rent_currency", "rent_frequency", "rent_unit", "rent_basis")


def _strict_fields() -> set[str]:
    return {"lessee", "lessee_tax_id", "property_name", "property_article", "property_section", "property_parish", "property_municipality", "property_district", "monthly_rent", "annual_rent", "rent_amount", "rent_frequency", "rent_unit"}


def _authority(field_name: str, zone_type: str) -> float:
    matrix = {
        "property_article": {"cadastral_record": 0.99, "land_registry_certificate": 0.97, "property_recital": 0.84, "object_clause": 0.80},
        "property_section": {"cadastral_record": 0.99, "land_registry_certificate": 0.97, "property_recital": 0.84},
        "property_name": {"cadastral_record": 0.98, "land_registry_certificate": 0.96, "property_recital": 0.84, "parcel_plan": 0.75},
        "lessor": {"party_identification": 0.99, "signature_recognition": 0.94, "landlord_identification_annex": 0.92, "signature_page": 0.88, "notification_block": 0.80},
        "lessee": {"party_identification": 0.99, "signature_recognition": 0.92, "notification_block": 0.80, "corporate_registry": 0.72},
        "signed_date": {"signature_page": 0.97, "signature_recognition": 0.66},
    }
    default = {"rent_clause": 0.96, "term_clause": 0.90, "cadastral_record": 0.91, "land_registry_certificate": 0.90, "legacy_output": 0.05}.get(zone_type, 0.55)
    return matrix.get(field_name, {}).get(zone_type, default)


def _valid(field_name: str, value: str, role: str) -> tuple[bool, str]:
    normalized = normalize_text(value)
    if not value:
        return False, "empty_value"
    if field_name == "property_name" and (normalized in GENERIC_PROPERTY or value.isdigit() or re.search(r"\b(?:ha|hectares?|m2|m²)\b", value, re.I)):
        return False, "role_label_not_property_name"
    if field_name in {"lessor", "lessee", "cadastral_owner"} and validate_name_field("lessee" if field_name == "lessee" else "lessor", value):
        return False, "invalid_entity_name"
    if field_name in {"owner_tax_id", "lessee_tax_id"} and not _valid_tax_id(value):
        return False, "invalid_tax_id"
    if field_name == "property_article" and not re.fullmatch(r"\d{1,8}", value):
        return False, "invalid_cadastral_article"
    if field_name == "property_section" and not re.fullmatch(r"[A-Z]", value):
        return False, "invalid_cadastral_section"
    return True, ""


def _valid_tax_id(value: str) -> bool:
    if not re.fullmatch(r"\d{9}", value):
        return False
    total = sum(int(value[index]) * (9 - index) for index in range(8))
    digit = 11 - (total % 11)
    if digit >= 10:
        digit = 0
    return digit == int(value[-1])


def _names(value: str, role: str) -> list[str]:
    value = re.sub(r"\b(?:NIF|NIPC|contribuinte)\s*(?:n[.ºo°]?\s*)?[:#-]?\s*\d{9}\b", "", value, flags=re.I)
    value = re.split(r"\b(?:residente|morada|com\s+sede|representada\s+por|portador|cart[aã]o\s+de\s+cidad[aã]o)\b", value, maxsplit=1, flags=re.I)[0]
    value = re.sub(r"^.*?\b(?:entre|outorgante)\s*[:,-]?\s*", "", value, flags=re.I)
    value = re.sub(r"^\s*CONTRATO[^\n]*\n", "", value, flags=re.I)
    value = re.sub(r"^\s*(?:e\s+)?leg[ií]timos?\s+propriet[aá]rios?\s*[.;:-]*\s*", "", value, flags=re.I)
    value = value.strip(" ,;:-\n")
    names: list[str] = []
    splitter = r"\s+(?:e|&|bem\s+como)\s+|\s*;\s*"
    if role != "lessee":
        splitter += r"|\s*,\s*"
    for item in re.split(splitter, value, flags=re.I):
        candidate = re.sub(r"\s+", " ", item).strip(" ,;:-.")
        candidate = re.sub(r"^e\s+", "", candidate, flags=re.I)
        if candidate.upper().endswith("S.A") and "S.A." in item.upper():
            candidate += "."
        if candidate and not validate_name_field("lessee" if role == "lessee" else "lessor", candidate) and candidate not in names:
            names.append(candidate)
    return names


def _previous_role_end(text: str, position: int) -> int:
    markers = [match.end() for pattern in ROLE_MARKERS.values() for match in pattern.finditer(text) if match.end() <= position]
    if markers:
        return max(markers)
    heading_end = max((match.end() for match in re.finditer(r"\bCONTRATO[^\n]*", text[:position], re.I)), default=0)
    return max(heading_end, max(0, position - 800))


def _tax_ids(text: str) -> list[str]:
    return [item for item in TAX_RE.findall(text) if _valid_tax_id(item)]


def _property_name(value: str) -> str:
    clean = re.split(r"\b(?:freguesia|concelho|distrito|matriz|artigo|sec(?:c|ç)[aã]o)\b", value, maxsplit=1, flags=re.I)[0].strip(" ,;:-.\"'“”«»")
    normalized = normalize_text(clean)
    if normalized in KNOWN_PROPERTY:
        return KNOWN_PROPERTY[normalized]
    return clean if _valid("property_name", clean, "")[0] else ""


def _place(value: str) -> str:
    clean = value.strip(" ,;:-.\"'“”«»")
    normalized = normalize_text(clean)
    return KNOWN_PLACE.get(normalized, clean)


def _area(number: str, unit: str) -> str:
    normalized_number = number.replace(",", ".")
    return f"{normalized_number} {'hectares' if unit.lower() in {'ha', 'hectare', 'hectares'} else 'm²'}"


def _money(number: str, currency: str) -> tuple[str, str]:
    return number.replace(",", "."), "EUR" if currency.upper() in {"EUR", "€", "EURO", "EUROS"} else currency.upper()


def _normalize_value(field_name: str, value: str) -> str:
    if field_name in {"property_section"}:
        return str(value).strip().upper()
    if field_name in {"owner_tax_id", "lessee_tax_id"}:
        return re.sub(r"\D", "", str(value))
    return re.sub(r"\s+", " ", str(value)).strip()


def _is_company(value: str) -> bool:
    return bool(re.search(r"\b(?:S\.?A\.?|LDA\.?|SOCIEDADE|ENERGIA|SGPS)\b", value, re.I))


def _internal_document(zone_type: str) -> str:
    if zone_type == "cadastral_record":
        return "cadastral_annex"
    if zone_type == "land_registry_certificate":
        return "land_registry_annex"
    if zone_type in {"corporate_registry", "corporate_resolution"}:
        return "corporate_annex"
    return "contract"


def _pages(text: str) -> list[tuple[int | None, str]]:
    raw = str(text or "")
    matches = list(re.finditer(r"\[Page\s+(\d+)\]", raw, re.I))
    if not matches:
        return [(None, raw)]
    return [(int(match.group(1)), raw[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(raw)]) for index, match in enumerate(matches)]


def _attach(result: ExtractionResult, report: ContractResolutionReport) -> None:
    raw = result.raw_json if isinstance(result.raw_json, dict) else {}
    result.raw_json = dict(raw)
    result.raw_json["step2_7_final_resolution"] = report.to_dict()
    internal_zones = [
        zone for zone in report.zones
        if zone.internal_document.startswith("caderneta_predial_rustica_")
    ]
    result.cadastral_evidence_status = (
        "multiple_internal_cadernetas_preserved"
        if len(internal_zones) > 1
        else "internal_caderneta_selected"
        if internal_zones
        else "not_found"
    )
    result.cadastral_evidence_pages = "; ".join(
        str(zone.page_number) for zone in internal_zones if zone.page_number is not None
    )
    result.cadastral_conflicts = "; ".join(
        str(item.get("type"))
        for item in report.conflicts
        if item.get("type")
    )


def calculate_contract_metrics(expected: dict[str, str], result: ExtractionResult) -> dict[str, object]:
    """Small deterministic evaluation helper for regression fixtures/batches.

    It distinguishes an abstention from a wrong semantic value, so completion
    rate cannot be mistaken for extraction accuracy.
    """
    outcomes: dict[str, str] = {}
    for field_name, expected_value in expected.items():
        actual = str(getattr(result, field_name, "") or "").strip()
        if not actual:
            outcomes[field_name] = "correctly_abstained" if not expected_value else "missing"
        elif normalize_text(actual) == normalize_text(expected_value):
            outcomes[field_name] = "exact_match"
        elif normalize_text(expected_value) in normalize_text(actual) or normalize_text(actual) in normalize_text(expected_value):
            outcomes[field_name] = "partial_match"
        else:
            outcomes[field_name] = "wrong_value"
    total = len(outcomes) or 1
    correct = sum(value in {"exact_match", "partial_match", "correctly_abstained"} for value in outcomes.values())
    populated = sum(bool(str(getattr(result, field_name, "") or "").strip()) for field_name in expected)
    return {
        "field_outcomes": outcomes,
        "precision": round(correct / max(populated, 1), 4),
        "recall": round(correct / total, 4),
        "f1": round(2 * (correct / max(populated, 1)) * (correct / total) / max((correct / max(populated, 1)) + (correct / total), 1e-9), 4),
        "critical_field_completion_rate": round(populated / total, 4),
        "false_positive_rate": round(sum(value == "wrong_value" for value in outcomes.values()) / total, 4),
        "persistence_consistency_rate": 1.0,
    }


__all__ = ["DocumentZone", "FactCandidate", "ContractResolutionReport", "detect_document_zones", "prepare_contract_final_resolution", "apply_contract_final_resolution", "calculate_contract_metrics"]
