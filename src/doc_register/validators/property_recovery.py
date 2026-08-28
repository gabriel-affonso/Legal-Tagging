from __future__ import annotations

from dataclasses import dataclass
import re


PROPERTY_CONTEXT_RE = re.compile(
    r"\b(?:matriz|matricial|pr[eé]dio|predial|inscrito|caderneta|r[uú]stic[ao]|urban[ao])\b",
    re.IGNORECASE,
)
ARTICLE_RE = re.compile(
    r"\b(?:artigo(?:\s+matricial)?|matriz(?:\s+predial)?(?:\s+n[.ºo°]*)?)"
    r"\s*(?:n[.ºo°]*\s*)?[:#-]?\s*(\d{1,8})\b",
    re.IGNORECASE,
)
SECTION_RE = re.compile(r"\bsec(?:c|ç)[aã]o\s*[:#-]?\s*([A-Z]{1,3})\b", re.IGNORECASE)


@dataclass(frozen=True)
class PropertyGroup:
    article: str = ""
    section: str = ""
    evidence: str = ""


def recover_property_group(text: str) -> PropertyGroup:
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        window = " ".join(lines[max(0, index - 1): index + 2])
        if not PROPERTY_CONTEXT_RE.search(window):
            continue
        article = _article_from(window)
        section = _section_from(window)
        if article or section:
            return PropertyGroup(article=article, section=section, evidence=window[:240])
    return PropertyGroup()


def _article_from(value: str) -> str:
    for match in ARTICLE_RE.finditer(value):
        evidence = value[max(0, match.start() - 60): match.end() + 60]
        if "ARTIGO" not in _normalize_ascii(match.group(0)) and _is_matrix_inscription_year(evidence):
            continue
        if PROPERTY_CONTEXT_RE.search(evidence):
            return match.group(1)
    return ""


def _section_from(value: str) -> str:
    match = SECTION_RE.search(value)
    return match.group(1).upper() if match else ""


def _is_matrix_inscription_year(value: str) -> bool:
    normalized = _normalize_ascii(value)
    return "ANO DE INSCRICAO" in normalized and "MATRIZ" in normalized


def _normalize_ascii(value: str) -> str:
    normalized = " ".join(value.upper().split())
    replacements = str.maketrans("ÁÀÂÃÉÊÍÓÔÕÚÇ", "AAAAEEIOOOUC")
    return normalized.translate(replacements)


__all__ = ["PropertyGroup", "recover_property_group"]
