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


def apply_ocr_quality(result: ExtractionResult, *, document_text: str) -> ExtractionResult:
    report = analyze_ocr_quality(document_text)
    result.ocr_quality_score = str(report.score) if report.text_chars else ""
    result.ocr_quality_flags = "; ".join(report.flags)
    raw = result.raw_json if isinstance(result.raw_json, dict) else {}
    result.raw_json = dict(raw)
    result.raw_json["ocr_quality"] = report.to_dict()
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


__all__ = ["OcrQualityReport", "analyze_ocr_quality", "apply_ocr_quality"]
