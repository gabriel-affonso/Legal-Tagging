from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import logging
import shutil
import subprocess
import time
from pathlib import Path

from .config import AppConfig
from .contract_types import apply_contract_type_hints
from .detectors import detect_signals
from .models import ExtractionResult, PdfCandidate
from .ollama_client import OllamaConnectionError, extract_with_ollama
from .pdf_text import extract_pdf_caderneta_text, extract_text_with_optional_ocr, run_ocrmypdf
from .property_intelligence import discover_caderneta_groups
from .registry import ExcelRegister, PropertyTableRegister
from .text_selection import select_classification_text, select_highlighted_relevant_text
from .ai_reviewer import review_if_needed
from .vision_recovery import recover_contract_with_vision
from .step33_engine import (
    apply_field_centric_extraction,
    prepare_field_centric_extraction,
)
from .validators.contract_clause_integration import apply_contract_clause_segmentation
from .validators.party_centric import (
    apply_party_centric_contract_extraction,
    prepare_party_centric_contract,
)
from .validators.contract_final_resolution import (
    apply_contract_final_resolution,
    prepare_contract_final_resolution,
)
from .validators.integration import recover_validate_result
from .validators.output_safety import apply_output_safety
from .validators.validator import validate_result


LOGGER = logging.getLogger(__name__)

# The beginning commonly contains title and parties; the end commonly contains
# signatures and dates. These portions complement the keyword-based excerpts.
EXTRACTION_FRONT_SHARE = 0.22
EXTRACTION_END_SHARE = 0.28
MIN_USEFUL_TEXT_CHARS = 200


