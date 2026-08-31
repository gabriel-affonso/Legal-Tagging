from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from .models import RenderedPage


def render_pdf_page(
    path: Path,
    page_number: int,
    *,
    dpi: int,
    max_pixels: int,
) -> RenderedPage:
    """Render one page in memory; no confidential image is written to disk."""
    try:
        try:
            import pymupdf as fitz  # type: ignore[import-not-found]
        except ImportError:
            import fitz  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required for Step 3.5 visual recovery.") from exc

    with fitz.open(str(path)) as document:
        if page_number < 1 or page_number > len(document):
            raise ValueError(f"PDF page {page_number} is outside the document range")
        page = document[page_number - 1]
        scale = max(0.5, float(dpi) / 72.0)
        estimated_pixels = max(1.0, page.rect.width * scale * page.rect.height * scale)
        if estimated_pixels > max_pixels:
            scale *= (max_pixels / estimated_pixels) ** 0.5
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        if pixmap.width <= 32 or pixmap.height <= 32:
            # Qwen3-VL rejects images at or below its 32-pixel resize factor.
            # A normal PDF page is far larger; this protects unusual tiny PDFs
            # from crashing the local model runner.
            raise ValueError("rendered image is too small for qwen3-vl")
        image = pixmap.tobytes("png")
    return RenderedPage(
        page_number=page_number,
        image_base64=base64.b64encode(image).decode("ascii"),
        image_sha256=hashlib.sha256(image).hexdigest(),
        width=pixmap.width,
        height=pixmap.height,
    )
