"""Ownership sanity checks: a tenant cannot become the cadastral owner."""
from __future__ import annotations

import re
import unicodedata

from .entity_validator import validate_entity


def validate_ownership(*, owner_name: str, owner_tax_id: str, lessee_name: str, lessee_tax_id: str, evidence_sources: set[str]) -> list[str]:
    issues: list[str] = []
    if owner_name and not validate_entity(owner_name)[0]:
        issues.append("invalid_owner_name")
    if owner_name and lessee_name and _normal(owner_name) == _normal(lessee_name):
        issues.append("owner_name_matches_lessee")
    if owner_tax_id and lessee_tax_id and re.sub(r"\D", "", owner_tax_id) == re.sub(r"\D", "", lessee_tax_id):
        issues.append("owner_tax_id_matches_lessee")
    if owner_name and not (evidence_sources & {"CADERNETA", "CRP", "CONTRACT", "IDENTIFICATION_ANNEX"}):
        issues.append("owner_without_authorized_evidence")
    return issues


def _normal(value: str) -> str:
    plain = "".join(char for char in unicodedata.normalize("NFKD", value) if not unicodedata.combining(char))
    return re.sub(r"\W+", "", plain).upper()


__all__ = ["validate_ownership"]
