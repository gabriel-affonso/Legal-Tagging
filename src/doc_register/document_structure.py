"""Step 3.3 document structure detection.

The extractor works with small, named evidence zones instead of treating a PDF
as a single bag of text.  Page markers are retained when present so every
decision can remain auditable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Iterable


PARTY_SECTION = "PARTY_SECTION"
PROPERTY_SECTION = "PROPERTY_SECTION"
FINANCIAL_SECTION = "FINANCIAL_SECTION"
SIGNATURE_SECTION = "SIGNATURE_SECTION"
ANNEX_CADERNETA = "ANNEX_CADERNETA"
ANNEX_CRP = "ANNEX_CRP"
ANNEX_IDENTIFICATION = "ANNEX_IDENTIFICATION"
NOTARIZATION_SECTION = "NOTARIZATION_SECTION"


@dataclass(frozen=True)
class EvidenceZone:
    zone_type: str
    text: str
    pages: tuple[int | None, ...]
    signals: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "zone_type": self.zone_type,
            "pages": list(self.pages),
            "signals": list(self.signals),
            "text": self.text[:900],
        }


@dataclass
class DocumentStructure:
    zones: list[EvidenceZone] = field(default_factory=list)

    def of_type(self, *zone_types: str) -> list[EvidenceZone]:
        wanted = set(zone_types)
        return [zone for zone in self.zones if zone.zone_type in wanted]

    def context(self, *zone_types: str, max_chars: int = 8000) -> str:
        chunks: list[str] = []
        remaining = max_chars
        for zone in self.of_type(*zone_types):
            rendered = f"[{zone.zone_type}]\n{zone.text.strip()}"
            rendered = rendered[:remaining].rstrip()
            if rendered:
                chunks.append(rendered)
                remaining -= len(rendered) + 2
            if remaining <= 0:
                break
        return "\n\n".join(chunks)

    def to_dict(self) -> dict[str, object]:
        return {"version": "3.3", "zones": [zone.to_dict() for zone in self.zones]}


def detect_document_structure(document_text: str) -> DocumentStructure:
    """Detect contract and annex zones using conservative Portuguese markers."""
    pages = _pages(document_text)
    zones: list[EvidenceZone] = []
    whole = "\n".join(text for _, text in pages)
    if not whole.strip():
        return DocumentStructure()

    party = _between(whole, r"\bENTRE\s*:?", r"\bConsiderando\s+que\s*:", fallback_end=r"\bCl[aá]usula\s+1\b")
    if party:
        zones.append(_zone(PARTY_SECTION, party, pages, ("ENTRE", "Considerando que")))
    property_text = _between(whole, r"\bConsiderando\s+que\s*:", r"\bCl[aá]usula\s+1\b")
    if property_text:
        zones.append(_zone(PROPERTY_SECTION, property_text, pages, ("Considerando que", "Cláusula 1")))
    financial = _between(whole, r"\bCl[aá]usula\s+5\b", r"\bCl[aá]usula\s+6\b")
    if financial:
        zones.append(_zone(FINANCIAL_SECTION, financial, pages, ("Cláusula 5", "Cláusula 6")))

    for number, page in pages:
        normalized = _fold(page)
        signals: list[str] = []
        zone_type = ""
        if "caderneta predial" in normalized or ("identificacao do predio" in normalized and "artigo matricial" in normalized):
            zone_type, signals = ANNEX_CADERNETA, ["caderneta_predial"]
        elif "certidao do registo predial" in normalized or "conservatoria do registo predial" in normalized:
            zone_type, signals = ANNEX_CRP, ["registo_predial"]
        elif any(marker in normalized for marker in ("identificacao do senhorio", "cartao de cidadao", "bilhete de identidade")):
            zone_type, signals = ANNEX_IDENTIFICATION, ["identification_annex"]
        elif any(marker in normalized for marker in ("reconheco a assinatura", "reconhecimento de assinatura", "notario", "notarial")):
            zone_type, signals = NOTARIZATION_SECTION, ["notarization"]
        elif any(marker in normalized for marker in ("assinado", "assinatura", "senhorio", "arrendataria")) and _is_signature_page(number, pages):
            zone_type, signals = SIGNATURE_SECTION, ["signature_page"]
        if zone_type:
            zones.append(EvidenceZone(zone_type, page.strip(), (number,), tuple(signals)))

    # Some short contracts omit formal boundary words.  Their first page is a
    # party-only fallback; it is never used for cadastral fields.
    if not any(zone.zone_type == PARTY_SECTION for zone in zones):
        first_number, first_page = pages[0]
        if re.search(r"\b(?:senhorio|arrendat[aá]ri[oa]|outorgante)\b", first_page, re.I):
            zones.append(EvidenceZone(PARTY_SECTION, first_page.strip(), (first_number,), ("first_page_party_fallback",)))
    return DocumentStructure(zones)


def _zone(kind: str, text: str, pages: list[tuple[int | None, str]], signals: tuple[str, ...]) -> EvidenceZone:
    normalized_zone = _fold(text)
    matched_pages: list[int | None] = []
    for number, page in pages:
        lines = [line.strip() for line in page.splitlines() if len(line.strip()) >= 12]
        if any(_fold(line) in normalized_zone for line in lines):
            matched_pages.append(number)
    return EvidenceZone(kind, text.strip(), tuple(matched_pages), signals)


def _between(text: str, start: str, end: str, *, fallback_end: str | None = None) -> str:
    first = re.search(start, text, re.I)
    if not first:
        return ""
    tail = text[first.end():]
    last = re.search(end, tail, re.I)
    if not last and fallback_end:
        last = re.search(fallback_end, tail, re.I)
    return tail[:last.start()] if last else tail[:2500]


def _pages(text: str) -> list[tuple[int | None, str]]:
    matches = list(re.finditer(r"\[Page\s+(\d+)\]", str(text or ""), re.I))
    if not matches:
        return [(None, str(text or ""))]
    return [(int(match.group(1)), text[match.end(): matches[index + 1].start() if index + 1 < len(matches) else len(text)]) for index, match in enumerate(matches)]


def _is_signature_page(number: int | None, pages: Iterable[tuple[int | None, str]]) -> bool:
    page_numbers = [item for item, _ in pages if item is not None]
    return number is None or not page_numbers or number >= max(page_numbers) - 2


def _fold(value: str) -> str:
    import unicodedata
    return "".join(char for char in unicodedata.normalize("NFKD", value.lower()) if not unicodedata.combining(char))


__all__ = ["DocumentStructure", "EvidenceZone", "detect_document_structure", "PARTY_SECTION", "PROPERTY_SECTION", "FINANCIAL_SECTION", "SIGNATURE_SECTION", "ANNEX_CADERNETA", "ANNEX_CRP", "ANNEX_IDENTIFICATION", "NOTARIZATION_SECTION"]
