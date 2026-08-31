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


TAX_RE = re.compile(
    r"\b(?:NIF|NIPC|contribuinte(?:\s+fiscal)?|n[uú]mero\s+de\s+identifica[cç][aã]o\s+de\s+pessoa\s+colectiva)"
    r"\s*(?:n[.ºo°]?\s*)?[:#-]?\s*((?:\d\s*){9})\b",
    re.I,
)
ROLE_DEFINITION_RE = {
    "lessor": re.compile(
        r"\b(?:na\s+qualidade\s+de\s+)?(?:promitentes?\s+)?senhori[oa]s?\b|"
        r"\bdesignad[oa]s?\s+(?:em\s+conjunto\s+)?por\s+[\"“”«»]?(?:senhori[oa]s?)\b",
        re.I,
    ),
    "lessee": re.compile(
        r"\bdesignad[oa]s?\s+por\s+[\"“”«»]?(?:arrendat[aá]ri[oa]s?)\b|"
        r"\bna\s+qualidade\s+de\s+arrendat[aá]ri[oa]s?\b",
        re.I,
    ),
}


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
    tax_id: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"value": self.value, "role": self.role, "source": self.source, "page": self.page, "evidence": self.evidence[:350], "source_score": self.source_score, "valid": self.valid, "validation_reason": self.validation_reason, "tax_id": self.tax_id}


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
    if source == "NOTARIZATION":
        extracted = _notarization_entities(zone.text)
    elif source == "IDENTIFICATION_ANNEX":
        extracted = [("lessor", name, "") for name in _identification_annex_names(zone.text)]
    elif source == "CONTRACT":
        extracted = _contract_role_entities(zone.text)
    else:
        # Signature labels and land-registry narrative corroborate identities
        # already found elsewhere; they are not safe discovery sources.
        extracted = []

    for role, name, tax_id in extracted:
        valid, reason = validate_entity(name)
        evidence = PartyEvidence(
            name, role, canonical_source(source), zone.pages[0] if zone.pages else None,
            zone.text[:900], source_score("lessor" if role == "lessor" else "lessee", source),
            valid, reason, tax_id,
        )
        if any(
            item.role == evidence.role and item.value == evidence.value and item.source == evidence.source
            for item in report.roles
        ):
            continue
        report.roles.append(evidence)
        if valid:
            target = report.companies if _is_company(name) else report.persons
            if name not in target:
                target.append(name)


def _contract_role_entities(text: str) -> list[tuple[str, str, str]]:
    """Extract identities only from explicit party-definition blocks."""
    results: list[tuple[str, str, str]] = []
    for role, marker in ROLE_DEFINITION_RE.items():
        for role_match in marker.finditer(text):
            previous = max(
                (match.end() for pattern in ROLE_DEFINITION_RE.values() for match in pattern.finditer(text, 0, role_match.start())),
                default=0,
            )
            block = text[max(previous, role_match.start() - 5000):role_match.start()]
            for name, tax_id in _identities_in_block(block):
                _append_unique(results, (role, name, tax_id))
    return results


def _identities_in_block(block: str) -> list[tuple[str, str]]:
    identities: list[tuple[str, str]] = []
    # Corporate/public bodies have a stable name immediately before their
    # fiscal or registered-office details.  Representatives are deliberately
    # excluded because they occur after ``representada por``.
    organization = re.compile(
        r"(?im)(?:^|\n|\bE\s+)[ \t]*(?P<name>[A-ZÀ-ÖØ-Þ][A-ZÀ-ÖØ-Þ0-9 .,&'’\-/]{3,120}?)\s*,\s*"
        r"(?P<details>(?:com\s+sede|contribuinte\s+fiscal|NIPC|n[uú]mero\s+de\s+identifica[cç][aã]o))"
    )
    for match in organization.finditer(block):
        name = _clean_entity_name(match.group("name"))
        tail = block[match.end("name"):min(len(block), match.end("name") + 500)]
        tax_match = TAX_RE.search(tail)
        if name:
            identities.append((name, re.sub(r"\s", "", tax_match.group(1)) if tax_match else ""))

    # Individual templates put the name immediately before an identity label.
    person = re.compile(
        r"(?im)(?:^|\n|\bENTRE\s*:\s*|\s+e\s+)[ \t]*"
        r"(?P<name>[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+(?:\s+[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+){1,7})\s*,?\s*"
        r"(?P<label>NIF|contribuinte\s+fiscal|natural\s+de|portador)\b"
    )
    for match in person.finditer(block):
        name = _clean_entity_name(match.group("name"))
        tail = block[match.end("name"):min(len(block), match.end("name") + 300)]
        tax_match = TAX_RE.search(tail)
        if name:
            identities.append((name, re.sub(r"\s", "", tax_match.group(1)) if tax_match else ""))
    return list(dict.fromkeys(identities))


