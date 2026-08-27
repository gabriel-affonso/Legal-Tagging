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
        raise RuntimeError(
            "Missing dependency: install pypdf with `pip install -r requirements.txt`."
        ) from exc

    reader = PdfReader(str(path))
    chunks: list[str] = []
    current_chars = 0

    for page_number, page in enumerate(reader.pages, start=1):
        if page_number > max_pages:
            break

        text = page.extract_text() or ""
        text = " ".join(text.split())
        if text:
            chunk = f"[Page {page_number}] {text}"
            chunks.append(chunk)
            current_chars += len(chunk)

        if current_chars >= max_chars:
            break

    return "\n\n".join(chunks)[:max_chars]


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

    # Always check the OCR cache before deciding whether a new OCR run is allowed.
    # This permits reuse of OCR PDFs created by older pipeline versions even when
    # ocr_enabled is false because endpoint security blocks OCRmyPDF.
    ocr_output = ocr_dir / f"{path.stem}__ocr.pdf"
    cached_result = _read_cached_ocr(
        ocr_output,
        native_text=native_text,
        native_chars=native_chars,
        max_pages=max_pages,
        max_chars=max_chars,
    )
    if cached_result is not None:
        return cached_result

    if not ocr_enabled:
        return ExtractedText(
            text=native_text,
            source="native_pdf_text",
            native_text_chars=native_chars,
            notes=(
                "OCR novo desativado, nenhum OCR reutilizavel foi encontrado "
                "e o texto nativo do PDF parece insuficiente."
            ),
        )

    try:
        run_ocrmypdf(
            path,
            ocr_output,
            language=ocr_language,
            timeout_seconds=ocr_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        _remove_partial_file(ocr_output)
        return ExtractedText(
            text=native_text,
            source="native_pdf_text",
            native_text_chars=native_chars,
            notes=(
                f"OCR excedeu {ocr_timeout_seconds} segundos; "
                "processamento continuou com o texto nativo."
            ),
        )
    except PermissionError as exc:
        _remove_partial_file(ocr_output)
        return ExtractedText(
            text=native_text,
            source="native_pdf_text",
            native_text_chars=native_chars,
            notes=(
                f"OCR bloqueado pela seguranca do Windows ({exc}); "
                "processamento continuou com o texto nativo."
            ),
        )
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        _remove_partial_file(ocr_output)
        return ExtractedText(
            text=native_text,
            source="native_pdf_text",
            native_text_chars=native_chars,
            notes=f"OCR indisponivel; processamento continuou com o texto nativo. Detalhe: {exc}",
        )

    # Read the newly generated OCR file with the same safeguards used for cache.
    generated_result = _read_cached_ocr(
        ocr_output,
        native_text=native_text,
        native_chars=native_chars,
        max_pages=max_pages,
        max_chars=max_chars,
        cache_note="OCR executado e texto OCR utilizado.",
    )
    if generated_result is not None:
        return generated_result

    return ExtractedText(
        text=native_text,
        source="native_pdf_text",
        native_text_chars=native_chars,
        notes="OCR executado, mas o arquivo gerado nao produziu texto reutilizavel.",
    )


def _read_cached_ocr(
    ocr_output: Path,
    *,
    native_text: str,
    native_chars: int,
    max_pages: int,
    max_chars: int,
    cache_note: str = "OCR existente reutilizado; OCRmyPDF nao foi executado.",
) -> ExtractedText | None:
    if not ocr_output.is_file():
        return None

    try:
        if ocr_output.stat().st_size <= 0:
            LOGGER.warning("Ignoring empty cached OCR file: %s", ocr_output)
            return None

        ocr_text = extract_pdf_text(
            ocr_output,
            max_pages=max_pages,
            max_chars=max_chars,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        LOGGER.warning("Could not reuse cached OCR %s: %s", ocr_output, exc)
        return None

    ocr_chars = len(ocr_text.strip())
    if ocr_chars > native_chars and ocr_chars > 0:
        LOGGER.info("Using cached OCR PDF: %s (%s chars)", ocr_output.name, ocr_chars)
        return ExtractedText(
            text=ocr_text,
            source="cached_ocr_pdf_text",
            native_text_chars=native_chars,
            ocr_text_chars=ocr_chars,
            notes=cache_note,
        )

    LOGGER.warning(
        "Cached OCR did not improve extracted text: %s (native=%s, ocr=%s)",
        ocr_output.name,
        native_chars,
        ocr_chars,
    )
    return ExtractedText(
        text=native_text,
        source="native_pdf_text",
        native_text_chars=native_chars,
        ocr_text_chars=ocr_chars,
        notes="OCR existente encontrado, mas nao melhorou a quantidade de texto extraido.",
    )


def _remove_partial_file(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError as exc:
        LOGGER.warning("Could not remove partial OCR output %s: %s", path, exc)


def run_ocrmypdf(
    input_pdf: Path,
    output_pdf: Path,
    *,
    language: str,
    timeout_seconds: int,
) -> None:
    executable = shutil.which("ocrmypdf")
    if not executable:
        raise RuntimeError(
            "OCR necessario, mas `ocrmypdf` nao esta instalado ou nao esta no PATH."
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
