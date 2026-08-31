"""Step 3.3 document structure detection.

The extractor works with small, named evidence zones instead of treating a PDF
as a single bag of text.  Page markers are retained when present so every
decision can remain auditable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re


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

    boundary = re.search(r"\bConsiderando\s+que\s*:", whole, re.I)
    if not boundary:
        boundary = re.search(r"\bCl[aá]usula\s+1\b", whole, re.I)
    preamble = whole[:boundary.start()] if boundary else whole[:6000]
    heading = re.search(r"(?m)^\s*ENTRE\s*:?", preamble, re.I)
    party = preamble[heading.end():] if heading else preamble
    if not re.search(r"CONTRATO\s+DE\s+ARRENDAMENTO\b", preamble, re.I):
        party = ""
    if party:
        zones.append(_zone(PARTY_SECTION, party, pages, ("ENTRE", "Considerando que")))
    property_text = _between(whole, r"\bConsiderando\s+que\s*:", r"\bCl[aá]usula\s+1\b")
    if property_text:
        zones.append(_zone(PROPERTY_SECTION, property_text, pages, ("Considerando que", "Cláusula 1")))
    financial = _between(whole, r"\bCl[aá]usula\s+5\b", r"\bCl[aá]usula\s+6\b")
    if financial:
        zones.append(_zone(FINANCIAL_SECTION, financial, pages, ("Cláusula 5", "Cláusula 6")))

    for page_index, (number, page) in enumerate(pages):
        normalized = _fold(page)
        signals: list[str] = []
        zone_type = ""
        if _is_caderneta_page(normalized):
            zone_type, signals = ANNEX_CADERNETA, ["caderneta_predial"]
        elif _is_notarization_page(normalized):
            zone_type, signals = NOTARIZATION_SECTION, ["notarization"]
        elif _is_crp_page(normalized):
            zone_type, signals = ANNEX_CRP, ["registo_predial"]
        elif _is_identification_heading(normalized):
            # The useful identity data is commonly on the pages immediately
            # after the annex cover.  Keep those pages together and stop at a
            # new annex or an unrelated bank/cadastral document.
            grouped = [page]
            for _, following in pages[page_index + 1:page_index + 4]:
                following_folded = _fold(following)
                if _starts_unrelated_annex(following_folded):
                    break
                grouped.append(following)
            zones.append(EvidenceZone(
                ANNEX_IDENTIFICATION,
                "\n".join(grouped).strip(),
                tuple(item[0] for item in pages[page_index:page_index + len(grouped)]),
                ("identification_annex",),
            ))
            continue
        elif _is_explicit_signature_page(normalized):
            zone_type, signals = SIGNATURE_SECTION, ["signature_page"]
        if zone_type:
            zones.append(EvidenceZone(zone_type, page.strip(), (number,), tuple(signals)))

    # Older templates do not always number clauses consistently.  A page with
    # an explicit commercial label is still a safe financial zone; this avoids
    # letting a broad LLM value publish a rent or purchase price.
    if not any(zone.zone_type == FINANCIAL_SECTION for zone in zones):
        for number, page in pages:
            if re.search(r"\b(?:renda|contrapartida|pre[cç]o\s+de\s+compra|op[cç][aã]o\s+de\s+compra|cess[aã]o|ced[eê]ncia)\b", page, re.I):
                zones.append(EvidenceZone(FINANCIAL_SECTION, page.strip(), (number,), ("commercial_page_fallback",)))

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


def _is_caderneta_page(normalized: str) -> bool:
    has_title = bool(re.search(
        r"\b(?:actualizacao\s+(?:de\s+)?)?caderneta\s+predial\s+(?:rustica|urbana)\b",
        normalized,
    ))
    has_structure = all(marker in normalized for marker in (
        "identificacao do predio", "artigo matricial", "elementos do predio",
    ))
    return has_title or (has_structure and "titulares" in normalized)


def _is_notarization_page(normalized: str) -> bool:
    return bool(re.search(
        r"\b(?:reconhecimento\b.{0,80}\b(?:assinatura|mencoes)|reconheco\s+as?\s+assinaturas?|notarial)\b",
        normalized,
        re.S,
    ))


def _is_crp_page(normalized: str) -> bool:
    # A recital that merely says the property is described at a Conservatory
    # is contract evidence, not a land-registry certificate.
    return bool(re.search(
        r"\b(?:certidao\s+(?:permanente\s+)?do\s+registo\s+predial|"
        r"descricoes\s*-\s*averbamentos\s*-\s*anotacoes|"
        r"informacao\s+em\s+vigor)\b",
        normalized,
    ))


def _is_identification_heading(normalized: str) -> bool:
    return len(normalized) <= 500 and bool(re.search(
        r"\banexo\s+[ivxlcdm0-9]+\b.{0,120}\bidentificacao\s+(?:do|dos|da|das)\s+senhori[oa]s?\b",
        normalized,
        re.S,
    ))


def _starts_unrelated_annex(normalized: str) -> bool:
    return any(marker in normalized for marker in (
        "caderneta predial", "consultas de nib", "iban", "declaracao de protecao de dados",
        "certidao permanente", "planta com a area", "reconhecimento de assinatura",
        "reconhecimento mencoes", "registo online dos actos",
    ))


def _is_explicit_signature_page(normalized: str) -> bool:
    # Contract signatures are often followed by dozens of annex pages, so
    # their physical distance from the end of the PDF is not evidence.
    return bool(
        re.search(r"\belaborado\s+e\s+assinado\b", normalized)
        or re.search(r"\b(?:senhorios?|senhoria)\b.{0,500}\barrendataria\b", normalized, re.S)
        and any(marker in normalized for marker in ("segue-se pagina de assinaturas", "assinado", "assinatura"))
    )


def _fold(value: str) -> str:
    import unicodedata
    return "".join(char for char in unicodedata.normalize("NFKD", value.lower()) if not unicodedata.combining(char))


__all__ = ["DocumentStructure", "EvidenceZone", "detect_document_structure", "PARTY_SECTION", "PROPERTY_SECTION", "FINANCIAL_SECTION", "SIGNATURE_SECTION", "ANNEX_CADERNETA", "ANNEX_CRP", "ANNEX_IDENTIFICATION", "NOTARIZATION_SECTION"]
