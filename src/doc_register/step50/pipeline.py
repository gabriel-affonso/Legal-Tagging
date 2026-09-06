"""Step 5.0 sequential pipeline. Final decisions have exactly one publisher."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import time
import uuid

import pymupdf as fitz
from .extraction import deterministic_candidates, model_candidates, resolve, segment_pages
from .models import LocalModels, PROMPT_VERSION
from .recognition import inventory_page
from .schema import Document, Entity, FieldResult, Page, ProcessingResult, Region, Relationship, identity
from .storage import atomic_json, digest_file, locked, runtime


@dataclass(frozen=True)
class PipelineConfig:
    max_pages: int = 500
    max_file_bytes: int = 200_000_000
    max_regions_per_page: int = 12
    max_document_seconds: int = 3600
    context_chars: int = 6000
    native_min_chars: int = 80
    dpi: int = 300
    max_pixels: int = 8_000_000
    ocr_enabled: bool = True
    ocr_language: str = 'por+eng'
    ocr_timeout: int = 90
    text_enabled: bool = True
    vision_enabled: bool = True
    ollama_url: str = 'http://127.0.0.1:11434'
    text_model: str = 'qwen3:8b'
    vision_model: str = 'qwen3-vl:4b-instruct'
    model_revision: str = 'unverified'
    model_timeout: int = 180
    max_model_calls: int = 80
    max_model_failures: int = 2

    def __post_init__(self):
        for key, value in asdict(self).items():
            expected = type(self.__dataclass_fields__[key].default)
            if type(value) is not expected:
                raise ValueError(f'{key} must be {expected.__name__}')
            if isinstance(value, int) and not isinstance(value, bool) and value <= 0:
                raise ValueError(f'{key} must be positive')
        if self.context_chars < 600:
            raise ValueError('context_chars must be at least 600')
        if self.model_revision == '':
            raise ValueError('model_revision must identify provisioned models or be unverified')

    @classmethod
    def read(cls, path):
        if path is None:
            return cls()
        return cls(**json.loads(Path(path).read_text()))


class Pipeline:
    def __init__(self, output: Path, config=PipelineConfig()):
        self.output = output.resolve()
        self.config = config

    def process(self, source: Path, *, force=False) -> Path:
        source = source.resolve()
        if source.stat().st_size > self.config.max_file_bytes:
            raise ValueError('file_size_budget_exceeded')
        digest = digest_file(source)
        root = self.output / digest
        with locked(root / '.lock'):
            provenance = runtime()
            fingerprint = identity(asdict(self.config), provenance['code_sha256'], provenance['dependencies'], PROMPT_VERSION)
            cache = root / fingerprint
            cache.mkdir(parents=True, exist_ok=True)
            original = root / 'original.pdf'
            if not original.exists():
                temporary = root / 'original.copying'
                shutil.copyfile(source, temporary)
                if digest_file(temporary) != digest:
                    temporary.unlink()
                    raise ValueError('input_changed_during_ingestion')
                temporary.replace(original)
            elif digest_file(original) != digest:
                raise ValueError('immutable_original_hash_mismatch')
            pointer = cache / 'latest.json'
            # Unpinned local model tags must not silently reuse model-dependent outputs.
            reusable = self.config.model_revision != 'unverified' or not (self.config.text_enabled or self.config.vision_enabled)
            if pointer.exists() and not force and reusable:
                previous = Path(json.loads(pointer.read_text())['result'])
                if previous.exists():
                    return previous
            pending = cache / 'pending.json'
            if pending.exists() and not force and reusable:
                run_id = json.loads(pending.read_text())['run_id']
            else:
                run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:10]
                atomic_json(pending, {'run_id': run_id})
            run = cache / run_id
            run.mkdir(exist_ok=True)
            result = ProcessingResult(Document(digest, digest, str(original)), run_id, fingerprint,
                                      provenance={**provenance, 'config': asdict(self.config), 'prompt_version': PROMPT_VERSION,
                                                  'source_name': source.name, 'started_at': datetime.now(timezone.utc).isoformat()})
            started = time.monotonic()
            models = LocalModels(self.config)
            models.deadline = started + self.config.max_document_seconds
            try:
                with fitz.open(original) as document:
                    result.document.page_count = len(document)
                    if document.needs_pass:
                        raise ValueError('protected_document')
                    if len(document) == 0:
                        raise ValueError('empty_document')
                    page_hashes = {}
                    for index in range(len(document)):
                        if index >= self.config.max_pages or time.monotonic() - started >= self.config.max_document_seconds:
                            result.pages.append(Page(index + 1, 0, 0, 0, 'not_processed', issues=['incomplete_coverage:budget']))
                            result.incomplete_coverage = True
                            continue
                        checkpoint = run / 'pages' / f'{index + 1}.json'
                        if checkpoint.exists():
                            data = json.loads(checkpoint.read_text())
                            data['regions'] = [Region(**r) for r in data['regions']]
                            page = Page(**data)
                        else:
                            page = inventory_page(document, index, self.config, run / 'crops', models.transcribe if self.config.vision_enabled else None, models.deadline)
                            atomic_json(checkpoint, asdict(page))
                        page_key = identity([(r.text, r.bbox, r.image.get('sha256')) for r in page.regions])
                        if page_key in page_hashes:
                            page.duplicate_of = page_hashes[page_key]
                        else:
                            page_hashes[page_key] = page.number
                        result.pages.append(page)
            except Exception as exc:
                result.document.status = 'technical_error'
                result.issues.append(str(exc)[:300])
                result.incomplete_coverage = True
            result.segments = segment_pages(result.pages, self.config.context_chars)
            for page in result.pages:
                if page.status != 'processed' or any('incomplete_coverage' in i for i in page.issues):
                    result.incomplete_coverage = True
                for region in page.regions:
                    if any(i.startswith(('recognition_failure', 'untranscribed_region', 'visual_failure', 'visual_not_examined')) for i in region.issues):
                        result.incomplete_coverage = True
                    result.candidates.extend(deterministic_candidates(region))
            region_map = {r.id: r for p in result.pages for r in p.regions}
            if self.config.text_enabled:
                for segment in result.segments:
                    if time.monotonic() - started >= self.config.max_document_seconds:
                        result.issues.append('extraction_budget_exhausted')
                        result.incomplete_coverage = True
                        break
                    region = region_map[segment.region_ids[0]]
                    checkpoint = run / 'extraction' / f'{segment.id}.json'
                    try:
                        if checkpoint.exists():
                            response = json.loads(checkpoint.read_text())
                        else:
                            response = models.extract({'source_type': segment.source_type, 'logical_document': segment.logical_document,
                                                       'regions': [{'region_id': region.id, 'text': segment.text}]})
                            # Parse first; invalid outputs cannot become valid cached observations.
                            model_candidates(region, response)
                            atomic_json(checkpoint, response)
                        result.candidates.extend(model_candidates(region, response))
                    except Exception as exc:
                        result.issues.append(f'extraction_failure:{segment.id}:{str(exc)[:160]}')
                        result.incomplete_coverage = True
            else:
                result.issues.append('text_model_disabled:label_rules_only')
            result.candidates = list({c.id: c for c in result.candidates}.values())
            result.fields = resolve(result.candidates)
            # Expose expected gaps; absence is never treated as a successful extraction.
            for logical in sorted({s.logical_document for s in result.segments if s.source_type in {'contract', 'amendment'}}):
                for field in ['signed_date', 'contract_start_date', 'contract_end_date', 'rent']:
                    if not any(f.logical_document == logical and f.field == field for f in result.fields):
                        result.fields.append(FieldResult(field, 'contract', logical, 'not_found', reasons=['no_anchored_candidate']))
            entities = {}
            for candidate in result.candidates:
                key = (candidate.logical_document, candidate.entity)
                entities[key] = Entity(candidate.entity, candidate.entity, 'unresolved', candidate.logical_document)
            result.entities = list(entities.values())
            for f in result.fields:
                if f.field in {'role', 'owner', 'represented_by', 'amends'} and f.status == 'accepted':
                    result.relationships.append(Relationship(f.entity, f.field, f.value, f.candidate_ids, f.status, f.logical_document))
            if result.document.status != 'technical_error':
                result.document.status = 'needs_review'
            # No dataset-backed whole-document policy exists yet. This is explicit, not a pseudo-confidence.
            result.issues.append('whole_document_acceptance_not_calibrated')
            result.metrics = {'elapsed_seconds': time.monotonic()-started, 'model_calls': models.calls,
                              'model_failures': models.failures, 'candidate_count': len(result.candidates),
                              'automatic_fields': sum(f.status == 'accepted' for f in result.fields),
                              'memory_tree_peak_bytes': None}
            destination = run / 'result.json'
            atomic_json(destination, result.to_dict())
            from .review import write_review
            write_review(result.to_dict(), run / 'review.html')
            atomic_json(pointer, {'result': str(destination)})
            pending.unlink(missing_ok=True)
            return destination
