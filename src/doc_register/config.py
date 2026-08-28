from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json


@dataclass(frozen=True)
class AppConfig:
    input_dir: Path
    processing_dir: Path
    archive_dir: Path
    error_dir: Path
    log_dir: Path
    excel_path: Path
    ollama_url: str
    ollama_model: str
    ollama_timeout_seconds: int
    llm_classification_words: int
    llm_extraction_max_chars: int
    poll_interval_seconds: int
    minimum_file_age_seconds: int
    ocr_enabled: bool
    ocr_language: str
    ocr_min_text_chars: int
    ocr_timeout_seconds: int
    ocr_dir: Path
    pdf_layout_extraction_enabled: bool
    pdf_layout_min_quality_score: int
    max_pdf_pages: int
    max_text_chars: int
    copy_only_recent_minutes: int
    ai_review_enabled: bool = True
    ai_review_timeout_seconds: int = 180
    ai_review_max_evidence_chars: int = 6500
    ai_review_auto_accept_confidence: float = 0.90
    ai_review_human_confidence: float = 0.70
    contract_clause_segmentation_enabled: bool = True
    contract_clause_segmentation_max_clause_chars: int = 1200
    property_extraction_dir: Path | None = None
    property_llm_enabled: bool = True
    property_llm_timeout_seconds: int = 180

    @classmethod
    def from_json(cls, path: Path) -> "AppConfig":
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)

        base = path.parent

        def as_path(key: str, default: str | None = None) -> Path:
            raw_value = raw[key] if key in raw else default
            if raw_value is None:
                raise KeyError(key)
            value = Path(raw_value).expanduser()
            return value if value.is_absolute() else (base / value).resolve()

        return cls(
            input_dir=as_path("input_dir"),
            processing_dir=as_path("processing_dir"),
            archive_dir=as_path("archive_dir"),
            error_dir=as_path("error_dir"),
            log_dir=as_path("log_dir", "logs"),
            excel_path=as_path("excel_path"),
            ollama_url=str(raw.get("ollama_url", "http://localhost:11434")).rstrip("/"),
            ollama_model=str(raw.get("ollama_model", "qwen3:8b")),
            ollama_timeout_seconds=int(raw.get("ollama_timeout_seconds", 600)),
            llm_classification_words=int(raw.get("llm_classification_words", 500)),
            llm_extraction_max_chars=int(raw.get("llm_extraction_max_chars", 8000)),
            poll_interval_seconds=int(raw.get("poll_interval_seconds", 300)),
            minimum_file_age_seconds=int(raw.get("minimum_file_age_seconds", 30)),
            ocr_enabled=_as_bool(raw.get("ocr_enabled", True)),
            ocr_language=str(raw.get("ocr_language", "por+eng")),
            ocr_min_text_chars=int(raw.get("ocr_min_text_chars", 600)),
            ocr_timeout_seconds=int(raw.get("ocr_timeout_seconds", 600)),
            ocr_dir=as_path("ocr_dir", "ocr"),
            pdf_layout_extraction_enabled=_as_bool(
                raw.get("pdf_layout_extraction_enabled", True)
            ),
            pdf_layout_min_quality_score=int(raw.get("pdf_layout_min_quality_score", 35)),
            max_pdf_pages=int(raw.get("max_pdf_pages", 12)),
            max_text_chars=int(raw.get("max_text_chars", 24000)),
            copy_only_recent_minutes=int(raw.get("copy_only_recent_minutes", 0)),
            ai_review_enabled=_as_bool(raw.get("ai_review_enabled", True)),
            ai_review_timeout_seconds=int(raw.get("ai_review_timeout_seconds", 180)),
            ai_review_max_evidence_chars=int(raw.get("ai_review_max_evidence_chars", 6500)),
            ai_review_auto_accept_confidence=float(raw.get("ai_review_auto_accept_confidence", 0.90)),
            ai_review_human_confidence=float(raw.get("ai_review_human_confidence", 0.70)),
            contract_clause_segmentation_enabled=_as_bool(
                raw.get("contract_clause_segmentation_enabled", True)
            ),
            contract_clause_segmentation_max_clause_chars=int(
                raw.get("contract_clause_segmentation_max_clause_chars", 1200)
            ),
            property_extraction_dir=as_path(
                "property_extraction_dir", "property_extractions"
            ),
            property_llm_enabled=_as_bool(raw.get("property_llm_enabled", True)),
            property_llm_timeout_seconds=int(
                raw.get("property_llm_timeout_seconds", 180)
            ),
        )

    def ensure_directories(self) -> None:
        if not self.input_dir.exists():
            raise FileNotFoundError(f"Input directory does not exist: {self.input_dir}")
        self.processing_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.error_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.ocr_dir.mkdir(parents=True, exist_ok=True)
        self.excel_path.parent.mkdir(parents=True, exist_ok=True)
        if self.property_extraction_dir:
            self.property_extraction_dir.mkdir(parents=True, exist_ok=True)


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "sim"}
    return bool(value)
