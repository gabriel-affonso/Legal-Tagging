from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any

from ..models import ExtractionResult


@dataclass(frozen=True)
class OcrQualityReport:
    score: int
    flags: list[str]
    text_chars: int
    alnum_ratio: float
    symbol_ratio: float
    short_token_ratio: float

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["alnum_ratio"] = round(self.alnum_ratio, 3)
        payload["symbol_ratio"] = round(self.symbol_ratio, 3)
        payload["short_token_ratio"] = round(self.short_token_ratio, 3)
        return payload


@dataclass(frozen=True)
class PageQualityReport:
    page: int | None
    page_surface_quality: float
    page_language_quality: float
    page_layout_quality: float
    entity_recoverability: float
    numeric_recoverability: float
    overall_page_quality: float
    flags: list[str]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in (
            "page_surface_quality", "page_language_quality", "page_layout_quality",
            "entity_recoverability", "numeric_recoverability", "overall_page_quality",
        ):
            payload[key] = round(float(payload[key]), 3)
        return payload


def apply_ocr_quality(result: ExtractionResult, *, document_text: str) -> ExtractionResult:
    report = analyze_ocr_quality(document_text)
    result.ocr_quality_score = str(report.score) if report.text_chars else ""
    result.ocr_quality_flags = "; ".join(report.flags)
    raw = result.raw_json if isinstance(result.raw_json, dict) else {}
    result.raw_json = dict(raw)
    result.raw_json["ocr_quality"] = report.to_dict()
    result.raw_json["page_quality"] = [item.to_dict() for item in analyze_page_quality(document_text)]
    return result


def analyze_ocr_quality(text: str) -> OcrQualityReport:
    raw = str(text or "")
    chars = [char for char in raw if not char.isspace()]
    if not chars:
        return OcrQualityReport(0, ["no_text"], 0, 0.0, 0.0, 0.0)

    alnum_ratio = sum(char.isalnum() for char in chars) / len(chars)
    symbol_ratio = sum(not char.isalnum() for char in chars) / len(chars)
    tokens = re.findall(r"\S+", raw)
    alpha_tokens = [token for token in tokens if re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", token)]
    short_tokens = [
        token for token in alpha_tokens
        if len(re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ]", "", token)) == 1
    ]
    short_token_ratio = len(short_tokens) / len(alpha_tokens) if alpha_tokens else 0.0

    flags: list[str] = []
    score = 100
    if len(raw.strip()) < 200:
        flags.append("low_text_volume")
        score -= 35
    if alnum_ratio < 0.65:
        flags.append("low_alnum_ratio")
        score -= 30
    if symbol_ratio > 0.25:
        flags.append("high_symbol_noise")
        score -= 20
    if short_token_ratio > 0.25 and len(alpha_tokens) >= 12:
        flags.append("fragmented_words")
        score -= 25
    if "\ufffd" in raw or "�" in raw:
        flags.append("replacement_characters")
        score -= 20

    score = max(0, min(100, score))
    if score < 60 and "ocr_quality_low" not in flags:
        flags.append("ocr_quality_low")
    return OcrQualityReport(
        score=score,
        flags=flags,
        text_chars=len(raw.strip()),
        alnum_ratio=alnum_ratio,
        symbol_ratio=symbol_ratio,
        short_token_ratio=short_token_ratio,
    )


def analyze_page_quality(text: str) -> list[PageQualityReport]:
    """Assess recoverability per physical page without treating clean noise as quality.

    Scores are intentionally independent: a page can be suitable for prose but
    unsuitable for numeric identifiers or parties.
    """
    pages = _pages(text)
    reports: list[PageQualityReport] = []
    for page, raw in pages:
        chars = [char for char in raw if not char.isspace()]
        tokens = re.findall(r"\S+", raw)
        alpha_tokens = [token for token in tokens if re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", token)]
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        alnum = sum(char.isalnum() for char in chars) / len(chars) if chars else 0.0
        symbols = sum(not char.isalnum() for char in chars) / len(chars) if chars else 1.0
        vowel_less = sum(
            bool(re.fullmatch(r"[A-Za-z]{4,}", re.sub(r"[^A-Za-z]", "", token)))
            and not re.search(r"[aeiouáéíóúàâãêôõü]", token, re.I)
            for token in alpha_tokens
        )
        fragmented = sum(len(re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ]", "", token)) == 1 for token in alpha_tokens)
        gibberish_ratio = (vowel_less + fragmented) / len(alpha_tokens) if alpha_tokens else 1.0
        short_lines = sum(len(line) < 12 for line in lines) / len(lines) if lines else 1.0
        headings = bool(re.search(r"\b(?:CONTRATO|CADERNETA|ARTIGO|SEC[CÇ][AÃ]O|NIF|NIPC|ASSINATURA|CL[AÁ]USULA)\b", raw, re.I))
        valid_ids = len(re.findall(r"\b\d{9}\b|\b(?:19|20)\d{2}[/-]\d{2}[/-]\d{2}\b|\b\d+(?:[,.]\d+)?\s*(?:ha|hectares|m2|m²)\b", raw, re.I))
        name_shapes = len(re.findall(r"\b[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]+(?:\s+[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]+)+\b", raw))

        surface = _clamp(0.55 * alnum + 0.25 * (1 - symbols) + 0.20 * min(1.0, len(chars) / 600))
        language = _clamp(0.78 - 0.85 * gibberish_ratio + (0.12 if headings else 0.0))
        layout = _clamp(0.72 - 0.45 * short_lines + (0.12 if len(lines) >= 3 else 0.0))
        entity = _clamp(0.30 + 0.10 * min(4, name_shapes) + 0.25 * language + 0.15 * layout)
        numeric = _clamp(0.25 + 0.14 * min(4, valid_ids) + 0.22 * surface + 0.10 * language)
        overall = _clamp(0.28 * surface + 0.27 * language + 0.20 * layout + 0.15 * entity + 0.10 * numeric)
        flags: list[str] = []
        if gibberish_ratio > 0.28:
            flags.append("semantic_ocr_degradation")
        if numeric < 0.45:
            flags.append("numeric_recoverability_low")
        if entity < 0.45:
            flags.append("entity_recoverability_low")
        reports.append(PageQualityReport(page, surface, language, layout, entity, numeric, overall, flags))
    return reports


def _pages(text: str) -> list[tuple[int | None, str]]:
    matches = list(re.finditer(r"\[Page\s+(\d+)\]", str(text or ""), re.I))
    if not matches:
        return [(None, str(text or ""))]
    return [
        (int(match.group(1)), str(text or "")[match.end(): matches[index + 1].start() if index + 1 < len(matches) else len(str(text or ""))])
        for index, match in enumerate(matches)
    ]


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


__all__ = ["OcrQualityReport", "PageQualityReport", "analyze_ocr_quality", "analyze_page_quality", "apply_ocr_quality"]