class DocumentProcessor:
    def __init__(self, config: AppConfig):
        self.config = config
        self.register = ExcelRegister(config.excel_path)
        self.property_table_register = PropertyTableRegister(config.excel_path)
        self._vision_failures = 0

    def scan_once(
        self,
        *,
        reprocess_cadernetas: bool = False,
        property_table: bool = False,
    ) -> int:
        self.config.ensure_directories()
        self._vision_failures = 0
        target_register = self.property_table_register if property_table else self.register
        existing_hashes = set() if reprocess_cadernetas else target_register.existing_hashes()
        processed = 0

        for source_path in self._iter_pdf_files():
            candidate: PdfCandidate | None = None
            try:
                candidate = self._copy_candidate(source_path)
                if candidate.sha256 in existing_hashes:
                    LOGGER.info("Skipping duplicate PDF: %s", source_path.name)
                    continue

                self._process_candidate(
                    candidate,
                    replace_existing=reprocess_cadernetas,
                    property_table=property_table,
                )
                existing_hashes.add(candidate.sha256)
                processed += 1
            except OllamaConnectionError as exc:
                # A connection failure is an infrastructure problem, not a PDF
                # problem. Do not register the current document as failed, do
                # not move it to the error directory, and stop the batch so the
                # remaining PDFs can be retried after Ollama is restored.
                LOGGER.critical(
                    "Stopping scan because local Ollama is unavailable while "
                    "processing %s: %s",
                    source_path.name,
                    exc,
                )
                raise
            except Exception as exc:
                LOGGER.exception("Failed to process %s", source_path)
                if candidate and candidate.sha256 not in existing_hashes:
                    if property_table:
                        self.property_table_register.upsert(candidate, _error_result(exc))
                    elif reprocess_cadernetas:
                        self.register.upsert(candidate, _error_result(exc))
                    else:
                        self.register.append(candidate, _error_result(exc))
                    existing_hashes.add(candidate.sha256)
                self._move_to_error_dir(source_path)

        return processed

    def _iter_pdf_files(self) -> list[Path]:
        cutoff = None
        if self.config.copy_only_recent_minutes > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(
                minutes=self.config.copy_only_recent_minutes
            )

        files: list[Path] = []
        now = datetime.now(timezone.utc)
        for path in self.config.input_dir.rglob("*"):
            if not path.is_file() or path.suffix.lower() != ".pdf":
                continue

            modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if (now - modified_at).total_seconds() < self.config.minimum_file_age_seconds:
                LOGGER.info("Skipping file still settling: %s", path.name)
                continue
            if cutoff and modified_at < cutoff:
                continue
            files.append(path)

        return sorted(files, key=lambda item: item.stat().st_mtime)

    def _copy_candidate(self, source_path: Path) -> PdfCandidate:
        digest = _sha256(source_path)
        stat = source_path.stat()
        created_at = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc)
        modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        copied_path = self._destination_path(source_path, digest)

        if not copied_path.exists():
            shutil.copy2(source_path, copied_path)

        return PdfCandidate(
            source_path=source_path,
            copied_path=copied_path,
            sha256=digest,
            created_at=created_at,
            modified_at=modified_at,
        )

    def _destination_path(self, source_path: Path, digest: str) -> Path:
        safe_stem = "".join(
            ch if ch.isalnum() or ch in ".-_ " else "_"
            for ch in source_path.stem
        ).strip()
        safe_stem = safe_stem[:80] or "document"
        return self.config.processing_dir / f"{safe_stem}__{digest[:12]}.pdf"

    def _process_candidate(
        self,
        candidate: PdfCandidate,
        *,
        replace_existing: bool = False,
        property_table: bool = False,
    ) -> None:
        started_at = time.monotonic()
        LOGGER.info("Extracting text from %s", candidate.copied_path.name)

        extracted_text = extract_text_with_optional_ocr(
            candidate.copied_path,
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

        if extracted_text.notes:
            LOGGER.warning("%s: %s", candidate.copied_path.name, extracted_text.notes)

        document_text = _normalize_document_text(extracted_text.text)
        # Search every PDF for a native/cached-OCR caderneta before deciding
        # whether the primary pipeline should treat it as a lease annex.
        caderneta_text, caderneta_source = _read_internal_caderneta_text(
            candidate.copied_path,
            self.config,
            allow_ocr=False,
        )
        caderneta_found = bool(discover_caderneta_groups(caderneta_text))
        if caderneta_found:
            document_text = _append_internal_caderneta_text(document_text, caderneta_text)
        signals = detect_signals(candidate.source_path.name, document_text)
        if signals.suggested_category == "lease_contract" and not caderneta_found:
            caderneta_text, caderneta_source = _read_internal_caderneta_text(
                candidate.copied_path,
                self.config,
                allow_ocr=True,
            )
            caderneta_found = bool(discover_caderneta_groups(caderneta_text))
            if caderneta_found:
                document_text = _append_internal_caderneta_text(document_text, caderneta_text)
                signals = detect_signals(candidate.source_path.name, document_text)
        if caderneta_found:
            LOGGER.info(
                "Added internal caderneta search region for %s: chars=%s source=%s",
                candidate.copied_path.name,
                len(caderneta_text),
                caderneta_source,
            )
        # Step 2.7 runs before the LLM extraction.  For lease candidates the
        # second prompt receives only field-authorized clauses, never a broad
        # document-wide semantic sample.
        party_centric_report = prepare_party_centric_contract(document_text)
        final_resolution_report = prepare_contract_final_resolution(document_text)
        step33_report = prepare_field_centric_extraction(document_text)

        classification_text = select_classification_text(
            document_text,
            fallback_words=self.config.llm_classification_words,
        )
        highlighted_text = step33_report.context_for_llm(
            max_chars=self.config.llm_extraction_max_chars,
        ) or final_resolution_report.context_for_llm(
            max_chars=self.config.llm_extraction_max_chars,
        ) or party_centric_report.context_for_llm(
            max_chars=self.config.llm_extraction_max_chars,
        ) or _build_extraction_context(
            document_text,
            max_chars=self.config.llm_extraction_max_chars,
        )

        LOGGER.info(
            "Prepared text for %s: total=%d classification=%d extraction=%d "
            "native=%s ocr=%s suggested_category=%s",
            candidate.copied_path.name,
            len(document_text),
            len(classification_text),
            len(highlighted_text),
            extracted_text.native_text_chars,
            extracted_text.ocr_text_chars,
            signals.suggested_category,
        )

        if len(classification_text.strip()) < MIN_USEFUL_TEXT_CHARS:
            LOGGER.warning(
                "%s: classification sample is short (%d chars)",
                candidate.copied_path.name,
                len(classification_text.strip()),
            )
        if len(highlighted_text.strip()) < MIN_USEFUL_TEXT_CHARS:
            LOGGER.warning(
                "%s: extraction context is short (%d chars)",
                candidate.copied_path.name,
                len(highlighted_text.strip()),
            )

        LOGGER.info(
            "Sending classification sample and extraction context from %s "
            "to local Ollama model %s",
            candidate.copied_path.name,
            self.config.ollama_model,
        )

        llm_started_at = time.monotonic()
        result = extract_with_ollama(
            self.config.ollama_url,
            self.config.ollama_model,
            file_name=candidate.source_path.name,
            classification_text=classification_text,
            highlighted_text=highlighted_text,
            signals=signals,
            timeout_seconds=self.config.ollama_timeout_seconds,
        )
        LOGGER.info(
            "Ollama processing completed for %s in %.2fs",
            candidate.copied_path.name,
            time.monotonic() - llm_started_at,
        )

        result.text_source = extracted_text.source
        result.native_text_chars = str(extracted_text.native_text_chars)
        result.ocr_text_chars = str(extracted_text.ocr_text_chars)
        if extracted_text.notes:
            result.extraction_notes = _join_notes(
                result.extraction_notes,
                extracted_text.notes,
            )
        apply_contract_type_hints(
            result,
            candidate.source_path.name,
            document_text[:3000],
            signals.contract_type,
            signals.contract_type_label,
        )
        result = apply_contract_clause_segmentation(
            result,
            document_text=document_text,
            enabled=self.config.contract_clause_segmentation_enabled,
            max_clause_text_chars=self.config.contract_clause_segmentation_max_clause_chars,
        )

        result = recover_validate_result(
            result,
            config=self.config,
            file_name=candidate.source_path.name,
            signals=signals,
            document_text=document_text,
        )
        result = apply_party_centric_contract_extraction(
            result,
            party_centric_report,
        )
        # Re-score after the structure gate so the AI reviewer sees the real
        # missing fields instead of broad-context guesses that were removed.
        result = validate_result(
            result,
            signals,
            document_text,
            file_name=candidate.source_path.name,
            recover=False,
            entity_resolution=False,
        )
        result = review_if_needed(
            result,
            config=self.config,
            file_name=candidate.source_path.name,
            document_text=(
                final_resolution_report.context_for_llm(
                    max_chars=self.config.ai_review_max_evidence_chars,
                )
                if final_resolution_report.active
                else party_centric_report.context_for_llm(
                    max_chars=self.config.ai_review_max_evidence_chars,
                )
                if party_centric_report.active
                else document_text
            ),
        )
        # Reviewer proposals remain subject to the same clause-origin gate.
        result = apply_party_centric_contract_extraction(
            result,
            party_centric_report,
        )
        # Step 3.3 contributes field-specific, authoritative evidence before
        # the final contract resolver.  Keeping that resolver last is
        # deliberate: the register never depends on the order in which a
        # recovery, the reviewer, or a source-specific extractor happened to
        # run (no last-write-wins publication).
        result = apply_field_centric_extraction(result, step33_report)
        result = apply_contract_final_resolution(result, final_resolution_report)
        if self._vision_failures < int(self.config.vision_max_failures_per_batch):
            result, vision_applied = recover_contract_with_vision(
                result,
                config=self.config,
                file_name=candidate.source_path.name,
                pdf_path=candidate.copied_path,
                signals=signals,
                final_resolution_report=final_resolution_report,
            )
            vision_audit = result.raw_json.get("vision_recovery", {}) if isinstance(result.raw_json, dict) else {}
            self._vision_failures += len(vision_audit.get("failures", [])) if isinstance(vision_audit, dict) else 0
            if vision_applied:
                # Vision candidates are added to the same candidate graph as
                # OCR and deterministic evidence; the final resolver remains
                # the only component that publishes contract fields.
                result = apply_contract_final_resolution(result, final_resolution_report)
        elif self.config.vision_enabled:
            result.vision_status = "CIRCUIT_OPEN"
            result.vision_trigger_reason = "vision_failure_limit_reached"
        result = apply_output_safety(result, document_text=document_text)
        result = validate_result(
            result,
            signals,
            document_text,
            file_name=candidate.source_path.name,
            ollama_url=self.config.ollama_url,
            ollama_model=self.config.ollama_model,
            recovery_ai_enabled=False,
            recovery_timeout_seconds=self.config.critical_recovery_timeout_seconds
            if hasattr(self.config, "critical_recovery_timeout_seconds")
            else 180,
            recover=False,
            # Entity recovery already ran before the final resolver. Do not
            # allow a later sanitiser to overwrite its selected candidates.
            entity_resolution=False,
        )
        if property_table:
            property_rows = self.property_table_register.upsert(candidate, result)
            LOGGER.info(
                "Registered %s property row(s) for %s in Property Table.",
                property_rows,
                candidate.source_path.name,
            )
        elif replace_existing:
            self.register.upsert(candidate, result)
        else:
            self.register.append(candidate, result)
        self._archive_candidate(candidate)

        LOGGER.info(
            "Registered %s as %s in %.2fs; category=%s confidence=%s review=%s reasons=%s",
            candidate.source_path.name,
            result.document_type or "unknown",
            time.monotonic() - started_at,
            result.document_category,
            result.confidence or "blank",
            result.needs_review,
            result.review_reason or "none",
        )

    def _archive_candidate(self, candidate: PdfCandidate) -> None:
        archive_path = self.config.archive_dir / candidate.copied_path.name
        if not archive_path.exists():
            shutil.copy2(candidate.copied_path, archive_path)

    def _move_to_error_dir(self, source_path: Path) -> None:
        try:
            destination = self.config.error_dir / source_path.name
            if not destination.exists():
                shutil.copy2(source_path, destination)
        except Exception:
            LOGGER.exception(
                "Could not copy failed file to error directory: %s",
                source_path,
            )


def _read_internal_caderneta_text(
    path: Path,
    config: AppConfig,
    *,
    allow_ocr: bool,
) -> tuple[str, str]:
    """Read every page, creating OCR only if native text has no caderneta."""
    cached_ocr = config.ocr_dir / f"{path.stem}__ocr.pdf"
    if cached_ocr.is_file():
        return extract_pdf_caderneta_text(cached_ocr), "cached_ocr_pdf"

    native_text = extract_pdf_caderneta_text(path)
    if discover_caderneta_groups(native_text) or not allow_ocr or not config.ocr_enabled:
        return native_text, "native_pdf"

    try:
        run_ocrmypdf(
            path,
            cached_ocr,
            language=config.ocr_language,
            timeout_seconds=config.ocr_timeout_seconds,
        )
        if cached_ocr.is_file():
            return extract_pdf_caderneta_text(cached_ocr), "generated_ocr_pdf"
    except (FileNotFoundError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        LOGGER.warning("Could not OCR internal caderneta region for %s: %s", path.name, exc)
    return native_text, "native_pdf"


def _append_internal_caderneta_text(document_text: str, caderneta_text: str) -> str:
    """Preserve the contract's first pages and append the later annex evidence."""
    if not document_text:
        return caderneta_text
    return f"{document_text}\n\n[INTERNAL CADENETA SEARCH REGION]\n{caderneta_text}"


def _build_extraction_context(text: str, max_chars: int) -> str:
    """Combine keyword excerpts with document beginning and signature area.

    The keyword selector remains the main source. The beginning protects party
    and property descriptions, while the end protects signature dates that are
    often absent from keyword-only excerpts. Duplicate passages are removed.
    """
    if not text.strip() or max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return _label_excerpt("FULL DOCUMENT TEXT", text)

    front_budget = max(300, int(max_chars * EXTRACTION_FRONT_SHARE))
    end_budget = max(300, int(max_chars * EXTRACTION_END_SHARE))
    highlighted_budget = max(500, max_chars - front_budget - end_budget)

    highlighted = select_highlighted_relevant_text(
        text,
        max_chars=highlighted_budget,
    )
    chunks = [
        ("DOCUMENT BEGINNING - title, parties and initial property description", text[:front_budget]),
        ("KEYWORD-BASED RELEVANT EXCERPTS", highlighted),
        ("DOCUMENT END - signatures, dates and final clauses", text[-end_budget:]),
    ]
    return _join_distinct_excerpts(chunks, max_chars=max_chars)


def _join_distinct_excerpts(
    chunks: list[tuple[str, str]],
    *,
    max_chars: int,
) -> str:
    output: list[str] = []
    seen: set[str] = set()
    remaining = max_chars

    for label, chunk in chunks:
        cleaned = chunk.strip()
        if not cleaned:
            continue
        fingerprint = _text_fingerprint(cleaned)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)

        labelled = _label_excerpt(label, cleaned)
        if len(labelled) > remaining:
            labelled = labelled[:remaining].rstrip()
        if not labelled:
            break
        output.append(labelled)
        remaining -= len(labelled) + 2
        if remaining <= 0:
            break

    return "\n\n".join(output)


def _label_excerpt(label: str, text: str) -> str:
    return f"[{label}]\n{text.strip()}"


def _text_fingerprint(value: str) -> str:
    normalized = " ".join(value.lower().split())
    # A bounded fingerprint also catches exact duplicates without retaining
    # confidential document text in an auxiliary structure.
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalize_document_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x00", "")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    normalized = "\n".join(lines)
    normalized = "\n\n".join(
        block.strip() for block in normalized.split("\n\n") if block.strip()
    )
    return normalized.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _join_notes(*notes: str) -> str:
    return " | ".join(note.strip() for note in notes if note and note.strip())


def _error_result(exc: Exception) -> ExtractionResult:
    return ExtractionResult(
        processing_status="error",
        processed_ok="no",
        needs_review="yes",
        review_reason="Erro durante processamento automatico.",
        error_message=str(exc)[:1000],
        confidence="low",
    )
