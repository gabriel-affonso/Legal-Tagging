"""Step 2.7: conservative, clause-scoped extraction for lease contracts.

The output of this module is deliberately auditable.  It does not ask an LLM
to decide who a party is; it first narrows evidence to the relevant contract
clause and then applies deterministic role markers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from ..models import ExtractionResult
from .contract_clause_segmentation import ContractClause, ContractClauseSegmentationReport, segment_contract_clauses
from .name_quality import validate_name_field
from .property_recovery import recover_property_group
from .signature_date import normalize_date
from .validation_rules import normalize_text


LEASE_RE = re.compile(r"\b(?:contrato\s+de\s+arrendamento|arrendat[aá]ri[oa]|senhorios?)\b", re.I)
ROLE_MARKERS = {
    "lessor": re.compile(r"\b(?:promitente\s+)?senhorios?|propriet[aá]rios?\b", re.I),
    "lessee": re.compile(r"\b(?:arrendat[aá]ri[oa]s?|lessee|tenant)\b", re.I),
}
TEMPLATE_MARKERS = {
    "lessor": re.compile(r"\b(?:de\s+ora\s+em\s+diante|doravante|conjuntamente)\s+designad[oa]s?\s+por\s+(?:promitentes?\s+)?senhorios?\b", re.I),
    "lessee": re.compile(r"\b(?:de\s+ora\s+em\s+diante|doravante|conjuntamente)\s+designad[oa]s?\s+por\s+arrendat[aá]ri[oa]s?\b", re.I),
}
TAX_ID_RE = re.compile(r"\b(?:NIF|NIPC|contribuinte)\s*(?:n[.ºo°]?\s*)?[:#-]?\s*(\d{9})\b", re.I)
BAD_LEASE_ENTITIES = ("PASSPORT", "GOVERNMENT", "CANADA", "CITIZENSHIP", "IMMIGRATION")
DATE_RE = r"(?:\d{1,2}[/-]\d{1,2}[/-](?:19|20)\d{2}|(?:19|20)\d{2}-\d{2}-\d{2})"
MONEY_RE = r"((?:\d{1,3}(?:[ .]\d{3})*|\d+)(?:,\d{1,2})?\s*(?:EUR|€|euros?))"


@dataclass(frozen=True)
class FieldCandidate:
    value: str
    source: str
    page: int | None
    score: float
    evidence: str

    def to_dict(self) -> dict[str, object]:
        return {"value": self.value, "source": self.source, "page": self.page, "score": round(self.score, 2), "evidence": self.evidence[:300]}


@dataclass
class PartyCentricReport:
    active: bool = False
    status: str = "NOT_APPLICABLE"
    segmentation: ContractClauseSegmentationReport | None = None
    candidates: dict[str, list[FieldCandidate]] = field(default_factory=dict)
    field_sources: dict[str, dict[str, object]] = field(default_factory=dict)
    blocked_values: list[dict[str, str]] = field(default_factory=list)

    def context_for_llm(self, *, max_chars: int) -> str:
        if not self.active or not self.segmentation:
            return ""
        groups = (
            ("PARTIES — LESSOR (allowed only for lessor/owner)", ("__lessor_window__",)),
            ("PARTIES — LESSEE (allowed only for lessee)", ("__lessee_window__",)),
            ("PROPERTY RECITAL (allowed only for property fields)", ("recital_property", "property")),
            ("TERM (allowed only for contract dates)", ("term",)),
            ("COMMERCIAL CLAUSES (allowed only for prices/rent)", ("rent", "payment", "commercial")),
        )
        chunks: list[str] = []
        used = 0
        for label, types in groups:
            text = (
                self._party_window("lessor" if types == ("__lessor_window__",) else "lessee", max_chars=max_chars)
                if types[0].startswith("__")
                else self.segmentation.text_for_types(*types, max_chars=max_chars)
            )
            if not text:
                continue
            rendered = f"[{label}]\n{text.strip()}"
            if used + len(rendered) > max_chars:
                rendered = rendered[: max(0, max_chars - used)].rstrip()
            if rendered:
                chunks.append(rendered)
                used += len(rendered) + 2
            if used >= max_chars:
                break
        return "\n\n".join(chunks)

    def _party_window(self, role: str, *, max_chars: int) -> str:
        """A role window, not the broader generic `parties` clause."""
        values = self.candidates.get(role, [])
        evidence: list[str] = []
        for item in values:
            clean = item.evidence.strip()
            if clean and clean not in evidence:
                evidence.append(clean)
        if evidence:
            return "\n".join(evidence)[:max_chars]
        # No role marker was found: page 1/2 preamble remains the sole
        # permitted fallback, never rent, signatures, or annexes.
        clauses = [
            clause.text for clause in self.segmentation.clauses
            if clause.page in (None, 1, 2) and clause.clause_type in {"preamble", "title", "parties"}
        ]
        return "\n".join(clauses)[:max_chars]

    def review_context(self, fields: list[str], *, max_chars: int) -> str:
        if not self.segmentation:
            return ""
        mapping = {
            "lessor": ("parties_lessor", "preamble"), "owner_name": ("parties_lessor", "preamble"),
            "lessee": ("parties_lessee", "preamble"), "property_article": ("recital_property", "property"),
            "property_section": ("recital_property", "property"), "signed_date": ("signatures",),
            "monthly_rent": ("rent", "payment", "commercial"),
        }
        wanted: list[str] = []
        for name in fields:
            for clause_type in mapping.get(name, ()):
                if clause_type not in wanted:
                    wanted.append(clause_type)
        chunks: list[str] = []
        if "parties_lessor" in wanted:
            chunks.append(self._party_window("lessor", max_chars=max_chars))
        if "parties_lessee" in wanted:
            chunks.append(self._party_window("lessee", max_chars=max_chars))
        ordinary = tuple(item for item in wanted if not item.startswith("parties_"))
        if ordinary:
            chunks.append(self.segmentation.text_for_types(*ordinary, max_chars=max_chars))
        return "\n\n".join(chunk for chunk in chunks if chunk)[:max_chars]

    def to_dict(self) -> dict[str, object]:
        return {
            "version": "2.7", "status": self.status, "active": self.active,
            "segmentation": self.segmentation.to_dict() if self.segmentation else {},
            "candidates": {name: [item.to_dict() for item in values] for name, values in self.candidates.items()},
            "field_sources": self.field_sources, "blocked_values": self.blocked_values,
        }


def prepare_party_centric_contract(document_text: str) -> PartyCentricReport:
    report = PartyCentricReport()
    if not document_text.strip() or not LEASE_RE.search(document_text[:12000]):
        return report
    report.active = True
    segmentation = segment_contract_clauses(document_text)
    report.segmentation = segmentation
    if segmentation.status != "SEGMENTED":
        report.status = segmentation.status
        return report
    report.status = "SEGMENTED"
    _collect_party_candidates(report)
    _collect_property_candidates(report)
    _collect_term_and_commercial_candidates(report)
    return report


def apply_party_centric_contract_extraction(result: ExtractionResult, report: PartyCentricReport) -> ExtractionResult:
    """Make structure-derived values authoritative for a lease contract.

    Existing values are retained only when their literal evidence is in the
    allowed clause.  This is the guardrail that prevents broad-context OCR or
    LLM noise from becoming an authoritative contract field.
    """
    if not report.active or (result.document_category or "").lower() != "lease_contract":
        _attach(result, report)
        return result

    low_confidence = (result.confidence or "").lower() == "low" or "ocr_quality_low" in (result.ocr_quality_flags or "")
    allowed = {
        "lessor": ("parties_lessor", "preamble"), "owner_name": ("parties_lessor", "preamble"),
        "owner_tax_id": ("parties_lessor", "preamble"), "lessee": ("parties_lessee", "preamble"),
        "lessee_tax_id": ("parties_lessee", "preamble"),
        "property_article": ("recital_property", "property"), "property_section": ("recital_property", "property"),
        "property_parish": ("recital_property", "property"), "property_municipality": ("recital_property", "property"),
        "property_district": ("recital_property", "property"), "contract_start_date": ("term",), "contract_end_date": ("term",),
        "monthly_rent": ("rent", "payment", "commercial"), "option_price": ("commercial", "rent", "payment"),
        "purchase_price": ("commercial", "rent", "payment"), "assignment_price": ("commercial", "rent", "payment"),
    }
    for field_name, types in allowed.items():
        selected = _best(report.candidates.get(field_name, []))
        current = str(getattr(result, field_name, "") or "").strip()
        if selected:
            if field_name in {"lessor", "owner_name"}:
                peers = [item for item in report.candidates.get(field_name, []) if item.score == selected.score]
                value = "; ".join(item.value for item in peers)
                setattr(result, field_name, value)
                report.field_sources[field_name] = {**selected.to_dict(), "value": value, "party_candidates": [item.value for item in peers]}
                if field_name == "lessor" and len(peers) > 1:
                    result.lessor_2 = peers[1].value
                    report.field_sources["lessor_2"] = {**peers[1].to_dict(), "source": "parties_lessor"}
            else:
                setattr(result, field_name, selected.value)
                report.field_sources[field_name] = selected.to_dict()
        elif current and (low_confidence or not _value_is_in_context(current, report, types) or _is_bad_lease_entity(current)):
            setattr(result, field_name, "")
            report.blocked_values.append({"field": field_name, "value": current[:180], "reason": "low_confidence" if low_confidence else "outside_allowed_clause"})
        elif current:
            report.field_sources[field_name] = {"source": "allowed_clause_llm_validated", "page": None, "score": 0.0, "evidence": "literal value found in allowed context"}
    if report.candidates.get("lessor") and not result.owner_name:
        selected = _best(report.candidates["lessor"])
        if selected:
            result.owner_name = selected.value
            report.field_sources["owner_name"] = {**selected.to_dict(), "source": "parties_lessor"}
    _attach(result, report)
    return result


def _collect_party_candidates(report: PartyCentricReport) -> None:
    assert report.segmentation is not None
    for role in ("lessor", "lessee"):
        clauses = report.segmentation.clauses_of_type(f"parties_{role}", "preamble")
        for clause in clauses:
            template_candidates = _template_candidates(clause, role)
            for candidate in template_candidates:
                _add(report, role, candidate)
                if role == "lessor":
                    _add(report, "owner_name", FieldCandidate(candidate.value, "parties_lessor", candidate.page, candidate.score, candidate.evidence))
            if not template_candidates and not report.candidates.get(role):
                for candidate in _label_candidates(clause, role):
                    _add(report, role, candidate)
                    if role == "lessor":
                        _add(report, "owner_name", FieldCandidate(candidate.value, "parties_lessor", candidate.page, candidate.score, candidate.evidence))
            for block, evidence in _template_blocks(clause, role):
                tax_field = "owner_tax_id" if role == "lessor" else "lessee_tax_id"
                source = "parties_lessor" if role == "lessor" else "parties_lessee"
                for tax_id in TAX_ID_RE.findall(block):
                    _add(report, tax_field, FieldCandidate(tax_id, source, clause.page, _page_weight(clause.page), evidence))


def _template_candidates(clause: ContractClause, role: str) -> list[FieldCandidate]:
    values: list[FieldCandidate] = []
    for block, evidence in _template_blocks(clause, role):
        for name in _names_from_block(block, role):
            values.append(FieldCandidate(name, f"parties_{role}_template", clause.page, 1.35 * _page_weight(clause.page), evidence))
    return values


def _template_blocks(clause: ContractClause, role: str) -> list[tuple[str, str]]:
    """Return the local block immediately before each high-reliability marker."""
    all_markers = [match for pattern in TEMPLATE_MARKERS.values() for match in pattern.finditer(clause.text)]
    output: list[tuple[str, str]] = []
    for match in TEMPLATE_MARKERS[role].finditer(clause.text):
        previous_end = max((item.end() for item in all_markers if item.end() <= match.start()), default=0)
        start = max(previous_end, match.start() - 700)
        block = clause.text[start:match.start()]
        evidence = clause.text[max(0, start - 40):match.end() + 60]
        output.append((block, evidence))
    return output


def _label_candidates(clause: ContractClause, role: str) -> list[FieldCandidate]:
    labels = r"(?:primeir[oa]\s+outorgante|senhorios?|propriet[aá]rios?)" if role == "lessor" else r"(?:segund[oa]\s+outorgante|arrendat[aá]ri[oa]s?)"
    values: list[FieldCandidate] = []
    for match in re.finditer(rf"\b{labels}\b\s*[:,-]?\s*(.{{5,360}})", clause.text, re.I):
        for name in _names_from_block(match.group(1), role):
            values.append(FieldCandidate(name, f"parties_{role}", clause.page, _page_weight(clause.page), match.group(0)))
    return values


def _names_from_block(block: str, role: str) -> list[str]:
    text = " ".join(block.split())
    text = re.sub(r"\b(?:NIF|NIPC|contribuinte)\s*(?:n[.ºo°]?\s*)?[:#-]?\s*\d{9}\b", "", text, flags=re.I)
    text = re.split(r"\b(?:residente|morada|com\s+sede|portador|casad[oa]|solteir[oa]|doravante|de\s+ora\s+em\s+diante)\b", text, maxsplit=1, flags=re.I)[0]
    text = re.sub(r"^.*?(?:entre|outorgante)\s*[:,-]?\s*", "", text, flags=re.I)
    text = text.strip(" ,;:-")
    parts = re.split(r"\s+(?:e|&|bem\s+como)\s+|\s*;\s*", text, flags=re.I)
    output: list[str] = []
    for part in parts:
        value = re.sub(r"\s+", " ", part).strip(" ,;:-").lstrip(". ")
        value = re.sub(r"^(?:o|a)\s+", "", value, flags=re.I)
        if value and not _is_bad_lease_entity(value) and not validate_name_field(role, value) and value not in output:
            output.append(value)
    return output


def _collect_property_candidates(report: PartyCentricReport) -> None:
    assert report.segmentation is not None
    for clause in report.segmentation.clauses_of_type("recital_property", "property"):
        group = recover_property_group(clause.text)
        for field_name, value in (("property_article", group.article), ("property_section", group.section)):
            if value:
                _add(report, field_name, FieldCandidate(value, "recital_property", clause.page, _page_weight(clause.page), group.evidence))
        patterns = {
            "property_parish": r"\b(?:freguesia|par[oó]quia)\s+(?:de\s+)?([^,;\n.]{3,100})",
            "property_municipality": r"\b(?:concelho|munic[ií]pio)\s+(?:de\s+)?([^,;\n.]{3,100})",
            "property_district": r"\bdistrito\s+(?:de\s+)?([^,;\n.]{3,100})",
        }
        for field_name, pattern in patterns.items():
            match = re.search(pattern, clause.text, re.I)
            if match:
                value = match.group(1).strip(" ,;:-")
                if value:
                    _add(report, field_name, FieldCandidate(value, "recital_property", clause.page, _page_weight(clause.page), match.group(0)))


def _collect_term_and_commercial_candidates(report: PartyCentricReport) -> None:
    assert report.segmentation is not None
    for clause in report.segmentation.clauses_of_type("term"):
        for field_name, marker in (("contract_start_date", r"(?:in[ií]cio|inicia-se|produz\s+efeitos|come[cç]a)"), ("contract_end_date", r"(?:termo|termina|cessa|fim)")):
            match = re.search(marker + r".{0,100}?(" + DATE_RE + r")", clause.text, re.I)
            if match and (value := normalize_date(match.group(1))):
                _add(report, field_name, FieldCandidate(value, "term_clause", clause.page, _page_weight(clause.page), match.group(0)))
    price_specs = (("monthly_rent", r"(?:renda\s+mensal|renda|mensalidade).{0,100}?" + MONEY_RE), ("option_price", r"(?:op[cç][aã]o(?:\s+de\s+compra)?|pre[cç]o\s+da\s+op[cç][aã]o).{0,100}?" + MONEY_RE), ("purchase_price", r"(?:pre[cç]o\s+(?:de\s+)?compra|compra\s+e\s+venda).{0,100}?" + MONEY_RE), ("assignment_price", r"(?:cess[aã]o|ced[eê]ncia|pre[cç]o\s+de\s+cess[aã]o).{0,100}?" + MONEY_RE))
    for clause in report.segmentation.clauses_of_type("rent", "payment", "commercial"):
        for field_name, pattern in price_specs:
            match = re.search(pattern, clause.text, re.I)
            if match:
                value = re.sub(r"\s+", " ", match.group(1)).replace("€", "EUR").strip()
                _add(report, field_name, FieldCandidate(value, "rent_clause" if field_name == "monthly_rent" else "commercial_clause", clause.page, _page_weight(clause.page), match.group(0)))


def _add(report: PartyCentricReport, field_name: str, candidate: FieldCandidate) -> None:
    current = report.candidates.setdefault(field_name, [])
    key = normalize_text(candidate.value)
    if key and all(normalize_text(item.value) != key for item in current):
        current.append(candidate)


def _best(candidates: list[FieldCandidate]) -> FieldCandidate | None:
    return max(candidates, key=lambda item: (item.score, len(item.value)), default=None)


def _page_weight(page: int | None) -> float:
    if page is None or page <= 1:
        return 1.0
    if page == 2:
        return 0.8
    if page == 3:
        return 0.5
    return 0.2


def _value_is_in_context(value: str, report: PartyCentricReport, types: tuple[str, ...]) -> bool:
    if not report.segmentation:
        return False
    haystack = normalize_text(report.segmentation.text_for_types(*types, max_chars=20000))
    needle = normalize_text(value)
    return bool(needle and needle in haystack)


def _is_bad_lease_entity(value: str) -> bool:
    normalized = normalize_text(value)
    return any(marker in normalized for marker in BAD_LEASE_ENTITIES)


def _attach(result: ExtractionResult, report: PartyCentricReport) -> None:
    raw = result.raw_json if isinstance(result.raw_json, dict) else {}
    result.raw_json = dict(raw)
    result.raw_json["step2_7_party_centric"] = report.to_dict()


__all__ = ["FieldCandidate", "PartyCentricReport", "prepare_party_centric_contract", "apply_party_centric_contract_extraction"]
