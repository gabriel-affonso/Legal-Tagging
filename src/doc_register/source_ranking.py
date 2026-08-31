"""Field-level source authority for Step 3.3."""
from __future__ import annotations

from dataclasses import dataclass


OWNERSHIP_FIELDS = frozenset({"owner_name", "owner_tax_id", "property_article", "property_section", "property_parish", "property_name", "property_total_area"})
CONTRACT_FIELDS = frozenset({"rent_amount", "monthly_rent", "annual_rent", "rent_frequency", "rent_unit", "rent_basis", "contract_start_date", "contract_end_date", "rent_payment_day", "option_price", "purchase_price", "assignment_price"})
SIGNATURE_FIELDS = frozenset({"signed_date"})
PARTY_FIELDS = frozenset({"lessor", "lessee", "lessee_tax_id"})

SOURCE_ALIASES = {
    "caderneta": "CADERNETA", "caderneta_predial": "CADERNETA", "annex_caderneta": "CADERNETA",
    "crp": "CRP", "annex_crp": "CRP", "contract": "CONTRACT", "party_section": "CONTRACT",
    "signature": "SIGNATURE", "notarization": "NOTARIZATION", "ocr_residual": "OCR_RESIDUAL", "legacy": "OCR_RESIDUAL",
}


@dataclass(frozen=True)
class RankedValue:
    field: str
    value: str
    source: str
    source_score: int
    valid: bool = True


def canonical_source(source: str) -> str:
    return SOURCE_ALIASES.get(str(source or "").strip().lower(), str(source or "").strip().upper() or "OCR_RESIDUAL")


def source_score(field: str, source: str) -> int:
    source = canonical_source(source)
    if field in PARTY_FIELDS:
        return {"NOTARIZATION": 100, "IDENTIFICATION_ANNEX": 95, "CONTRACT": 90, "SIGNATURE": 85, "CRP": 75, "OCR_RESIDUAL": 10}.get(source, 10)
    if field in OWNERSHIP_FIELDS:
        return {"CADERNETA": 100, "CRP": 90, "CONTRACT": 50, "OCR_RESIDUAL": 10}.get(source, 10)
    if field in CONTRACT_FIELDS:
        return {"CONTRACT": 100, "SIGNATURE": 80, "NOTARIZATION": 75, "CADERNETA": 10, "CRP": 10, "OCR_RESIDUAL": 10}.get(source, 10)
    if field in SIGNATURE_FIELDS:
        return {"SIGNATURE": 100, "NOTARIZATION": 95, "CONTRACT": 50, "OCR_RESIDUAL": 10}.get(source, 10)
    return {"CADERNETA": 100, "CRP": 90, "CONTRACT": 50, "SIGNATURE": 90, "NOTARIZATION": 95, "OCR_RESIDUAL": 10}.get(source, 10)


def select_ranked(field: str, candidates: list[RankedValue]) -> RankedValue | None:
    valid = [item for item in candidates if item.valid and item.value.strip()]
    if not valid:
        return None
    # A verified cadastral value is a hard source lock for property ownership.
    if field in OWNERSHIP_FIELDS:
        caderneta = [item for item in valid if canonical_source(item.source) == "CADERNETA"]
        if caderneta:
            return max(caderneta, key=lambda item: (item.source_score, len(item.value)))
    return max(valid, key=lambda item: (item.source_score, len(item.value)))


__all__ = ["RankedValue", "canonical_source", "source_score", "select_ranked", "OWNERSHIP_FIELDS"]
