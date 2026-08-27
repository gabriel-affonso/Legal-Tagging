from __future__ import annotations

import logging
from pathlib import Path
import shutil
import subprocess

from .models import ExtractedText


LOGGER = logging.getLogger(__name__)


def extract_pdf_text(path: Path, max_pages: int, max_chars: int) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("Missing dependency: install pypdf with `pip install -r requirements.txt`.") from exc

    reader = PdfReader(str(path))
    chunks: list[str] = []

    for page_number, page in enumerate(reader.pages, start=1):
        if page_number > max_pages:
            break
        text = page.extract_text() or ""
        text = " ".join(text.split())
        if text:
            chunks.append(f"[Page {page_number}] {text}")
        if sum(len(chunk) for chunk in chunks) >= max_chars:
            break

    combined = "\n\n".join(chunks)
    return combined[:max_chars]


def extract_text_with_optional_ocr(
    path: Path,
    *,
    max_pages: int,
    max_chars: int,
    ocr_enabled: bool,
    ocr_min_text_chars: int,
    ocr_dir: Path,
    ocr_language: str,
    ocr_timeout_seconds: int,
) -> ExtractedText:
    native_text = extract_pdf_text(path, max_pages=max_pages, max_chars=max_chars)
    native_chars = len(native_text.strip())

    if native_chars >= ocr_min_text_chars:
        return ExtractedText(
            text=native_text,
            source="native_pdf_text",
            native_text_chars=native_chars,
        )

    if not ocr_enabled:
        return ExtractedText(
            text=native_text,
            source="native_pdf_text",
            native_text_chars=native_chars,
            notes="OCR desativado e o texto nativo do PDF parece insuficiente.",
        )

    ocr_output = ocr_dir / f"{path.stem}__ocr.pdf"
    try:
        run_ocrmypdf(path, ocr_output, language=ocr_language, timeout_seconds=ocr_timeout_seconds)
    except RuntimeError as exc:
        return ExtractedText(
            text=native_text,
            source="native_pdf_text",
            native_text_chars=native_chars,
            notes=str(exc),
        )

    ocr_text = extract_pdf_text(ocr_output, max_pages=max_pages, max_chars=max_chars)
    ocr_chars = len(ocr_text.strip())
    if ocr_chars > native_chars:
        return ExtractedText(
            text=ocr_text,
            source="ocr_pdf_text",
            native_text_chars=native_chars,
            ocr_text_chars=ocr_chars,
        )

    return ExtractedText(
        text=native_text,
        source="native_pdf_text",
        native_text_chars=native_chars,
        ocr_text_chars=ocr_chars,
        notes="OCR executado, mas nao melhorou a quantidade de texto extraido.",
    )


def run_ocrmypdf(input_pdf: Path, output_pdf: Path, *, language: str, timeout_seconds: int) -> None:
    executable = shutil.which("ocrmypdf")
    if not executable:
        raise RuntimeError(
            "OCR necessario, mas `ocrmypdf` nao esta instalado. Instale com `brew install ocrmypdf`."
        )

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    command = [
        executable,
        "--skip-text",
        "--deskew",
        "--rotate-pages",
        "--language",
        language,
        str(input_pdf),
        str(output_pdf),
    ]
    LOGGER.info("Running OCR: %s", " ".join(command))
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"OCR failed for {input_pdf.name}: {message[:600]}")
