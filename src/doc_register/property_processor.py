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
from .pdf_text import extract_pdf_annex_text, extract_text_with_optional_ocr
from .property_intelligence import reconcile_property, recover_from_annexes, recovery_required
from .property_pack import PropertyPackDiscovery, PropertyPackMatch
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
        pack_discovery = PropertyPackDiscovery(pdf_files)
        processed = 0
        for path in pdf_files:
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
                if result.status == "processed" and recovery_required(result):
                    pack_match = pack_discovery.find_for_contract(path, extracted.text, result)
                    result = _with_property_pack_audit(result, pack_match)
                    if pack_match.caderneta_values:
                        result = reconcile_property(result, pack_match.caderneta_values, [])
                    if recovery_required(result):
                        annex_text = extract_pdf_annex_text(path)
                        result = recover_from_annexes(result, annex_text)
                    if recovery_required(result) and self.config.property_llm_enabled:
                        llm_result = PropertyExtractionPipeline(self._llm_extractor()).extract(
                            extracted.text,
                            allow_llm=True,
                        )
                        llm_result = _with_property_pack_audit(llm_result, pack_match)
                        if pack_match.caderneta_values:
                            llm_result = reconcile_property(llm_result, pack_match.caderneta_values, [])
                        result = recover_from_annexes(llm_result, annex_text)
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
        "property_name": result.get("property_name", ""),
        "matrix_article": result.get("matrix_article", ""),
        "matrix_section": result.get("matrix_section", ""),
        "area_m2": result.get("area_m2", ""),
        "confidence": result.get("confidence", ""),
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
        "property_pack_status": _audit_value(result, "property_pack_status"),
        "property_pack_match_score": _audit_value(result, "property_pack_match_score"),
        "property_pack_match_method": _audit_value(result, "property_pack_match_method"),
        "property_pack_candidate_count": _audit_value(result, "property_pack_candidate_count"),
        "caderneta_source_file": _audit_value(result, "caderneta_source_file"),
    }


PIPELINE_VERSION = "3.1"


def _with_property_pack_audit(result, match: PropertyPackMatch):
    audit = dict(result.audit)
    audit.update(match.audit())
    return replace(result, audit=audit)


def _audit_value(result: dict[str, object], key: str) -> object:
    audit = result.get("audit")
    return audit.get(key, "") if isinstance(audit, dict) else ""


__all__ = ["PropertyExtractionProcessor"]
