"""Full page inventory; independent native blocks, OCR and reproducible crops."""
from __future__ import annotations
import base64
import hashlib
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

import pymupdf as fitz
from .schema import Page, Region, identity


def render_crop(page, bbox, *, dpi: int, max_pixels: int) -> tuple[bytes, dict]:
    """Coordinates always refer to the unrotated source page, in PDF points."""
    rect = fitz.Rect(bbox) & page.rect
    if rect.is_empty or rect.width <= 0 or rect.height <= 0:
        raise ValueError('empty_crop')
    scale = min(dpi / 72, math.sqrt(max_pixels / (rect.width * rect.height)))
    # Floor with a margin because pixmap edges round outwards.
    scale *= .995
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect, alpha=False)
    if pix.width * pix.height > max_pixels:
        raise ValueError('pixel_budget_exceeded')
    image = pix.tobytes('png')
    return image, {'sha256': hashlib.sha256(image).hexdigest(), 'bbox': list(rect),
                   'width': pix.width, 'height': pix.height, 'scale': scale,
                   'effective_dpi': scale * 72, 'original_rotation': page.rotation,
                   'render_rotation': 0, 'coordinates': 'unrotated_page_points_top_left'}


def ocr(image: bytes, *, language: str, timeout: int) -> str:
    executable = shutil.which('tesseract')
    if not executable:
        raise RuntimeError('tesseract_unavailable')
    with tempfile.TemporaryDirectory(prefix='step50-ocr-') as temporary:
        source = Path(temporary) / 'crop.png'
        source.write_bytes(image)
        completed = subprocess.run([executable, str(source), 'stdout', '-l', language, '--psm', '3'],
                                   capture_output=True, timeout=timeout, check=False)
        if completed.returncode:
            raise RuntimeError('tesseract_failed: ' + completed.stderr.decode(errors='replace')[:250])
        return completed.stdout.decode('utf-8').strip()


def inventory_page(document, index: int, config, artifact_dir: Path, visual_reader=None, deadline=None) -> Page:
    source = document[index]
    rotation = source.rotation
    source.set_rotation(0)  # Only in memory. Never save the input PDF.
    page = Page(index + 1, source.rect.width, source.rect.height, rotation, 'processed')
    try:
        blocks = source.get_text('blocks', sort=True)
        native = []
        for block in blocks:
            if block[6] != 0 or not block[4].strip():
                continue
            region = Region(identity(index, 'native', block[:4], block[4]), index + 1,
                            list(block[:4]), block[4], 'native')
            native.append(region)
        page.regions.extend(native)
        images = source.get_image_info()
        for region in native:
            if any((fitz.Rect(region.bbox) & fitz.Rect(item['bbox'])).get_area() > 0 for item in images):
                region.issues.append('native_over_image_requires_verification')
        drawings = source.get_drawings()
        # Native-only pages with vector marks can still contain handwritten fills.
        needs_full = len(''.join(r.text for r in native).strip()) < config.native_min_chars
        regions = [list(source.rect)] if needs_full else [list(item['bbox']) for item in images]
        if drawings and not regions:
            # Proposal, not a claim that the drawings are handwriting.
            regions = [list(source.rect)]
        if len(regions) > config.max_regions_per_page:
            page.issues.append('incomplete_coverage:region_budget')
        seen = set()
        for bbox in regions[:config.max_regions_per_page]:
            if deadline is not None and time.monotonic() >= deadline:
                page.issues.append('incomplete_coverage:time_budget')
                break
            key = tuple(round(v, 1) for v in bbox)
            if key in seen:
                continue
            seen.add(key)
            rid = identity(index, 'image', bbox)
            region = Region(rid, index + 1, bbox, '', 'ocr', granularity='crop')
            page.regions.append(region)
            try:
                image, recipe = render_crop(source, bbox, dpi=config.dpi, max_pixels=config.max_pixels)
                recipe['original_rotation'] = rotation
                crop_path = artifact_dir / f'{rid}.png'
                crop_path.parent.mkdir(parents=True, exist_ok=True)
                crop_path.write_bytes(image)
                region.image = {**recipe, 'path': str(crop_path.resolve())}
                if config.ocr_enabled:
                    try:
                        timeout = min(config.ocr_timeout, max(.1, deadline-time.monotonic())) if deadline else config.ocr_timeout
                        region.text = ocr(image, language=config.ocr_language, timeout=timeout)
                    except Exception as exc:
                        region.issues.append('ocr_failure:' + str(exc)[:200])
                else:
                    region.issues.append('ocr_disabled')
                # The literal VLM pass gets the crop only, never OCR guesses.
                if visual_reader is not None:
                    try:
                        reading = visual_reader(image)
                        visual = Region(identity(rid, 'visual'), index + 1, bbox, reading['text'], 'visual',
                                        granularity='crop', image=region.image,
                                        issues=['uncalibrated_visual'] + (['uncertain_tokens'] if reading['uncertain_tokens'] else []))
                        if reading['crossed_out']:
                            visual.issues.append('crossed_out')
                        visual.image = {**visual.image, 'modality': reading['modality'], 'uncertain_tokens': reading['uncertain_tokens']}
                        page.regions.append(visual)
                    except Exception as exc:
                        region.issues.append('visual_failure:' + str(exc)[:200])
                else:
                    region.issues.append('visual_not_examined')
                if not region.text:
                    region.issues.append('untranscribed_region')
            except Exception as exc:
                region.issues.append('recognition_failure:' + str(exc)[:200])
        if not page.regions:
            page.status = 'blank_or_unreadable'
            page.issues.append('blank_requires_visual_confirmation')
        if rotation:
            page.issues.append('rotation_metadata_normalized')
        if '\ufffd' in ''.join(r.text for r in native):
            page.issues.append('native_encoding_suspect')
    except Exception as exc:
        page.status = 'technical_error'
        page.issues.append(str(exc)[:300])
    finally:
        source.set_rotation(rotation)
    return page
