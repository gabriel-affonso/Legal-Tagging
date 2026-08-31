from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re
import shutil
import subprocess

from .models import ExtractedText


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class TextExtractionCandidate:
    text: str
    source: str
    native_text_chars: int = 0
    ocr_text_chars: int = 0
    quality_score: int = 0
    notes: str = ""


def extract_pdf_text(
    path: Path,
    max_pages: int,
    max_chars: int,
    *,
    preserve_layout: bool = False,
) -> str:
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
        text = _normalize_page_text(text, preserve_layout=preserve_layout)
        if text:
            chunk = f"[Page {page_number}] {text}"
            chunks.append(chunk)
            current_chars += len(chunk)

        if current_chars >= max_chars:
            break

    return "\n\n".join(chunks)[:max_chars]


def extract_pdf_annex_text(
    path: Path,
    *,
    max_chars: int = 100000,
) -> str:
    """Extract a broad region close to the end of a PDF for annex discovery.

    Pages are read backwards from the end so the character limit can never
    discard the final pages.  The resulting text is restored to page order.
    This is important for cadernetas that begin shortly before the final page
    rather than on the document's last page.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("Missing dependency: install pypdf with `pip install -r requirements.txt`.") from exc

    reader = PdfReader(str(path))
    page_count = len(reader.pages)
    if not page_count:
        return ""
    annex_count = max(min(30, page_count), int((page_count * 0.60) + 0.999))
    start_index = max(0, page_count - annex_count)
    chunks: list[tuple[int, str]] = []
    current_chars = 0
    for page_index in range(page_count - 1, start_index - 1, -1):
        page_text = _normalize_page_text(reader.pages[page_index].extract_text() or "", preserve_layout=True)
        if page_text:
            chunk = f"[Page {page_index + 1}] {page_text}"
            chunks.append((page_index, chunk))
            current_chars += len(chunk)
        if current_chars >= max_chars:
            break
    chunks.sort(key=lambda item: item[0])
    return "\n\n".join(chunk for _, chunk in chunks)


def extract_pdf_caderneta_text(
    path: Path,
    *,
    start_page: int = 1,
    max_chars: int | None = None,
) -> str:
    """Extract every PDF page for internal-caderneta discovery.

    A caderneta may appear anywhere in a contract.  The caller uses the page
    markers and the caderneta's own title/labels to select it afterwards, so
    this function intentionally has no positional assumption or text cutoff.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("Missing dependency: install pypdf with `pip install -r requirements.txt`.") from exc

    reader = PdfReader(str(path))
    start_index = min(max(0, start_page - 1), len(reader.pages))
    chunks: list[str] = []
    current_chars = 0
    for page_index in range(start_index, len(reader.pages)):
        page_text = _normalize_page_text(
            reader.pages[page_index].extract_text() or "",
            preserve_layout=True,
        )
        if not page_text:
            continue
        chunk = f"[Page {page_index + 1}] {page_text}"
        if max_chars is not None and current_chars + len(chunk) > max_chars and chunks:
            break
        chunks.append(chunk)
        current_chars += len(chunk)
    return "\n\n".join(chunks)


