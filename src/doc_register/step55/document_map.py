"""Classify observations before field extraction; no model calls occur here."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Observation:
    id: str
    page: int
    text: str
    bbox: tuple[float, float, float, float] | None
    reading_method: str


def classify(text: str) -> tuple[str, str, str]:
    folded = text.casefold()
    if len(folded.strip()) < 12:
        return "unknown", "insufficient text to classify", "low_text"
    if re.search(r"pol[ií]tica\s+de\s+privacidade|regulamento\s+geral\s+de\s+prote[cç][aã]o", folded):
        return "privacy_notice", "privacy marker", "processed"
    if re.search(r"cart[aã]o\s+de\s+cidad[aã]o|bilhete\s+de\s+identidade", folded):
        return "identity_document", "identity marker", "processed"
    if re.search(r"caderneta\s+predial", folded):
        return "cadastral_record", "cadastral heading", "processed"
    if re.search(r"certid[aã]o\s+(?:permanente|predial)|conservat[oó]ria", folded):
        return "land_registry_record", "registry heading", "processed"
    if re.search(r"planta\b|levantamento\s+topogr[aá]fico|escala\s*1\s*:", folded):
        return "site_plan", "site-plan marker", "processed"
    if re.search(r"contrato\s+de\s+arrendamento|primeiro\s+outorgante|condi[cç][aã]o\s+suspensiva", folded):
        return "lease_contract", "contract marker", "processed"
    if re.search(r"envio\s+de\s+recibo|remetemos.*recibo|junto\s+enviamos", folded):
        return "payment_correspondence", "payment correspondence marker", "processed"
    if re.search(r"recibo\s+de\s+renda|retid[oa]\s+na\s+fonte|valor\s+l[ií]quido", folded):
        return "payment_receipt", "receipt/withholding marker", "processed"
    if re.search(r"transfer[eê]ncia\s+(?:efetuada|executada)|data[- ]valor", folded):
        return "bank_payment_confirmation", "bank-confirmation marker", "processed"
    return "unknown", "no supported marker", "processed"


def map_documents(observations: list[Observation]):
    """One logical document per classified observation is conservative by design.

    Adjacent regions with the same type may be grouped only when the pipeline can
    prove their boundary; Step 5.5 keeps regions separate rather than inheriting
    a contract type into RGPD or identity attachments.
    """
    from .canonical import LogicalDocument, stable_id
    output = []
    for observation in observations:
        source_type, reason, state = classify(observation.text)
        output.append(LogicalDocument(
            id="doc-" + stable_id(observation.id, source_type), source_type=source_type,
            region_ids=[observation.id], page_numbers=[observation.page],
            classification_reason=reason, page_state=state,
        ))
    return output
