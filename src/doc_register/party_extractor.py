"""Evidence-zone party extraction for Step 3.3.

This module intentionally returns entities and role evidence separately.  It
does not ask a model a document-wide "who is the lessor?" question.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from .document_structure import ANNEX_CRP, ANNEX_IDENTIFICATION, NOTARIZATION_SECTION, PARTY_SECTION, SIGNATURE_SECTION, DocumentStructure, EvidenceZone
from .entity_validator import validate_entity
from .source_ranking import canonical_source, source_score


ROLE_PATTERNS = {
    "lessor": re.compile(r"\b(?:senhorios?|locadores?|propriet[aá]rios?)\b", re.I),
    "lessee": re.compile(r"\b(?:arrendat[aá]ri[oa]s?|locat[aá]ri[oa]s?)\b", re.I),
}
TAX_RE = re.compile(r"\b(?:NIF|NIPC|contribuinte)\s*(?:n[.ºo°]?\s*)?[:#-]?\s*(\d{9})\b", re.I)


@dataclass(frozen=True)
class PartyEvidence:
    value: str
    role: str
    source: str
    page: int | None
    evidence: str
    source_score: int
    valid: bool
    validation_reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"value": self.value, "role": self.role, "source": self.source, "page": self.page, "evidence": self.evidence[:350], "source_score": self.source_score, "valid": self.valid, "validation_reason": self.validation_reason}


@dataclass
class PartyExtractionReport:
    persons: list[str] = field(default_factory=list)
    companies: list[str] = field(default_factory=list)
    roles: list[PartyEvidence] = field(default_factory=list)

    def candidates_for(self, role: str) -> list[PartyEvidence]:
        return [item for item in self.roles if item.role == role and item.valid]

    def to_dict(self) -> dict[str, object]:
        return {"persons": self.persons, "companies": self.companies, "roles": [item.to_dict() for item in self.roles]}


def extract_parties(structure: DocumentStructure) -> PartyExtractionReport:
    report = PartyExtractionReport()
    # Ordered sources implement the multi-evidence resolver's authority.
    sources = (
        (ANNEX_IDENTIFICATION, "IDENTIFICATION_ANNEX"),
        (NOTARIZATION_SECTION, "NOTARIZATION"),
        (PARTY_SECTION, "CONTRACT"),
        (SIGNATURE_SECTION, "SIGNATURE"),
        (ANNEX_CRP, "CRP"),
    )
    for zone_type, source in sources:
        for zone in structure.of_type(zone_type):
            _extract_zone(zone, source, report)
    return report


def _extract_zone(zone: EvidenceZone, source: str, report: PartyExtractionReport) -> None:
    for role, marker in ROLE_PATTERNS.items():
        for match in marker.finditer(zone.text):
            start = max(0, zone.text.rfind("\n", max(0, match.start() - 700), match.start()))
            block = zone.text[start:match.start()]
            for name in _entities_from_block(block, role):
                valid, reason = validate_entity(name)
                evidence = PartyEvidence(name, role, canonical_source(source), zone.pages[0] if zone.pages else None, _compact_evidence(block, match.group(0)), source_score("lessor" if role == "lessor" else "lessee", source), valid, reason)
                report.roles.append(evidence)
                if valid:
                    target = report.companies if _is_company(name) else report.persons
                    if name not in target:
                        target.append(name)


def _entities_from_block(block: str, role: str) -> list[str]:
    cleaned = TAX_RE.sub("", block)
    cleaned = re.sub(r"^\s*CONTRATO[^\n]*\n", "", cleaned, flags=re.I)
    cleaned = re.split(r"\b(?:residente|morada|com\s+sede|representad[oa]\s+por|portador|cart[aã]o)\b", cleaned, maxsplit=1, flags=re.I)[0]
    cleaned = re.split(r"\b(?:todos?\s+na\s+qualidade|de\s+ora\s+em\s+diante|doravante)\b", cleaned, maxsplit=1, flags=re.I)[0]
    cleaned = re.sub(r"^.*?\b(?:entre|outorgantes?)\s*[:,-]?\s*", "", cleaned, flags=re.I | re.S)
    cleaned = cleaned.strip(" ,;:-\n")
    splitter = r"\s+(?:e|&|bem\s+como)\s+|\s*;\s*"
    if role == "lessor":
        splitter += r"|\s*,\s*(?=[A-ZÀ-ÖØ-Þ])"
    candidates: list[str] = []
    for value in re.split(splitter, cleaned, flags=re.I):
        value = re.sub(r"\s+", " ", value).strip(" ,;:-")
        value = re.sub(r"^(?:e\s+)?(?:o\s+)?", "", value, flags=re.I)
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def _compact_evidence(block: str, role_word: str) -> str:
    return f"{block.strip()[-280:]} {role_word}".strip()


def _is_company(value: str) -> bool:
    return bool(re.search(r"\b(?:S\.?A\.?|LDA\.?|ENERGIA|SGPS|SOCIEDADE)\b", value, re.I))


__all__ = ["PartyEvidence", "PartyExtractionReport", "extract_parties"]
