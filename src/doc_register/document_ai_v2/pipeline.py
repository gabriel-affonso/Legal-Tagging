"""Small, local Document AI v2 pipeline used by the rent re-search.

It parses one page at a time, records geometry, and derives canonical text
from visual order.  OCR engines are adapters: an unavailable optional engine
is a page warning, never a reason to promote legacy PDF text as truth.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from typing import Literal

import fitz

Route = Literal["NATIVE_TEXT", "CLEAN_SCAN", "NOISY_SCAN", "COMPLEX_LAYOUT", "LOW_CONFIDENCE"]


@dataclass(frozen=True)
class V2Config:
    pipeline_version: str = "document-ai-v2.0.0"
    render_dpi: int = 250
    native_text_min_quality: float = .90
    rapidocr_min_confidence: float = .82
    max_workers: int = 1
    max_concurrent_pages: int = 1
    max_memory_mb: int = 12000
    enable_rapidocr: bool = True
    enable_paddleocr_vl: bool = False  # explicit opt-in; model is not bundled
    debug_artifacts: bool = False


@dataclass(frozen=True)
class Block:
    id: str
    page: int
    type: str
    text: str
    bbox: tuple[float, float, float, float]
    confidence: float
    source_engine: str
    reading_order: int


@dataclass(frozen=True)
class PageFeatures:
    page: int
    width: float
    height: float
    rotation: int
    has_text_layer: bool
    text_chars: int
    spans: int
    image_count: int
    image_coverage: float
    noise_ratio: float
    fragmentation: float
    spatial_inconsistency: float
    order_inconsistency: float
    text_layer_quality: float


def _hash(value: object) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _file_digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quality(words: list[dict], page: fitz.Page) -> tuple[float, float, float, float]:
    text = " ".join(str(w["text"]) for w in words)
    noise = sum(not (c.isalnum() or c.isspace() or c in ".,;:()/%€ºª-–") for c in text) / max(1, len(text))
    fragment = sum(len(str(w["text"]).strip()) <= 3 for w in words) / max(1, len(words))
    rect = page.rect
    invalid = sum(w["bbox"][0] < rect.x0 or w["bbox"][1] < rect.y0 or w["bbox"][2] > rect.x1 or w["bbox"][3] > rect.y1 for w in words)
    # PDF extraction sequence should broadly correlate with geometric sequence.
    geometric = sorted(range(len(words)), key=lambda i: (round(words[i]["bbox"][1] / 8), words[i]["bbox"][0]))
    disorder = sum(index != value for index, value in enumerate(geometric)) / max(1, len(words))
    spatial = invalid / max(1, len(words))
    return noise, fragment, spatial, disorder


class PageRouter:
    """Pure routing policy; it can be unit-tested without loading a model."""
    def __init__(self, config: V2Config): self.config = config

    def classify(self, f: PageFeatures) -> Route:
        if f.has_text_layer and f.text_layer_quality >= self.config.native_text_min_quality and not f.image_count:
            return "NATIVE_TEXT"
        if f.image_count and f.text_layer_quality < .45:
            return "NOISY_SCAN" if f.noise_ratio > .08 or f.spatial_inconsistency > .08 else "CLEAN_SCAN"
        if f.order_inconsistency > .35 or f.fragmentation > .70:
            return "COMPLEX_LAYOUT"
        return "LOW_CONFIDENCE"


class NativePdfParser:
    """Visual native parser. It deliberately ignores ``Page.get_text()`` order."""
    def parse(self, page: fitz.Page, number: int) -> list[Block]:
        words = page.get_text("words", sort=False)
        rows = sorted(words, key=lambda w: (round(w[1] / max(1.0, w[3] - w[1]) * 2), w[0]))
        blocks: list[Block] = []
        for order, word in enumerate(rows, 1):
            x0, y0, x1, y1, value, *_ = word
            value = str(value).strip()
            if value:
                blocks.append(Block(f"p{number}_b{order}", number, "paragraph", value,
                    (float(x0), float(y0), float(x1), float(y1)), 1.0, "pymupdf_native", order))
        return blocks


class RapidOCRParser:
    """Optional CPU adapter. RapidOCR is imported only if the user installs it."""
    def parse(self, image: bytes, number: int) -> list[Block]:
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
        except ImportError as exc:
            raise RuntimeError("rapidocr_not_installed") from exc
        result, _ = RapidOCR()(image)
        blocks = []
        for order, row in enumerate(result or [], 1):
            points, text, confidence = row
            xs, ys = zip(*points)
            blocks.append(Block(f"p{number}_b{order}", number, "paragraph", str(text),
                (float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))), float(confidence), "rapidocr", order))
        return blocks


class PaddleOCRVLParser:
    """Boundary for the advanced parser.

    No model is downloaded implicitly. A deployment enabling this backend must
    provide a pinned, locally mounted PaddleOCR-VL model and adapter; otherwise
    the page stays reviewable instead of silently using legacy OCR.
    """
    def parse(self, image: bytes, number: int) -> list[Block]:
        raise RuntimeError("paddleocr_vl_backend_requires_pinned_local_adapter")


def _group_lines(blocks: list[Block]) -> list[Block]:
    """Rebuild lines by geometry; never concatenate the legacy OCR text layer."""
    grouped: list[list[Block]] = []
    for block in sorted(blocks, key=lambda b: (b.bbox[1], b.bbox[0])):
        for line in grouped:
            center = (line[0].bbox[1] + line[0].bbox[3]) / 2
            if abs((block.bbox[1] + block.bbox[3]) / 2 - center) <= max(4, (block.bbox[3] - block.bbox[1]) * .55):
                line.append(block); break
        else: grouped.append([block])
    rebuilt = []
    for order, line in enumerate(grouped, 1):
        line.sort(key=lambda b: b.bbox[0])
        rebuilt.append(Block(f"p{line[0].page}_l{order}", line[0].page, "paragraph", " ".join(b.text for b in line),
            (min(b.bbox[0] for b in line), min(b.bbox[1] for b in line), max(b.bbox[2] for b in line), max(b.bbox[3] for b in line)),
            min(b.confidence for b in line), line[0].source_engine, order))
    return rebuilt


class DocumentAIV2Pipeline:
    def __init__(self, cache_root: Path, config: V2Config = V2Config()):
        self.cache_root, self.config, self.router = Path(cache_root), config, PageRouter(config)

    def inspect(self, source: Path) -> list[PageFeatures]:
        features = []
        with fitz.open(source) as pdf:
            for i, page in enumerate(pdf, 1):
                words = [{"text": w[4], "bbox": w[:4]} for w in page.get_text("words", sort=False)]
                noise, fragments, spatial, disorder = _quality(words, page)
                chars = sum(len(w["text"]) for w in words)
                # Trust is an explicit heuristic, not evidence that text exists.
                quality = max(0., min(1., 1 - noise * 3 - fragments * .15 - spatial * 2 - disorder * .35)) if chars else 0.
                images = page.get_images(full=True)
                features.append(PageFeatures(i, page.rect.width, page.rect.height, page.rotation, bool(chars), chars, len(words), len(images),
                    1.0 if images and not chars else 0.0, noise, fragments, spatial, disorder, quality))
        return features

    def process(self, source: Path, *, force: bool = False) -> dict:
        source = Path(source).resolve(); digest = _file_digest(source)
        fingerprint = _hash({"config": asdict(self.config), "source": digest})
        destination = self.cache_root / digest / fingerprint / "canonical.json"
        if destination.exists() and not force: return json.loads(destination.read_text())
        started = time.monotonic(); features = self.inspect(source); pages = []; warnings = []
        with fitz.open(source) as pdf:
            for feature in features:
                page = pdf[feature.page - 1]; route = self.router.classify(feature)
                blocks: list[Block] = []
                try:
                    if route == "NATIVE_TEXT": blocks = NativePdfParser().parse(page, feature.page)
                    elif route == "COMPLEX_LAYOUT" and self.config.enable_paddleocr_vl:
                        pix = page.get_pixmap(dpi=self.config.render_dpi, alpha=False)
                        blocks = PaddleOCRVLParser().parse(pix.tobytes("png"), feature.page)
                    elif self.config.enable_rapidocr:
                        pix = page.get_pixmap(dpi=self.config.render_dpi, alpha=False)
                        blocks = RapidOCRParser().parse(pix.tobytes("png"), feature.page)
                    if not blocks: raise RuntimeError("visual_parser_returned_no_blocks")
                except Exception as exc:
                    warnings.append(f"page_{feature.page}:{exc}")
                lines = _group_lines(blocks)
                pages.append({"number": feature.page, "width": feature.width, "height": feature.height, "rotation": feature.rotation,
                    "route": route, "features": asdict(feature), "blocks": [asdict(b) for b in lines], "status": "processed" if lines else "failed"})
        canonical_text = "\n\n".join(f"[Page {p['number']}]\n" + "\n".join(b["text"] for b in p["blocks"]) for p in pages)
        output = {"schema_version": "2.0", "pipeline_version": self.config.pipeline_version, "document": {"source_path": str(source), "sha256": digest},
            "pages": pages, "canonical_text": canonical_text, "quality": {"requires_review": bool(warnings), "warnings": warnings},
            "processing": {"config": asdict(self.config), "elapsed_seconds": round(time.monotonic()-started, 3), "routes": {r: sum(p["route"] == r for p in pages) for r in set(p["route"] for p in pages)}}}
        destination.parent.mkdir(parents=True, exist_ok=True); destination.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        return output
