from __future__ import annotations

from dataclasses import dataclass, field
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
    # Step 3.5: visual recovery is deliberately opt-in.  The first rollout is
    # shadow-only, so a vision model can never change the register by default.
    vision_enabled: bool = False
    vision_model: str = "qwen3-vl:4b-instruct"
    vision_timeout_seconds: int = 240
    vision_first_page_count: int = 2
    vision_max_pages_per_document: int = 2
    vision_render_dpi: int = 160
    vision_max_image_pixels: int = 3_000_000
    vision_ocr_quality_threshold: float = 0.60
    vision_critical_candidate_threshold: float = 0.78
    vision_apply_proposals: bool = False
    vision_auto_accept_enabled: bool = False
    vision_auto_accept_confidence: float = 0.95
    vision_keep_alive: str = "0"
    vision_max_failures_per_batch: int = 3
    vision_recall_mode: bool = False
    vision_recall_first_pages: int = 5
    vision_recall_last_pages: int = 3
    # Step 3.7: focused evidence extraction.  It is opt-in so every existing
    # Step 3.6 command keeps its current behaviour.
    step_3_7_enabled: bool = False
    step_3_7_maximum_contract_pages: int = 3
    step_3_7_maximum_cadastral_pages: int = 1
    step_3_7_maximum_total_pages: int = 4
    step_3_7_maximum_chars_per_page: int = 12_000
    step_3_7_maximum_total_context_chars: int = 40_000
    step_3_7_cadastral_page_threshold: int = 7
    step_3_7_cadastral_min_indicator_diversity: int = 2
    step_3_7_cadastral_ocr_quality_threshold: float = 0.60
    step_3_7_maximum_cadastral_visual_candidates: int = 3
    step_3_7_cadastral_indicator_scores: dict[str, int] = field(default_factory=dict)
    # Step 4.0 normalized operational workbook.  ``normalized`` is the new
    # default; ``legacy`` and ``both`` retain the historical register during
    # the migration window.
    output_format: str = "normalized"
    normalized_output_path: Path | None = None
    normalized_template_path: Path | None = None
    normalized_batch_id: str = ""
    normalized_reference_mg_context: str = ""

    @classmethod
    def from_json(cls, path: Path) -> "AppConfig":
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)

        base = path.parent
        step_3_7 = raw.get("step_3_7", {})
        if not isinstance(step_3_7, dict):
            raise ValueError("config.json: step_3_7 must be a JSON object")

        def step37_value(key: str, default: object) -> object:
            # Flat keys are accepted for deployments that cannot yet emit a
            # nested object, but the documented shape remains ``step_3_7``.
            return raw.get(f"step_3_7_{key}", step_3_7.get(key, default))

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
            vision_enabled=_as_bool(raw.get("vision_enabled", False)),
            vision_model=str(raw.get("vision_model", "qwen3-vl:4b-instruct")),
            vision_timeout_seconds=int(raw.get("vision_timeout_seconds", 240)),
            vision_first_page_count=int(raw.get("vision_first_page_count", 2)),
            vision_max_pages_per_document=int(raw.get("vision_max_pages_per_document", 2)),
            vision_render_dpi=int(raw.get("vision_render_dpi", 160)),
            vision_max_image_pixels=int(raw.get("vision_max_image_pixels", 3_000_000)),
            vision_ocr_quality_threshold=float(raw.get("vision_ocr_quality_threshold", 0.60)),
            vision_critical_candidate_threshold=float(raw.get("vision_critical_candidate_threshold", 0.78)),
            vision_apply_proposals=_as_bool(raw.get("vision_apply_proposals", False)),
            vision_auto_accept_enabled=_as_bool(raw.get("vision_auto_accept_enabled", False)),
            vision_auto_accept_confidence=float(raw.get("vision_auto_accept_confidence", 0.95)),
            vision_keep_alive=str(raw.get("vision_keep_alive", "0")),
            vision_max_failures_per_batch=int(raw.get("vision_max_failures_per_batch", 3)),
            vision_recall_mode=_as_bool(raw.get("vision_recall_mode", False)),
            vision_recall_first_pages=int(raw.get("vision_recall_first_pages", 5)),
            vision_recall_last_pages=int(raw.get("vision_recall_last_pages", 3)),
            step_3_7_enabled=_as_bool(step37_value("enabled", False)),
            step_3_7_maximum_contract_pages=max(
                1, int(step37_value("maximum_contract_pages", 3))
            ),
            step_3_7_maximum_cadastral_pages=max(
                0, int(step37_value("maximum_cadastral_pages", 1))
            ),
            step_3_7_maximum_total_pages=max(
                1, int(step37_value("maximum_total_pages", 4))
            ),
            step_3_7_maximum_chars_per_page=max(
                500, int(step37_value("maximum_chars_per_page", 12_000))
            ),
            step_3_7_maximum_total_context_chars=max(
                2_000, int(step37_value("maximum_total_context_chars", 40_000))
            ),
            step_3_7_cadastral_page_threshold=max(
                1, int(step37_value("cadastral_page_threshold", 7))
            ),
            step_3_7_cadastral_min_indicator_diversity=max(
                1, int(step37_value("cadastral_min_indicator_diversity", 2))
            ),
            step_3_7_cadastral_ocr_quality_threshold=max(
                0.0,
                min(
                    1.0,
                    float(step37_value("cadastral_ocr_quality_threshold", 0.60)),
                ),
            ),
            step_3_7_maximum_cadastral_visual_candidates=max(
                0,
                int(step37_value("maximum_cadastral_visual_candidates", 3)),
            ),
            step_3_7_cadastral_indicator_scores={
                str(key): int(value)
                for key, value in dict(
                    step37_value("cadastral_indicator_scores", {}) or {}
                ).items()
            },
            output_format=str(raw.get("output_format", "normalized")).strip().lower(),
            normalized_output_path=(
                as_path("normalized_output_path")
                if raw.get("normalized_output_path") else None
            ),
            normalized_template_path=(
                as_path("normalized_template_path")
                if raw.get("normalized_template_path") else None
            ),
            normalized_batch_id=str(raw.get("normalized_batch_id", "")).strip(),
            normalized_reference_mg_context=str(
                raw.get("normalized_reference_mg_context", "")
            ).strip(),
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
