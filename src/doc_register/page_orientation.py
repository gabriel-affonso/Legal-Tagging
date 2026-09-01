"""Pre-OCR orientation assessment for Step 3.6.

When Tesseract is available we score 0/90/180/270 from the rendered image;
otherwise PDF rotation metadata is retained as a safe fallback. OCRmyPDF still
performs its own rotate pass, so an unavailable local Tesseract never blocks a
document.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import shutil
import subprocess
from pathlib import Path


@dataclass(frozen=True)
class PageOrientation:
    page: int
    selected_degrees: int
    scores: dict[int, float]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def detect_page_orientations(path: Path, *, max_pages: int, language: str) -> list[PageOrientation]:
    try:
        try:
            import pymupdf as fitz  # type: ignore[import-not-found]
        except ImportError:
            import fitz  # type: ignore[import-not-found]
    except ImportError:
        return []
    tesseract = shutil.which("tesseract")
    results: list[PageOrientation] = []
    try:
        with fitz.open(str(path)) as document:
            for index, page in enumerate(document, start=1):
                if index > max_pages:
                    break
                metadata_rotation = int(getattr(page, "rotation", 0) or 0) % 360
                scores: dict[int, float] = {}
                if tesseract:
                    for degrees in (0, 90, 180, 270):
                        matrix = fitz.Matrix(1.35, 1.35)
                        rotate = getattr(matrix, "prerotate", None) or getattr(matrix, "pre_rotate")
                        pixmap = page.get_pixmap(matrix=rotate(degrees), alpha=False)
                        scores[degrees] = _linguistic_score(_ocr_bytes(tesseract, pixmap.tobytes("png"), language))
                else:
                    # Metadata cannot distinguish upside-down scans, but it is
                    # deterministic and correctly captures pages already rotated
                    # by the PDF producer.
                    scores = {degrees: (1.0 if degrees == (-metadata_rotation) % 360 else 0.0) for degrees in (0, 90, 180, 270)}
                selected = max(scores, key=lambda degrees: (scores[degrees], -degrees))
                results.append(PageOrientation(index, selected, scores))
    except (OSError, RuntimeError, ValueError, AttributeError):
        return []
    return results


def _ocr_bytes(executable: str, image: bytes, language: str) -> str:
    try:
        completed = subprocess.run(
            [executable, "stdin", "stdout", "--psm", "6", "-l", language],
            input=image, capture_output=True, timeout=25, check=False,
        )
        return completed.stdout.decode("utf-8", errors="replace") if completed.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _linguistic_score(text: str) -> float:
    if not text.strip():
        return 0.0
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]{3,}", text)
    common = re.findall(r"\b(?:contrato|arrendamento|artigo|matricial|sec[cç][aã]o|titular|pr[eé]dio|renda|outorgante|assinatura)\b", text, re.I)
    printable = sum(ch.isalnum() or ch.isspace() or ch in ".,;:-()/" for ch in text)
    return round(min(1.0, 0.04 * len(words) + 0.12 * len(common) + 0.15 * printable / max(len(text), 1)), 4)


__all__ = ["PageOrientation", "detect_page_orientations"]
