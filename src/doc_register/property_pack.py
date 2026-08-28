"""Find a contract's caderneta when related PDFs live in the same folder."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re
from typing import Iterable

from .pdf_text import extract_pdf_text
from .property_intelligence import CadernetaValues, discover_caderneta_pages, parse_caderneta
from .property_pipeline import PropertyExtraction, is_valid_matrix_article


LOGGER = logging.getLogger(__name__)
MATCH_THRESHOLD = 85


@dataclass(frozen=True)
class PropertyDocumentDescriptor:
    path: Path
    identifiers: frozenset[str]
    caderneta_values: CadernetaValues | None = None

    @property
    def is_caderneta(self) -> bool:
        return self.caderneta_values is not None


@dataclass(frozen=True)
class PropertyPackMatch:
    status: str
    score: int = 0
    methods: tuple[str, ...] = ()
    candidate_count: int = 0
    source_path: Path | None = None
    caderneta_values: CadernetaValues | None = None

    def audit(self) -> dict[str, object]:
        return {
            "property_pack_status": self.status,
            "property_pack_match_score": self.score,
            "property_pack_match_method": ";".join(self.methods),
            "property_pack_candidate_count": self.candidate_count,
            "caderneta_source_file": self.source_path.name if self.source_path else "",
        }


class PropertyPackDiscovery:
    """Build a local deterministic catalogue once per property scan."""

    def __init__(self, paths: Iterable[Path]):
        self._paths = tuple(paths)
        self._descriptors: tuple[PropertyDocumentDescriptor, ...] | None = None

    def find_for_contract(
        self,
        contract_path: Path,
        contract_text: str,
        contract: PropertyExtraction,
    ) -> PropertyPackMatch:
        descriptors = self._catalogue()
        contract_ids = _identifiers(contract_path.name, contract_text, contract.matrix_article)
        property_tokens = _property_tokens(contract.property_name)
        candidates: list[tuple[int, tuple[str, ...], PropertyDocumentDescriptor]] = []

        for descriptor in descriptors:
            if descriptor.path == contract_path or not descriptor.is_caderneta:
                continue
            score, methods = _match_score(contract_ids, property_tokens, contract, descriptor)
            if score:
                candidates.append((score, methods, descriptor))

        if not candidates:
            return PropertyPackMatch(status="property_pack_not_found")
        candidates.sort(key=lambda item: item[0], reverse=True)
        top_score, top_methods, top = candidates[0]
        tied = [item for item in candidates if item[0] == top_score]
        if len(tied) > 1:
            return PropertyPackMatch(
                status="property_pack_ambiguous",
                score=top_score,
                candidate_count=len(candidates),
            )
        if top_score < MATCH_THRESHOLD:
            return PropertyPackMatch(
                status="property_pack_low_confidence",
                score=top_score,
                methods=top_methods,
                candidate_count=len(candidates),
                source_path=top.path,
            )
        return PropertyPackMatch(
            status="property_pack_matched",
            score=top_score,
            methods=top_methods,
            candidate_count=len(candidates),
            source_path=top.path,
            caderneta_values=top.caderneta_values,
        )

    def _catalogue(self) -> tuple[PropertyDocumentDescriptor, ...]:
        if self._descriptors is not None:
            return self._descriptors
        descriptors: list[PropertyDocumentDescriptor] = []
        for path in self._paths:
            try:
                text = extract_pdf_text(path, max_pages=3, max_chars=6000, preserve_layout=True)
                pages = discover_caderneta_pages(text)
                values = parse_caderneta(pages) if pages else None
                descriptors.append(PropertyDocumentDescriptor(
                    path=path,
                    identifiers=frozenset(_identifiers(path.name, text, values.matrix_article if values else "")),
                    caderneta_values=values,
                ))
            except Exception as exc:
                LOGGER.warning("Could not index property-pack candidate %s: %s", path.name, exc)
        self._descriptors = tuple(descriptors)
        return self._descriptors


def _match_score(
    contract_ids: set[str],
    property_tokens: set[str],
    contract: PropertyExtraction,
    descriptor: PropertyDocumentDescriptor,
) -> tuple[int, tuple[str, ...]]:
    caderneta = descriptor.caderneta_values
    if not caderneta:
        return 0, ()
    score = 0
    methods: list[str] = []
    strong_ids = {identifier for identifier in contract_ids & set(descriptor.identifiers) if _is_strong_identifier(identifier)}
    if strong_ids:
        score += 100
        methods.append("exact_property_identifier")
    if contract.matrix_article and contract.matrix_article == caderneta.matrix_article:
        score += 40
        methods.append("matrix_article")
    if contract.matrix_section and contract.matrix_section == caderneta.matrix_section:
        score += 20
        methods.append("matrix_section")
    caderneta_tokens = _property_tokens(caderneta.property_name)
    if property_tokens and caderneta_tokens and property_tokens & caderneta_tokens:
        score += 15
        methods.append("property_name_token")
    return score, tuple(methods)


def _identifiers(file_name: str, text: str, matrix_article: str) -> set[str]:
    source = re.sub(r"[_\-]", " ", f"{file_name}\n{text}").upper()
    identifiers = {
        f"{prefix}{number}"
        for prefix, number in re.findall(r"\b(VA|PR)[_\-\s]*(\d{1,6}[A-Z]?)\b", source)
    }
    identifiers.update(re.findall(r"\b\d{1,7}[A-Z]\b", source))
    if is_valid_matrix_article(matrix_article):
        identifiers.add(matrix_article.upper())
    return identifiers


def _is_strong_identifier(value: str) -> bool:
    return bool(re.fullmatch(r"(?:VA|PR)\d{1,6}[A-Z]?|\d{1,7}[A-Z]", value))


def _property_tokens(value: str) -> set[str]:
    ignored = {"quinta", "monte", "herdade", "predio", "rustico", "da", "de", "do", "dos", "das"}
    return {
        token.lower()
        for token in re.findall(r"[A-Za-zÀ-ÿ]{4,}", value)
        if token.lower() not in ignored
    }


__all__ = ["PropertyPackDiscovery", "PropertyPackMatch"]
