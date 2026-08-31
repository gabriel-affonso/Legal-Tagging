"""Independent runner for the focused lease-property extraction pipeline."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path

from .config import AppConfig
from .ollama_client import extract_property_with_ollama
from .pdf_text import extract_pdf_caderneta_text, extract_text_with_optional_ocr
from .property_intelligence import (
    AnnexPage,
    discover_caderneta_groups,
    assess_cadastral_evidence,
    matrix_key,
    parse_caderneta,
    reconcile_property,
    recover_from_annexes,
    recovery_required,
)
from .property_pack import PropertyPackDiscovery, PropertyPackMatch, is_definite_non_property_filename
from .property_pipeline import PropertyExtractionPipeline
from .registry import PropertyExcelRegister


LOGGER = logging.getLogger(__name__)


class PropertyExtractionProcessor:
    """Scan PDFs without touching the Excel register or primary archive."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.output_dir = config.property_extraction_dir or (config.log_dir / "property_extractions")
        self.register = PropertyExcelRegister(config.excel_path)

    def scan_once(self) -> int:
        self.config.ensure_directories()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        existing_hashes = self.register.existing_hashes(PIPELINE_VERSION)
        pdf_files = self._iter_pdf_files()
        pack_discovery = PropertyPackDiscovery(pdf_files, config=self.config)
        # Phase 1 is deliberately complete before phase 2 starts.  Matching a
        # contract can therefore never depend on filesystem iteration order.
        pack_discovery.build_catalog()
        processed = 0
        for path in pdf_files:
            if is_definite_non_property_filename(path.name):
                LOGGER.info("Property scan skipped non-property attachment: %s", path.name)
                continue
            digest = _sha256(path)
            if digest in existing_hashes:
                continue
            try:
                extracted = extract_text_with_optional_ocr(
                    path,
                    max_pages=self.config.max_pdf_pages,
                    max_chars=self.config.max_text_chars,
                    ocr_enabled=self.config.ocr_enabled,
                    ocr_min_text_chars=self.config.ocr_min_text_chars,
                    ocr_dir=self.config.ocr_dir,
                    ocr_language=self.config.ocr_language,
                    ocr_timeout_seconds=self.config.ocr_timeout_seconds,
                    layout_extraction_enabled=self.config.pdf_layout_extraction_enabled,
                    layout_min_quality_score=self.config.pdf_layout_min_quality_score,
                )
                # Step 3.0: contract rules run first.  The LLM is deliberately
                # withheld until the caderneta recovery path is exhausted.
                result = PropertyExtractionPipeline().extract(
                    extracted.text,
                    allow_llm=False,
                )
                annex_text = ""
                if result.status == "processed":
                    pack_match = pack_discovery.find_for_contract(path, extracted.text, result)
                    result = _with_property_pack_audit(result, pack_match)
                    annex_text = _read_annex_text(path, self.config)
                    annex_groups = discover_caderneta_groups(annex_text)
                    assessed_groups = [
                        (group, assess_cadastral_evidence(group))
                        for group in annex_groups
                    ]
                    verified_groups = [
                        (group, evidence)
                        for group, evidence in assessed_groups
                        if evidence.is_structurally_valid
                    ]
                    if len(verified_groups) > 1:
                        result = _with_multiple_cadastral_properties(result, verified_groups, path.name)
                        annex_pages = []
                        internal_caderneta_status = "multiple_cadernetas_preserved"
                    else:
                        annex_pages, internal_caderneta_status = _select_internal_caderneta(
                            result, annex_groups,
                        )
                    if not annex_pages and len(pack_match.cadastral_properties) > 1:
                        result = _with_multiple_cadastral_properties(
                            result,
                            [([], evidence) for evidence in pack_match.cadastral_properties],
                            pack_match.source_path.name if pack_match.source_path else "external_caderneta",
                            same_pdf=False,
                        )
                    result = _with_internal_caderneta_audit(
                        result, annex_groups, internal_caderneta_status,
                    )
                    if result.status != "processed_multi_property":
                        result = _reconcile_property_evidence(result, pack_match, annex_pages, path.name)
                    if not pack_match.caderneta_values and not annex_pages:
                        result = recover_from_annexes(result, "")
                    if (
                        result.status != "processed_multi_property"
                        and internal_caderneta_status not in {
                            "multiple_cadernetas_unresolved",
                            "multiple_cadernetas_preserved",
                            "multiple_cadernetas_same_matrix_key",
                            "internal_caderneta_matrix_key_conflict",
                            "internal_caderneta_invalid_structure",
                        }
                        and recovery_required(result)
                        and self.config.property_llm_enabled
                    ):
                        llm_result = PropertyExtractionPipeline(self._llm_extractor()).extract(
                            extracted.text,
                            allow_llm=True,
                        )
                        llm_result = _with_property_pack_audit(llm_result, pack_match)
                        result = _reconcile_property_evidence(llm_result, pack_match, annex_pages, path.name)
                        if not pack_match.caderneta_values and not annex_pages:
                            result = recover_from_annexes(result, "")
                if result.status == "processed" and recovery_required(result):
                    result = replace(
                        result,
                        status="needs_review",
                        reason="property_recovery_incomplete",
                    )
                payload = {
                    "pipeline_version": PIPELINE_VERSION,
                    "processed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                    "source_file_name": path.name,
                    "source_file_path": str(path),
                    "sha256": digest,
                    "text_source": extracted.source,
                    "native_text_chars": extracted.native_text_chars,
                    "ocr_text_chars": extracted.ocr_text_chars,
                    "extraction_notes": extracted.notes,
                    "annex_text_chars": len(annex_text),
                    "result": result.to_dict(),
                }
                self.register.upsert(_excel_payload(payload, result.to_dict()))
                _write_json(self.output_dir / f"{digest}.json", payload)
                existing_hashes.add(digest)
                processed += 1
                LOGGER.info(
                    "Property extraction finished for %s: status=%s confidence=%s llm=%s",
                    path.name,
                    result.status,
                    result.confidence,
                    result.used_llm,
                )
            except Exception:
                LOGGER.exception("Property extraction failed for %s", path)
        return processed

    def _iter_pdf_files(self) -> list[Path]:
        return sorted(
            (path for path in self.config.input_dir.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf"),
            key=lambda item: item.stat().st_mtime,
        )

    def _llm_extractor(self):
        if not self.config.property_llm_enabled:
            return None

        def extract(clause_text: str) -> dict[str, object]:
            return extract_property_with_ollama(
                self.config.ollama_url,
                self.config.ollama_model,
                clause_text,
                timeout_seconds=self.config.property_llm_timeout_seconds,
            )

        return extract


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(path)


def _excel_payload(payload: dict[str, object], result: dict[str, object]) -> dict[str, object]:
    source = result.get("source") if isinstance(result.get("source"), dict) else {}
    return {
        "pipeline_version": payload["pipeline_version"],
        "processed_at": payload["processed_at"],
        "source_file_name": payload["source_file_name"],
        "source_file_path": payload["source_file_path"],
        "sha256": payload["sha256"],
        "status": result.get("status", ""),
        "reason": result.get("reason", ""),
        "document_type": result.get("document_type", ""),
        "owner_name": result.get("owner_name", ""),
        "property_name": result.get("property_name", ""),
        "matrix_article": result.get("matrix_article", ""),
        "matrix_section": result.get("matrix_section", ""),
        "property_matrix_key": result.get("property_matrix_key", ""),
        "area_m2": result.get("area_m2", ""),
        "confidence": result.get("confidence", ""),
        "extraction_confidence": result.get("extraction_confidence", ""),
        "identity_confidence": result.get("identity_confidence", ""),
        "completeness_score": result.get("completeness_score", ""),
        "consistency_score": result.get("consistency_score", ""),
        "final_confidence": result.get("final_confidence", ""),
        "properties": result.get("properties", []),
        "lease_score": result.get("lease_score", ""),
        "candidate_score": result.get("candidate_score", ""),
        "used_llm": result.get("used_llm", ""),
        "source_section": source.get("section", ""),
        "source_clause": source.get("clause", ""),
        "text_source": payload["text_source"],
        "native_text_chars": payload["native_text_chars"],
        "ocr_text_chars": payload["ocr_text_chars"],
        "extraction_notes": payload["extraction_notes"],
        "annex_text_chars": payload["annex_text_chars"],
        "llm_error": result.get("llm_error", ""),
        "audit": result.get("audit", {}),
        "evidence_model": result.get("evidence_model", {}),
        "property_catalog_status": _audit_value(result, "property_catalog_status"),
        "property_catalog_text_source": _audit_value(result, "property_catalog_text_source"),
        "property_pack_status": _audit_value(result, "property_pack_status"),
        "property_pack_match_score": _audit_value(result, "property_pack_match_score"),
        "property_pack_match_method": _audit_value(result, "property_pack_match_method"),
        "property_pack_candidate_count": _audit_value(result, "property_pack_candidate_count"),
        "property_pack_property_count": _audit_value(result, "property_pack_property_count"),
        "caderneta_source_file": _audit_value(result, "caderneta_source_file"),
        "crp_source_file": _audit_value(result, "crp_source_file"),
        "caderneta_evidence_sources": _audit_value(result, "caderneta_evidence_sources"),
        "caderneta_same_pdf_found": _audit_value(result, "caderneta_same_pdf_found"),
        "internal_caderneta_status": _audit_value(result, "internal_caderneta_status"),
        "internal_caderneta_count": _audit_value(result, "internal_caderneta_count"),
    }


PIPELINE_VERSION = "3.3.2"


def _with_property_pack_audit(result, match: PropertyPackMatch):
    audit = dict(result.audit)
    audit.update(match.audit())
    return replace(result, audit=audit)


def _reconcile_property_evidence(result, match: PropertyPackMatch, annex_pages, contract_file_name: str):
    sources: list[str] = []
    if match.caderneta_values:
        result = reconcile_property(
            result,
            match.caderneta_values,
            [],
            match.cadastral_evidence,
        )
        if match.source_path:
            sources.append(match.source_path.name)
    if annex_pages:
        result = reconcile_property(result, parse_caderneta(annex_pages), annex_pages)
        sources.append(contract_file_name)
    if sources:
        audit = dict(result.audit)
        audit["caderneta_evidence_sources"] = sources
        audit["caderneta_same_pdf_found"] = bool(annex_pages)
        result = replace(result, audit=audit)
    return result


def _select_internal_caderneta(result, groups: list[list[AnnexPage]]) -> tuple[list[AnnexPage], str]:
    if not groups:
        return [], "internal_caderneta_not_found"
    evidence = [(group, assess_cadastral_evidence(group)) for group in groups]
    verified = [(group, item) for group, item in evidence if item.is_structurally_valid]
    if not verified:
        return [], "internal_caderneta_invalid_structure"

    contract_key = matrix_key(result.matrix_article, result.matrix_section)
    if contract_key:
        matches = [(group, item) for group, item in verified if item.matrix_key == contract_key]
        if len(matches) == 1:
            return matches[0][0], "internal_caderneta_selected_by_matrix_key"
        if len(matches) > 1:
            return [], "multiple_cadernetas_same_matrix_key"
        # A full contractual identity that disagrees with the only cadastral
        # identity is not enough to attribute a holder to this contract.
        return [], "internal_caderneta_matrix_key_conflict"
    if len(verified) == 1:
        return verified[0][0], "internal_caderneta_selected"
    return [], "multiple_cadernetas_preserved"


def _with_internal_caderneta_audit(result, groups: list[list[AnnexPage]], status: str):
    audit = dict(result.audit)
    audit.update({
        "internal_caderneta_status": status,
        "internal_caderneta_count": len(groups),
        "internal_caderneta_groups": [
            {
                "pages": [page.page_number for page in group],
                "values": parse_caderneta(group).as_dict(),
                "evidence": assess_cadastral_evidence(group).to_dict(),
            }
            for group in groups
        ],
    })
    if status in {
        "multiple_cadernetas_same_matrix_key",
        "internal_caderneta_matrix_key_conflict",
        "internal_caderneta_invalid_structure",
    }:
        return replace(
            result,
            status="needs_review",
            reason="multiple_internal_cadernetas_unresolved",
            audit=audit,
        )
    return replace(result, audit=audit)


def _with_multiple_cadastral_properties(
    result, verified_groups, contract_file_name: str, *, same_pdf: bool = True
):
    properties: list[dict[str, object]] = []
    completeness_values: list[float] = []
    identity_values: list[float] = []
    verified_owners: list[str] = []
    for group, evidence in verified_groups:
        values = evidence.values
        item = values.as_dict()
        item.update({
            "property_matrix_key": evidence.matrix_key,
            "source_file": contract_file_name,
            "pages": list(evidence.page_numbers),
            "structure_status": evidence.structure_status,
            "owner_status": evidence.owner_status,
        })
        properties.append(item)
        cadastral_fields = (
            values.property_name,
            values.matrix_article,
            values.matrix_section,
            values.area_m2,
        )
        completeness_values.append(sum(value not in ("", None) for value in cadastral_fields) / 4)
        identity_values.append(
            0.50 * bool(values.matrix_article)
            + 0.35 * bool(values.matrix_section)
            + 0.15 * bool(values.property_name)
        )
        if evidence.owner_is_verified and values.owner_name:
            verified_owners.append(values.owner_name)

    completeness = min(completeness_values, default=0.0)
    identity = min(identity_values, default=0.0)
    extraction = 0.99
    consistency = 1.0
    final_confidence = min(extraction, identity, completeness, consistency)
    common_owner = verified_owners[0] if verified_owners and len(set(verified_owners)) == 1 else ""
    audit = dict(result.audit)
    audit.update({
        "multi_property_status": "preserved",
        "multi_property_count": len(properties),
        "caderneta_same_pdf_found": same_pdf,
        "caderneta_evidence_sources": [contract_file_name],
        "identity_selection": {"source": "caderneta_predial", "atomic": True, "multiple": True},
    })
    return replace(
        result,
        status="processed_multi_property",
        reason="",
        property_name="",
        matrix_article="",
        matrix_section="",
        property_matrix_key="",
        area_m2=None,
        owner_name=common_owner,
        properties=tuple(properties),
        confidence=round(final_confidence * 100),
        extraction_confidence=extraction,
        identity_confidence=round(identity, 4),
        completeness_score=round(completeness, 4),
        consistency_score=consistency,
        final_confidence=round(final_confidence, 4),
        audit=audit,
    )


def _read_annex_text(path: Path, config: AppConfig) -> str:
    """Read all pages so an internal caderneta can be found anywhere."""
    cached_ocr = config.ocr_dir / f"{path.stem}__ocr.pdf"
    source = cached_ocr if cached_ocr.is_file() else path
    return extract_pdf_caderneta_text(source)


def _audit_value(result: dict[str, object], key: str) -> object:
    audit = result.get("audit")
    return audit.get(key, "") if isinstance(audit, dict) else ""


__all__ = ["PropertyExtractionProcessor"]