def extract_pdf_layout_text(path: Path, max_pages: int, max_chars: int) -> str:
    """Extract text with page/block/line structure when PyMuPDF is available."""
    try:
        # PyMuPDF renamed its import module.  Prefer the supported name while
        # retaining compatibility with environments that still expose fitz.
        try:
            import pymupdf as fitz  # type: ignore[import-not-found]
        except ImportError:
            import fitz  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is not installed; layout extraction unavailable.") from exc

    chunks: list[str] = []
    current_chars = 0
    with fitz.open(str(path)) as document:
        for page_index, page in enumerate(document, start=1):
            if page_index > max_pages:
                break
            page_text = _page_layout_text(page)
            if page_text:
                chunk = f"[Page {page_index}]\n{page_text}"
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
    layout_extraction_enabled: bool = True,
    layout_min_quality_score: int = 35,
) -> ExtractedText:
    native_candidate = _best_native_candidate(
        path,
        max_pages=max_pages,
        max_chars=max_chars,
        layout_extraction_enabled=layout_extraction_enabled,
    )
    native_text = native_candidate.text
    native_chars = native_candidate.native_text_chars

    if (
        native_chars >= ocr_min_text_chars
        and native_candidate.quality_score >= layout_min_quality_score
    ):
        return ExtractedText(
            text=native_text,
            source=native_candidate.source,
            native_text_chars=native_chars,
            notes=native_candidate.notes,
        )

    # Always check the OCR cache before deciding whether a new OCR run is allowed.
    # This permits reuse of OCR PDFs created by older pipeline versions even when
    # ocr_enabled is false because endpoint security blocks OCRmyPDF.
    ocr_output = ocr_dir / f"{path.stem}__ocr.pdf"
    cached_result = _read_cached_ocr(
        ocr_output,
        native_candidate=native_candidate,
        max_pages=max_pages,
        max_chars=max_chars,
        layout_extraction_enabled=layout_extraction_enabled,
    )
    if cached_result is not None:
        return cached_result

    if not ocr_enabled:
        return ExtractedText(
            text=native_text,
            source=native_candidate.source,
            native_text_chars=native_chars,
            notes=(
                "OCR novo desativado, nenhum OCR reutilizavel foi encontrado "
                "e o texto nativo/layout do PDF parece insuficiente."
                + _candidate_note_suffix(native_candidate)
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
            source=native_candidate.source,
            native_text_chars=native_chars,
            notes=(
                f"OCR excedeu {ocr_timeout_seconds} segundos; "
                "processamento continuou com o melhor texto nativo/layout."
                + _candidate_note_suffix(native_candidate)
            ),
        )
    except PermissionError as exc:
        _remove_partial_file(ocr_output)
        return ExtractedText(
            text=native_text,
            source=native_candidate.source,
            native_text_chars=native_chars,
            notes=(
                f"OCR bloqueado pela seguranca do Windows ({exc}); "
                "processamento continuou com o melhor texto nativo/layout."
                + _candidate_note_suffix(native_candidate)
            ),
        )
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        _remove_partial_file(ocr_output)
        return ExtractedText(
            text=native_text,
            source=native_candidate.source,
            native_text_chars=native_chars,
            notes=(
                "OCR indisponivel; processamento continuou com o melhor texto "
                f"nativo/layout. Detalhe: {exc}" + _candidate_note_suffix(native_candidate)
            ),
        )

    # Read the newly generated OCR file with the same safeguards used for cache.
    generated_result = _read_cached_ocr(
        ocr_output,
        native_candidate=native_candidate,
        max_pages=max_pages,
        max_chars=max_chars,
        layout_extraction_enabled=layout_extraction_enabled,
        cache_note="OCR executado e texto OCR utilizado.",
    )
    if generated_result is not None:
        return generated_result

    return ExtractedText(
        text=native_text,
        source=native_candidate.source,
        native_text_chars=native_chars,
        notes=(
            "OCR executado, mas o arquivo gerado nao produziu texto reutilizavel."
            + _candidate_note_suffix(native_candidate)
        ),
    )


def _read_cached_ocr(
    ocr_output: Path,
    *,
    native_candidate: TextExtractionCandidate,
    max_pages: int,
    max_chars: int,
    layout_extraction_enabled: bool,
    cache_note: str = "OCR existente reutilizado; OCRmyPDF nao foi executado.",
) -> ExtractedText | None:
    if not ocr_output.is_file():
        return None

    try:
        if ocr_output.stat().st_size <= 0:
            LOGGER.warning("Ignoring empty cached OCR file: %s", ocr_output)
            return None

        ocr_candidate = _best_native_candidate(
            ocr_output,
            max_pages=max_pages,
            max_chars=max_chars,
            layout_extraction_enabled=layout_extraction_enabled,
            source_prefix="cached_ocr_pdf",
        )
    except (OSError, RuntimeError, ValueError) as exc:
        LOGGER.warning("Could not reuse cached OCR %s: %s", ocr_output, exc)
        return None

    ocr_chars = ocr_candidate.ocr_text_chars
    if _candidate_is_better(ocr_candidate, native_candidate):
        LOGGER.info(
            "Using OCR PDF text: %s (%s chars, score=%s)",
            ocr_output.name,
            ocr_chars,
            ocr_candidate.quality_score,
        )
        return ExtractedText(
            text=ocr_candidate.text,
            source=ocr_candidate.source,
            native_text_chars=native_candidate.native_text_chars,
            ocr_text_chars=ocr_chars,
            notes=" ".join(
                part
                for part in (
                    cache_note,
                    ocr_candidate.notes,
                    _candidate_note_suffix(native_candidate).strip(),
                )
                if part
            ),
        )

    LOGGER.warning(
        "OCR PDF did not improve extracted text: %s (native=%s/%s, ocr=%s/%s)",
        ocr_output.name,
        native_candidate.native_text_chars,
        native_candidate.quality_score,
        ocr_chars,
        ocr_candidate.quality_score,
    )
    return ExtractedText(
        text=native_candidate.text,
        source=native_candidate.source,
        native_text_chars=native_candidate.native_text_chars,
        ocr_text_chars=ocr_chars,
        notes=(
            "OCR existente encontrado, mas nao melhorou a qualidade do texto extraido."
            + _candidate_note_suffix(native_candidate)
        ),
    )


def _best_native_candidate(
    path: Path,
    *,
    max_pages: int,
    max_chars: int,
    layout_extraction_enabled: bool,
    source_prefix: str = "native_pdf",
) -> TextExtractionCandidate:
    candidates: list[TextExtractionCandidate] = []

    plain_text = extract_pdf_text(path, max_pages=max_pages, max_chars=max_chars)
    candidates.append(
        _candidate_from_text(
            plain_text,
            source=f"{source_prefix}_text",
            native=source_prefix == "native_pdf",
            notes="",
        )
    )

    layout_text = extract_pdf_text(
        path,
        max_pages=max_pages,
        max_chars=max_chars,
        preserve_layout=True,
    )
    candidates.append(
        _candidate_from_text(
            layout_text,
            source=f"{source_prefix}_pypdf_layout_text",
            native=source_prefix == "native_pdf",
            notes="",
        )
    )

    if layout_extraction_enabled:
        try:
            pymupdf_text = extract_pdf_layout_text(
                path,
                max_pages=max_pages,
                max_chars=max_chars,
            )
            candidates.append(
                _candidate_from_text(
                    pymupdf_text,
                    source=f"{source_prefix}_layout_text",
                    native=source_prefix == "native_pdf",
                    notes="",
                )
            )
        except RuntimeError as exc:
            LOGGER.debug("Layout-aware PyMuPDF extraction unavailable for %s: %s", path, exc)
        except (OSError, ValueError) as exc:
            LOGGER.warning("Layout-aware extraction failed for %s: %s", path, exc)

    return max(candidates, key=_candidate_sort_key)


def _candidate_from_text(
    text: str,
    *,
    source: str,
    native: bool,
    notes: str,
) -> TextExtractionCandidate:
    chars = len(text.strip())
    score = _score_extracted_text(text)
    return TextExtractionCandidate(
        text=text,
        source=source,
        native_text_chars=chars if native else 0,
        ocr_text_chars=0 if native else chars,
        quality_score=score,
        notes=notes if notes and score > 0 else "",
    )


def _candidate_sort_key(candidate: TextExtractionCandidate) -> tuple[int, int, int]:
    line_count = sum(1 for line in candidate.text.splitlines() if line.strip())
    return (candidate.quality_score, line_count, len(candidate.text))


def _candidate_is_better(
    candidate: TextExtractionCandidate,
    baseline: TextExtractionCandidate,
) -> bool:
    if candidate.ocr_text_chars <= 0:
        return False
    if baseline.native_text_chars <= 0:
        return True
    if candidate.quality_score >= baseline.quality_score + 8:
        return True
    if (
        candidate.quality_score >= baseline.quality_score
        and candidate.ocr_text_chars >= int(baseline.native_text_chars * 1.15)
    ):
        return True
    return candidate.ocr_text_chars >= max(200, int(baseline.native_text_chars * 1.5))


def _score_extracted_text(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0

    chars = len(stripped)
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    alnum = sum(character.isalnum() for character in stripped)
    alnum_ratio = alnum / max(chars, 1)
    useful_lines = sum(1 for line in lines if sum(character.isalnum() for character in line) >= 8)
    page_markers = len(re.findall(r"\[Page\s+\d+\]", stripped, flags=re.IGNORECASE))
    labelled_signals = len(
        re.findall(
            r"\b(?:contrato|cl[aá]usula|outorgante|senhorio|arrendat[aá]rio|"
            r"pr[eé]dio|matriz|artigo|sec[cç][aã]o|renda|assinad[oa]|iban)\b",
            stripped,
            flags=re.IGNORECASE,
        )
    )
    noisy_lines = sum(1 for line in lines if len(line) > 20 and _line_alnum_ratio(line) < 0.35)

    score = 0
    score += min(35, chars // 80)
    score += min(25, useful_lines * 2)
    score += min(12, page_markers * 3)
    score += min(18, labelled_signals * 2)
    if alnum_ratio >= 0.55:
        score += 10
    elif alnum_ratio < 0.35:
        score -= 12
    score -= min(20, noisy_lines * 4)
    return max(0, min(100, score))


def _normalize_page_text(text: str, *, preserve_layout: bool) -> str:
    if not preserve_layout:
        return " ".join(text.split())

    lines = []
    for raw_line in text.replace("\x00", " ").splitlines():
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _page_layout_text(page: object) -> str:
    data = page.get_text("dict", sort=True)
    lines: list[tuple[float, float, str]] = []
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = [
                str(span.get("text", "")).strip()
                for span in sorted(line.get("spans", []), key=lambda item: item.get("bbox", [0])[0])
                if str(span.get("text", "")).strip()
            ]
            text = " ".join(spans)
            if not text:
                continue
            bbox = line.get("bbox", [0, 0, 0, 0])
            lines.append((float(bbox[1]), float(bbox[0]), text))

    if not lines:
        return ""

    output: list[str] = []
    previous_y: float | None = None
    for y, _x, text in sorted(lines, key=lambda item: (round(item[0], 1), item[1])):
        if previous_y is not None and y - previous_y > 18 and output and output[-1] != "":
            output.append("")
        output.append(re.sub(r"\s+", " ", text).strip())
        previous_y = y
    return "\n".join(output)


def _line_alnum_ratio(line: str) -> float:
    return sum(character.isalnum() for character in line) / max(len(line), 1)


def _candidate_note_suffix(candidate: TextExtractionCandidate) -> str:
    if not candidate.notes:
        return ""
    return f" {candidate.notes}"


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
