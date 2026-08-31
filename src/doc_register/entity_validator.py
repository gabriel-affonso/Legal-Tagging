"""Strict party/entity validation used before a value can be published."""
from __future__ import annotations

import re
import unicodedata


BLOCKED_ENTITY_LABELS = frozenset({"SENHORIO", "ARRENDATARIA", "ARRENDATARIO", "PARTE", "PROPRIETARIO", "LOCADOR", "LOCATARIO", "TITULAR"})
KNOWN_OCR_GARBAGE = ("AC OS JE", "A GYAX AIS", "PEN CL RENAS")


def validate_entity(value: str) -> tuple[bool, str]:
    raw = str(value or "").strip()
    folded = _fold(raw)
    compact = re.sub(r"\s+", " ", folded).strip()
    if not raw:
        return False, "empty_entity"
    if compact in BLOCKED_ENTITY_LABELS:
        return False, "role_label"
    if any(bad in compact for bad in KNOWN_OCR_GARBAGE):
        return False, "known_ocr_garbage"
    tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]{2,}", raw)
    letters = sum(char.isalpha() for char in raw)
    meaningful = sum(char.isalpha() for char in raw if not char.isspace())
    non_space = len([char for char in raw if not char.isspace()])
    if len(tokens) < 2:
        return False, "fewer_than_two_tokens"
    if letters < 8:
        return False, "fewer_than_eight_letters"
    if not non_space or meaningful / non_space < 0.70:
        return False, "low_alphabetic_ratio"
    return True, ""


def _fold(value: str) -> str:
    plain = "".join(char for char in unicodedata.normalize("NFKD", value) if not unicodedata.combining(char))
    return re.sub(r"[^A-Za-z0-9 ]", " ", plain).upper()


__all__ = ["validate_entity", "BLOCKED_ENTITY_LABELS"]