def _notarization_entities(text: str) -> list[tuple[str, str, str]]:
    results: list[tuple[str, str, str]] = []
    lessor_end = re.search(r"\btodos?\s+na\s+qualidade\s+de\s+senhori[oa]s?\b", text, re.I)
    if lessor_end:
        scope = text[:lessor_end.start()]
        start = max(scope.lower().rfind("contrato de arrendamento"), 0)
        scope = scope[start:]
        for match in re.finditer(
            r"(?:,\s*(?:e\s+)?de\s+|\be\s+de\s+)([A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+(?:\s+(?:da|das|de|do|dos|e|[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+)){1,8})\s*,\s*"
            r"(?:titular|casad[oa]|portador)",
            scope,
            re.I,
        ):
            _append_unique(results, ("lessor", _clean_entity_name(match.group(1)), ""))
    return [item for item in results if item[1]]


def _identification_annex_names(text: str) -> list[str]:
    names: list[str] = []
    lines = [re.sub(r"\s+", " ", line).strip(" []|_") for line in text.splitlines()]
    # Many Portuguese identity-card OCR layers preserve surname(s) and given
    # names as two consecutive uppercase lines.
    for first, second in zip(lines, lines[1:]):
        if _uppercase_name_line(first, min_words=2) and _uppercase_name_line(second, min_words=1):
            candidate = _title_name(f"{second} {first}")
            if validate_entity(candidate)[0] and candidate not in names:
                names.append(candidate)
    # Machine-readable zone: SURNAME<<GIVEN<NAMES.  It is especially useful
    # when the visual name is handwritten or split unpredictably.
    for match in re.finditer(r"(?m)^([A-Z]{3,}(?:<[A-Z]{2,})*)<<([A-Z]{3,}(?:<[A-Z]{2,})*)", text):
        surname = match.group(1).replace("<", " ")
        given = match.group(2).replace("<", " ")
        candidate = _title_name(f"{given} {surname}")
        candidate_tokens = set(_ascii(candidate).split())
        if any(len(candidate_tokens & set(_ascii(existing).split())) >= 2 for existing in names):
            continue
        if validate_entity(candidate)[0] and candidate not in names:
            names.append(candidate)
    return names


def _uppercase_name_line(value: str, *, min_words: int) -> bool:
    if not re.fullmatch(r"[A-ZÀ-ÖØ-Þ][A-ZÀ-ÖØ-Þ'’\-]*(?:\s+[A-ZÀ-ÖØ-Þ][A-ZÀ-ÖØ-Þ'’\-]*){0,5}", value):
        return False
    words = value.split()
    blocked = {"ANEXO", "IDENTIFICACAO", "SENHORIO", "SENHORIOS", "PORTUGAL", "NOME", "CONTRATO"}
    return len(words) >= min_words and not any(_ascii(word) in blocked for word in words)


def _title_name(value: str) -> str:
    particles = {"DA", "DAS", "DE", "DO", "DOS", "E"}
    return " ".join(word.lower() if word in particles else word.capitalize() for word in value.split())


def _clean_entity_name(value: str) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip(" ,;:-.\n")
    value = re.sub(r"^(?:E|ENTRE)\s+", "", value, flags=re.I)
    if re.search(r",\s*S\.A$", value, re.I):
        value += "."
    return value


def _append_unique(items: list[tuple[str, str, str]], value: tuple[str, str, str]) -> None:
    if value not in items:
        items.append(value)


def _ascii(value: str) -> str:
    import unicodedata
    return "".join(char for char in unicodedata.normalize("NFKD", value.upper()) if not unicodedata.combining(char))


def _is_company(value: str) -> bool:
    return bool(re.search(r"\b(?:S\.?A\.?|LDA\.?|ENERGIA|SGPS|SOCIEDADE)\b", value, re.I))


__all__ = ["PartyEvidence", "PartyExtractionReport", "extract_parties"]
