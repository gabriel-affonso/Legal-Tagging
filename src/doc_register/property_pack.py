"""OCR-aware discovery of cadernetas and CRPs stored beside contracts."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re
from typing import Callable, Iterable
import unicodedata

from .detectors import detect_signals
from .pdf_text import extract_pdf_caderneta_text, extract_text_with_optional_ocr
from .property_intelligence import (
    CadastralEvidence,
    CadernetaValues,
    assess_cadastral_evidence,
    discover_caderneta_groups,
    parse_caderneta,
)
from .property_pipeline import (
    PropertyExtraction,
    is_valid_matrix_article,
    is_valid_matrix_section,
    normalize_matrix_article,
)


LOGGER = logging.getLogger(__name__)
MATCH_THRESHOLD = 85
PropertyTextLoader = Callable[[Path], tuple[str, str]]


@dataclass(frozen=True)
class PropertyDocumentDescriptor:
    path: Path
    identifiers: frozenset[str]
    kind: str = "other"
    text_source: str = ""
    caderneta_values: CadernetaValues | None = None
    cadastral_evidence: CadastralEvidence | None = None
    crp_values: CadernetaValues | None = None
    sharepoint_sequence: int | None = None
    group_index: int = 0
    page_numbers: tuple[int, ...] = ()
    filename_identifiers: frozenset[str] = frozenset()
    filename_area_hints_m2: tuple[int | float, ...] = ()

    @property
    def is_caderneta(self) -> bool:
        return self.kind == "caderneta_predial" and self.caderneta_values is not None

    @property
    def is_crp(self) -> bool:
        return self.kind == "crp" and self.crp_values is not None


@dataclass(frozen=True)
class PropertyPackMatch:
    status: str
    score: int = 0
    methods: tuple[str, ...] = ()
    candidate_count: int = 0
    source_path: Path | None = None
    crp_source_path: Path | None = None
    caderneta_values: CadernetaValues | None = None
    cadastral_evidence: CadastralEvidence | None = None
    cadastral_properties: tuple[CadastralEvidence, ...] = ()
    catalog_status: str = "property_catalog_no_caderneta"
    catalog_text_source: str = ""

    def audit(self) -> dict[str, object]:
        return {
            "property_catalog_status": self.catalog_status,
            "property_catalog_text_source": self.catalog_text_source,
            "property_pack_status": self.status,
            "property_pack_match_score": self.score,
            "property_pack_match_method": ";".join(self.methods),
            "property_pack_candidate_count": self.candidate_count,
            "caderneta_source_file": self.source_path.name if self.source_path else "",
            "crp_source_file": self.crp_source_path.name if self.crp_source_path else "",
            "property_pack_property_count": len(self.cadastral_properties) or int(self.cadastral_evidence is not None),
        }


class PropertyPackDiscovery:
    """Index cadastral records in every PDF, reusing cached OCR when available."""

    def __init__(
        self,
        paths: Iterable[Path],
        *,
        config=None,
        text_loader: PropertyTextLoader | None = None,
    ):
        self._paths = tuple(paths)
        self._config = config
        self._text_loader = text_loader
        self._descriptors: tuple[PropertyDocumentDescriptor, ...] | None = None

    def build_catalog(self) -> tuple[PropertyDocumentDescriptor, ...]:
        """Complete phase one before any contract-to-property matching starts."""
        return self._catalogue()

    def find_for_contract(
        self,
        contract_path: Path,
        contract_text: str,
        contract: PropertyExtraction,
    ) -> PropertyPackMatch:
        descriptors = self._catalogue()
        cadernetas = [item for item in descriptors if item.is_caderneta and item.path != contract_path]
        if not cadernetas:
            return PropertyPackMatch(status="property_catalog_no_caderneta")

        contract_ids = _identifiers(contract_path.name, contract_text, contract.matrix_article)
        property_tokens = _property_tokens(contract.property_name)
        direct = [
            (*_match_score(contract_ids, property_tokens, contract, candidate), candidate)
            for candidate in cadernetas
        ]
        direct = [item for item in direct if item[0] > 0]
        if direct:
            direct.sort(key=lambda item: item[0], reverse=True)
            top_score, top_methods, top = direct[0]
            tied = [item for item in direct if item[0] == top_score]
            if (
                top_score >= MATCH_THRESHOLD
                and "exact_property_identifier" in top_methods
                and len(tied) > 1
                and len({item[2].path for item in tied}) == 1
            ):
                evidences = tuple(
                    item[2].cadastral_evidence
                    for item in tied
                    if item[2].cadastral_evidence is not None
                )
                if len(evidences) == len(tied):
                    return PropertyPackMatch(
                        status="property_pack_multiple_preserved",
                        score=top_score,
                        methods=top_methods,
                        candidate_count=len(cadernetas),
                        source_path=top.path,
                        cadastral_properties=evidences,
                        catalog_status="caderneta_catalogued",
                        catalog_text_source=top.text_source,
                    )
            if len(tied) > 1:
                return self._result("property_pack_ambiguous", top_score, (), len(cadernetas))
            if top_score >= MATCH_THRESHOLD:
                return self._result("property_pack_matched", top_score, top_methods, len(cadernetas), top)

        chained = self._sp_sequence_chain(contract_path, contract, cadernetas, descriptors)
        if chained:
            if len(chained) > 1:
                return self._result("property_pack_ambiguous", 95, (), len(cadernetas))
            caderneta, crp = chained[0]
            return self._result(
                "property_pack_matched",
                95,
                ("sp_sequence_chain", "crp_caderneta_agreement"),
                len(cadernetas),
                caderneta,
                crp,
            )

        if direct:
            score, methods, candidate = direct[0]
            return self._result("property_pack_low_confidence", score, methods, len(cadernetas), candidate)
        return self._result("property_pack_not_found", 0, (), len(cadernetas))

    def _sp_sequence_chain(
        self,
        contract_path: Path,
        contract: PropertyExtraction,
        cadernetas: list[PropertyDocumentDescriptor],
        descriptors: tuple[PropertyDocumentDescriptor, ...],
    ) -> list[tuple[PropertyDocumentDescriptor, PropertyDocumentDescriptor]]:
        contract_sequence = _sharepoint_sequence(contract_path.name)
        if contract_sequence is None:
            return []
        crps = [item for item in descriptors if item.is_crp and item.path != contract_path]
        chains: list[tuple[PropertyDocumentDescriptor, PropertyDocumentDescriptor]] = []
        for crp in crps:
            if crp.sharepoint_sequence is None or abs(contract_sequence - crp.sharepoint_sequence) > 2:
                continue
            for caderneta in cadernetas:
                if caderneta.sharepoint_sequence is None:
                    continue
                if abs(contract_sequence - caderneta.sharepoint_sequence) > 2:
                    continue
                if abs(caderneta.sharepoint_sequence - crp.sharepoint_sequence) > 2:
                    continue
                if _facts_agree(crp.crp_values, caderneta.caderneta_values):
                    chains.append((caderneta, crp))
        return chains

    def _result(
        self,
        status: str,
        score: int,
        methods: tuple[str, ...],
        candidate_count: int,
        caderneta: PropertyDocumentDescriptor | None = None,
        crp: PropertyDocumentDescriptor | None = None,
    ) -> PropertyPackMatch:
        return PropertyPackMatch(
            status=status,
            score=score,
            methods=methods,
            candidate_count=candidate_count,
            source_path=caderneta.path if caderneta else None,
            crp_source_path=crp.path if crp else None,
            caderneta_values=caderneta.caderneta_values if caderneta and status == "property_pack_matched" else None,
            cadastral_evidence=caderneta.cadastral_evidence if caderneta and status == "property_pack_matched" else None,
            catalog_status="caderneta_catalogued",
            catalog_text_source=caderneta.text_source if caderneta else "",
        )

    def _catalogue(self) -> tuple[PropertyDocumentDescriptor, ...]:
        if self._descriptors is not None:
            return self._descriptors
        descriptors: list[PropertyDocumentDescriptor] = []
        for path in self._paths:
            try:
                text, source = self._read_candidate(path)
                groups = discover_caderneta_groups(text)
                filename_evidence = _filename_evidence(path.name)
                if groups:
                    for group_index, caderneta_pages in enumerate(groups, start=1):
                        cadastral_evidence = assess_cadastral_evidence(caderneta_pages)
                        if not cadastral_evidence.is_structurally_valid:
                            continue
                        caderneta = cadastral_evidence.values
                        descriptors.append(PropertyDocumentDescriptor(
                            path=path,
                            identifiers=frozenset(_identifiers(path.name, "\n".join(page.text for page in caderneta_pages), caderneta.matrix_article)),
                            kind="caderneta_predial",
                            text_source=source,
                            caderneta_values=caderneta,
                            cadastral_evidence=cadastral_evidence,
                            sharepoint_sequence=_sharepoint_sequence(path.name),
                            group_index=group_index,
                            page_numbers=tuple(page.page_number for page in caderneta_pages),
                            filename_identifiers=frozenset(filename_evidence[0]),
                            filename_area_hints_m2=filename_evidence[1],
                        ))
                    continue
                crp = _parse_crp(path.name, text)
                if crp:
                    descriptors.append(PropertyDocumentDescriptor(
                        path=path,
                        identifiers=frozenset(_identifiers(path.name, text, crp.matrix_article)),
                        kind="crp",
                        text_source=source,
                        crp_values=crp,
                        sharepoint_sequence=_sharepoint_sequence(path.name),
                        filename_identifiers=frozenset(filename_evidence[0]),
                        filename_area_hints_m2=filename_evidence[1],
                    ))
            except Exception as exc:
                LOGGER.warning("Could not index property-pack candidate %s: %s", path.name, exc)
        self._descriptors = tuple(descriptors)
        LOGGER.info(
            "Property catalogue ready: cadernetas=%s crps=%s",
            sum(item.is_caderneta for item in self._descriptors),
            sum(item.is_crp for item in self._descriptors),
        )
        return self._descriptors

    def _read_candidate(self, path: Path) -> tuple[str, str]:
        if self._text_loader:
            return self._text_loader(path)
        if self._config is None:
            return extract_pdf_caderneta_text(path), "native_pdf_full"
        cached_ocr = self._config.ocr_dir / f"{path.stem}__ocr.pdf"
        if cached_ocr.is_file():
            return extract_pdf_caderneta_text(cached_ocr), "cached_ocr_full"
        full_native = extract_pdf_caderneta_text(path)
        if discover_caderneta_groups(full_native):
            return full_native, "native_pdf_full"
        # Property extraction is allowed to catalogue every cadastral PDF, but
        # known non-property attachments must not trigger OCR just because
        # their native text is short (e.g. IBAN proofs in the same folder).
        if is_definite_non_property_filename(path.name):
            return full_native, "native_pdf_non_property"
        if not _is_property_candidate_name(path.name) and not _has_property_markers(full_native):
            return full_native, "native_pdf_not_property_candidate"
        extracted = extract_text_with_optional_ocr(
            path,
            max_pages=self._config.max_pdf_pages,
            max_chars=self._config.max_text_chars,
            ocr_enabled=self._config.ocr_enabled,
            ocr_min_text_chars=self._config.ocr_min_text_chars,
            ocr_dir=self._config.ocr_dir,
            ocr_language=self._config.ocr_language,
            ocr_timeout_seconds=self._config.ocr_timeout_seconds,
            layout_extraction_enabled=self._config.pdf_layout_extraction_enabled,
            layout_min_quality_score=self._config.pdf_layout_min_quality_score,
        )
        return extracted.text, extracted.source


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
    strong_ids = {value for value in contract_ids & set(descriptor.identifiers) if _is_strong_identifier(value)}
    if strong_ids:
        score += 100
        methods.append("exact_property_identifier")
    if contract.matrix_article and contract.matrix_article == caderneta.matrix_article:
        score += 40
        methods.append("matrix_article")
    if contract.matrix_section and contract.matrix_section == caderneta.matrix_section:
        score += 20
        methods.append("matrix_section")
    if property_tokens & _property_tokens(caderneta.property_name):
        score += 15
        methods.append("property_name_token")
    return score, tuple(methods)


def _parse_crp(file_name: str, text: str) -> CadernetaValues | None:
    normalized = _fold(text)
    filename_is_crp = bool(re.search(r"(?:^|[_\-\s])CRP(?:[_\-\s]|$)", file_name, re.IGNORECASE))
    text_is_crp = bool(re.search(
        r"\b(?:certidao\s+permanente|conservatoria|descricao\s+predial|registo\s+predial|crp)\b",
        normalized,
        re.IGNORECASE,
    ))
    if not filename_is_crp and not text_is_crp:
        return None
    article_match = re.search(r"\bartigo(?:\s+matricial)?\D{0,20}(\d{1,10}(?:\s*ARV)?)\b", normalized, re.IGNORECASE)
    section_match = re.search(r"\bseccao\D{0,15}([A-Z])\b", normalized, re.IGNORECASE)
    article = normalize_matrix_article(article_match.group(1)).canonical if article_match else ""
    section = section_match.group(1).upper() if section_match else ""
    signals = detect_signals(file_name, text)
    if not article and is_valid_matrix_article(signals.property_article):
        article = signals.property_article
    if not article:
        article = _matrix_article_from_filename(file_name)
    if not section and is_valid_matrix_section(signals.property_section):
        section = signals.property_section
    values = CadernetaValues(matrix_article=article, matrix_section=section).validated()
    return values if values.matrix_article or values.matrix_section else None


def _facts_agree(first: CadernetaValues | None, second: CadernetaValues | None) -> bool:
    if not first or not second:
        return False
    return bool(
        first.matrix_article and _same_article(first.matrix_article, second.matrix_article)
        or first.matrix_section and first.matrix_section == second.matrix_section
    )


def _matrix_article_from_filename(file_name: str) -> str:
    normalized = re.sub(r"[_\s]", "-", file_name.upper())
    match = re.search(r"(?:ARTIGO|ART|MATRIZ|MAT)-(\d{1,10}(?:ARV)?)\b", normalized)
    if not match:
        return ""
    return normalize_matrix_article(match.group(1)).canonical


def _same_article(first: str, second: str) -> bool:
    return re.sub(r"[-/\s]", "", first.upper()) == re.sub(r"[-/\s]", "", second.upper())


def _identifiers(file_name: str, text: str, matrix_article: str) -> set[str]:
    source = re.sub(r"[_\-]", " ", f"{file_name}\n{text}").upper()
    identifiers = {
        f"{prefix}{number}"
        for prefix, number in re.findall(r"\b(VA|PR|TO)[_\-\s]*(\d{1,6}[A-Z]?)\b", source)
    }
    if is_valid_matrix_article(matrix_article):
        identifiers.add(matrix_article.upper())
    return identifiers


def _is_strong_identifier(value: str) -> bool:
    return bool(re.fullmatch(r"(?:VA|PR|TO)\d{1,6}[A-Z]?", value))


def is_definite_non_property_filename(name: str) -> bool:
    normalized = _fold(name)
    return bool(re.search(
        r"(?<![A-Z0-9])(?:IBAN|NIB|PAGAMENTO|COMPROVATIVO|FATURA|FACTURA|RECIBO|TRANSFERENCIA)(?![A-Z0-9])",
        normalized,
        re.IGNORECASE,
    ))


def _is_property_candidate_name(name: str) -> bool:
    normalized = _fold(name)
    return bool(re.search(r"caderneta|predial|crp|certidao|matriz|registo", normalized, re.I))


def _has_property_markers(text: str) -> bool:
    normalized = _fold(text)
    return bool(re.search(
        r"\bcaderneta\s+predial|\bartigo\s+matricial|\bsec[cç][aã]o\b|\bidentificacao\s+do\s+predio\b",
        normalized,
        re.I,
    ))


def _filename_evidence(file_name: str) -> tuple[set[str], tuple[int | float, ...]]:
    """Keep business identifiers and area hints separate from cadastral fields."""
    normalized = re.sub(r"[_\-]", " ", file_name.upper())
    identifiers = {
        f"{prefix}{number}"
        for prefix, number in re.findall(r"\b(VA|PR|TO)\s*(\d{1,6}[A-Z]?)\b", normalized)
    }
    areas: list[int | float] = []
    for raw, unit in re.findall(r"\b(\d+(?:[.,]\d+)?)\s*(M2|M²|HA)\b", normalized):
        try:
            number = float(raw.replace(",", "."))
        except ValueError:
            continue
        if unit == "HA":
            number *= 10_000
        if 0 < number <= 100_000_000:
            areas.append(int(number) if number.is_integer() else number)
    return identifiers, tuple(areas)


def _property_tokens(value: str) -> set[str]:
    ignored = {"quinta", "monte", "herdade", "predio", "rustico", "da", "de", "do", "dos", "das"}
    return {
        token.lower()
        for token in re.findall(r"[A-Za-zÀ-ÿ]{4,}", value)
        if token.lower() not in ignored
    }


def _sharepoint_sequence(name: str) -> int | None:
    match = re.search(r"\bSP(\d{3,})\b", re.sub(r"[_\-]", " ", name), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _fold(value: str) -> str:
    return "".join(
        character for character in unicodedata.normalize("NFD", value).upper()
        if unicodedata.category(character) != "Mn"
    )


__all__ = [
    "PropertyDocumentDescriptor", "PropertyPackDiscovery", "PropertyPackMatch",
    "is_definite_non_property_filename",
]
