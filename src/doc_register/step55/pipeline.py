"""Non-destructive Step 5.5 orchestration and staging output."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import time

import pymupdf as fitz

from .canonical import CanonicalResult, ExtractionTask, stable_id
from .document_map import Observation, map_documents
from .extraction import extract
from ..step50.recognition import ocr, render_crop


@dataclass(frozen=True)
class PipelineConfig:
    max_pages: int = 200
    max_file_bytes: int = 200 * 1024 * 1024
    native_min_chars: int = 12
    ocr_enabled: bool = True
    ocr_language: str = "por+eng"
    ocr_timeout: int = 90
    ocr_dpi: int = 220
    ocr_max_pixels: int = 8_000_000
    rules_enabled: bool = True
    plan_llm_tasks: bool = True

    def __post_init__(self):
        if self.max_pages < 1 or self.max_file_bytes < 1 or self.native_min_chars < 1 or self.ocr_timeout < 1 or self.ocr_dpi < 72 or self.ocr_max_pixels < 1:
            raise ValueError("Step 5.5 limits must be positive")

    @classmethod
    def read(cls, path: Path | None):
        return cls() if path is None else cls(**json.loads(path.read_text(encoding="utf-8")))


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return __import__("hashlib").file_digest(handle, "sha256").hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    from ..step50.storage import atomic_json
    atomic_json(path, value)


def _observations(source: Path, config: PipelineConfig) -> tuple[list[Observation], list[str]]:
    result, issues = [], []
    with fitz.open(source) as pdf:
        if pdf.needs_pass:
            raise ValueError("protected_document")
        if not len(pdf):
            raise ValueError("empty_document")
        for index, page in enumerate(pdf):
            if index >= config.max_pages:
                issues.append(f"page_{index + 1}:not_processed:max_pages")
                continue
            blocks = [item for item in page.get_text("blocks", sort=True) if item[6] == 0 and item[4].strip()]
            native_text = "".join(item[4] for item in blocks)
            if not blocks or len(native_text.strip()) < config.native_min_chars:
                # OCR is a second observation, not a replacement for the native
                # observation.  Failed/unavailable OCR leaves a visible gap.
                if config.ocr_enabled:
                    try:
                        image, recipe = render_crop(page, list(page.rect), dpi=config.ocr_dpi, max_pixels=config.ocr_max_pixels)
                        recognized = ocr(image, language=config.ocr_language, timeout=config.ocr_timeout)
                        if recognized.strip():
                            result.append(Observation("region-" + stable_id(index, "ocr", recipe["sha256"]), index + 1,
                                                      recognized, tuple(float(value) for value in page.rect), "ocr"))
                        else:
                            issues.append(f"page_{index + 1}:low_text:ocr_empty")
                    except Exception as exc:
                        issues.append(f"page_{index + 1}:low_text:ocr_failure:{str(exc)[:120]}")
                else:
                    issues.append(f"page_{index + 1}:low_text:ocr_disabled")
            if not blocks:
                # A page with no native text remains unknown until a reader has
                # actually transcribed it; it is never labelled blank here.
                if not config.ocr_enabled:
                    result.append(Observation("region-" + stable_id(index, "low_text"), index + 1, "[no native text]", None, "native"))
                continue
            for block_index, block in enumerate(blocks):
                result.append(Observation("region-" + stable_id(index, block_index, block[:4], block[4]), index + 1,
                                          block[4], tuple(float(value) for value in block[:4]), "native"))
    return result, issues


def _tasks(logical_documents, evidence, assertions) -> list[ExtractionTask]:
    by_document = {item.id: [] for item in logical_documents}
    evidence_document = {item.id: item.region_id for item in evidence}
    region_documents = {region: item.id for item in logical_documents for region in item.region_ids}
    for assertion in assertions:
        for evidence_id in assertion.evidence_ids:
            document_id = region_documents[evidence_document[evidence_id]]
            by_document[document_id].append(assertion)
    output = []
    requirements = {
        "lease_contract": (("parts", {"contract_role"}), ("properties", {"cadastral_article"}),
                            ("temporal", {"term_duration", "term_start_trigger", "signed_date"}),
                            ("financial", {"rent_term"})),
        "payment_receipt": (("financial", {"payment_gross_amount", "payment_net_amount"}),),
        "payment_correspondence": (("financial", {"payment_gross_amount", "payment_net_amount"}),),
        "cadastral_record": (("properties", {"cadastral_article", "area_measurement"}),),
    }
    predicates = {
        "parts": ["contract_role", "representation", "tax_id"],
        "properties": ["property_name", "cadastral_article", "registry_description", "area_measurement"],
        "temporal": ["signed_date", "term_duration", "term_start_trigger"],
        "financial": ["rent_term", "reservation_obligation", "payment_gross_amount", "withholding_tax_amount", "payment_net_amount"],
    }
    for logical in logical_documents:
        present = {assertion.predicate for assertion in by_document[logical.id]}
        relevant = [item.id for item in evidence if item.region_id in logical.region_ids]
        for task_type, expected in requirements.get(logical.source_type, ()):
            missing = expected - present
            if missing:
                output.append(ExtractionTask(id="task-" + stable_id(logical.id, task_type), task_type=task_type,
                                             logical_document_id=logical.id, evidence_ids=relevant,
                                             permitted_predicates=predicates[task_type],
                                             reason="missing deterministic predicates: " + ", ".join(sorted(missing))))
    return output


def staging_projection(result: CanonicalResult) -> dict:
    """A review-only projection. It is intentionally not an Excel writer."""
    rows = []
    entities = {item.id: item for item in result.entities}
    for assertion in result.assertions:
        rows.append({"subject_type": entities[assertion.subject_id].kind, "subject_id": assertion.subject_id,
                     "subject_label": entities[assertion.subject_id].label, "predicate": assertion.predicate,
                     "value": assertion.typed_value, "scope": assertion.scope,
                     "acceptance_status": assertion.acceptance_status, "evidence_ids": assertion.evidence_ids})
    return {"kind": "step55_staging_projection", "source_sha256": result.source_sha256,
            "export_blocked": True, "reason": "Step 5.5 does not write operational workbooks", "rows": rows}


class Pipeline:
    def __init__(self, output: Path, config: PipelineConfig = PipelineConfig()):
        self.output = output.resolve()
        self.config = config

    def process(self, source: Path, *, force: bool = False) -> Path:
        source = source.resolve()
        if source.suffix.casefold() != ".pdf":
            raise ValueError("pdf_required")
        if source.stat().st_size > self.config.max_file_bytes:
            raise ValueError("file_size_budget_exceeded")
        digest = _sha256(source)
        root = self.output / digest / ("step55-" + stable_id(asdict(self.config), "5.5.0"))
        destination = root / "result.json"
        if destination.exists() and not force:
            return destination
        started = time.monotonic()
        observations, issues = _observations(source, self.config)
        logical_documents = map_documents(observations)
        logical_by_region = {region: document for document in logical_documents for region in document.region_ids}
        evidence, entities, assertions, relations = [], [], [], []
        for observation in observations:
            logical = logical_by_region[observation.id]
            if not self.config.rules_enabled or logical.page_state != "processed":
                continue
            new_evidence, new_entities, new_assertions, new_relations = extract(observation, logical, entities)
            evidence.extend(new_evidence)
            entities.extend(new_entities)
            assertions.extend(new_assertions)
            relations.extend(new_relations)
        tasks = _tasks(logical_documents, evidence, assertions) if self.config.plan_llm_tasks else []
        coverage = {item.id: "processed" if item.page_state == "processed" else item.page_state for item in logical_documents}
        result = CanonicalResult(source_sha256=digest, source_path=str(source), logical_documents=logical_documents,
                                 evidence=evidence, entities=entities, assertions=assertions, relations=relations,
                                 tasks=tasks, coverage=coverage, issues=issues,
                                 metrics={"elapsed_seconds": time.monotonic() - started, "observations": len(observations),
                                          "assertions": len(assertions), "entities": len(entities), "planned_tasks": len(tasks),
                                          "model_calls": 0})
        root.mkdir(parents=True, exist_ok=True)
        _atomic_json(destination, result.model_dump(mode="json"))
        _atomic_json(root / "staging-projection.json", staging_projection(result))
        return destination
