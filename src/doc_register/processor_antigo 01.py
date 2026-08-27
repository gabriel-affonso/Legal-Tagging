from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import logging
import shutil
from pathlib import Path

from .config import AppConfig
from .detectors import detect_signals
from .models import PdfCandidate
from .ollama_client import extract_with_ollama
from .pdf_text import extract_text_with_optional_ocr
from .registry import ExcelRegister
from .text_selection import select_classification_text, select_highlighted_relevant_text
from .validators import validate_result


LOGGER = logging.getLogger(__name__)


class DocumentProcessor:
    def __init__(self, config: AppConfig):
        self.config = config
        self.register = ExcelRegister(config.excel_path)

    def scan_once(self) -> int:
        self.config.ensure_directories()
        existing_hashes = self.register.existing_hashes()
        processed = 0

        for source_path in self._iter_pdf_files():
            candidate = None
            try:
                candidate = self._copy_candidate(source_path)
                if candidate.sha256 in existing_hashes:
                    LOGGER.info("Skipping duplicate PDF: %s", source_path.name)
                    continue
                self._process_candidate(candidate)
                existing_hashes.add(candidate.sha256)
                processed += 1
            except Exception as exc:
                LOGGER.exception("Failed to process %s", source_path)
                if candidate and candidate.sha256 not in existing_hashes:
                    self.register.append(candidate, _error_result(exc))
                    existing_hashes.add(candidate.sha256)
                self._move_to_error_dir(source_path)

        return processed

    def _iter_pdf_files(self) -> list[Path]:
        cutoff = None
        if self.config.copy_only_recent_minutes > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.config.copy_only_recent_minutes)

        files = []
        now = datetime.now(timezone.utc)
        for path in self.config.input_dir.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() != ".pdf":
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
        safe_stem = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in source_path.stem).strip()
        safe_stem = safe_stem[:80] or "document"
        return self.config.processing_dir / f"{safe_stem}__{digest[:12]}.pdf"

    def _process_candidate(self, candidate: PdfCandidate) -> None:
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
        )
        if extracted_text.notes:
            LOGGER.warning("%s: %s", candidate.copied_path.name, extracted_text.notes)

        signals = detect_signals(candidate.source_path.name, extracted_text.text)
        classification_text = select_classification_text(
            extracted_text.text,
            fallback_words=self.config.llm_classification_words,
        )
        highlighted_text = select_highlighted_relevant_text(
            extracted_text.text,
            max_chars=self.config.llm_extraction_max_chars,
        )

        LOGGER.info(
            "Sending classification sample and highlighted excerpts from %s to local Ollama model %s",
            candidate.copied_path.name,
            self.config.ollama_model,
        )
        result = extract_with_ollama(
            self.config.ollama_url,
            self.config.ollama_model,
            file_name=candidate.source_path.name,
            classification_text=classification_text,
            highlighted_text=highlighted_text,
            signals=signals,
            timeout_seconds=self.config.ollama_timeout_seconds,
        )
        result.text_source = extracted_text.source
        result.native_text_chars = str(extracted_text.native_text_chars)
        result.ocr_text_chars = str(extracted_text.ocr_text_chars)
        if extracted_text.notes:
            result.extraction_notes = _join_notes(result.extraction_notes, extracted_text.notes)
        validate_result(result, signals, extracted_text.text)
        self.register.append(candidate, result)

        archive_path = self.config.archive_dir / candidate.copied_path.name
        if not archive_path.exists():
            shutil.copy2(candidate.copied_path, archive_path)
        LOGGER.info("Registered %s as %s", candidate.source_path.name, result.document_type or "unknown")

    def _move_to_error_dir(self, source_path: Path) -> None:
        try:
            destination = self.config.error_dir / source_path.name
            if not destination.exists():
                shutil.copy2(source_path, destination)
        except Exception:
            LOGGER.exception("Could not copy failed file to error directory: %s", source_path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _join_notes(*notes: str) -> str:
    return " | ".join(note.strip() for note in notes if note and note.strip())


def _error_result(exc: Exception):
    from .models import ExtractionResult

    return ExtractionResult(
        processing_status="error",
        processed_ok="no",
        needs_review="yes",
        review_reason="Erro durante processamento automatico.",
        error_message=str(exc)[:1000],
        confidence="low",
    )
