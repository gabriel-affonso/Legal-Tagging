"""Focused extraction of property identification data from lease contracts.

This module intentionally has no dependency on the primary document-register
workflow.  It can share its OCR output, but its decisions and persisted output
remain separate from the generic metadata extraction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata
from typing import Any, Callable, Mapping


LEASE_THRESHOLD = 60
PROPERTY_SCHEMA = (
    "property_name",
    "matrix_article",
    "matrix_section",
    "area_m2",
)


@dataclass(frozen=True)
class LeaseDetection:
    score: int
    matched_indicators: tuple[str, ...]

    @property
    def is_lease_contract(self) -> bool:
        return self.score >= LEASE_THRESHOLD


@dataclass(frozen=True)
class PropertyExtraction:
    status: str
    reason: str = ""
    document_type: str = ""
    property_name: str = ""
    matrix_article: str = ""
    matrix_section: str = ""
    area_m2: int | float | None = None
    confidence: int = 0
    source_section: str = ""
    source_clause: str = ""
    lease_score: int = 0
    candidate_score: int = 0
    used_llm: bool = False
    llm_error: str = ""
    evidence: str = ""
    evidence_model: Mapping[str, Any] = field(default_factory=dict)
    audit: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        output: dict[str, Any] = {
            "status": self.status,
            "document_type": self.document_type,
            "property_name": self.property_name or None,
            "matrix_article": self.matrix_article or None,
            "matrix_section": self.matrix_section or None,
            "area_m2": self.area_m2,
            "confidence": self.confidence,
            "source": {
                "section": self.source_section or None,
                "clause": self.source_clause or None,
            },
            "lease_score": self.lease_score,
            "candidate_score": self.candidate_score,
            "used_llm": self.used_llm,
            "evidence_model": dict(self.evidence_model),
        }
        if self.reason:
            output["reason"] = self.reason
        if self.llm_error:
            output["llm_error"] = self.llm_error
        if self.audit:
            output["audit"] = dict(self.audit)
        return output


LLMExtractor = Callable[[str], Mapping[str, Any]]


class PropertyExtractionPipeline:
    """Regex-first property extraction with an optional focused LLM fallback."""

    def __init__(self, llm_extractor: LLMExtractor | None = None):
        self._llm_extractor = llm_extractor

    def extract(self, document_text: str, *, allow_llm: bool = True) -> PropertyExtraction:
        text = _clean_text(document_text)
        detection = detect_lease_contract(text)
        if not detection.is_lease_contract:
            return PropertyExtraction(
                status="skipped",
                reason="not_lease_contract",
                lease_score=detection.score,
                audit={"lease_indicators": list(detection.matched_indicators)},
            )

        context, has_considering = select_considering_context(text)
        clause, has_clause_a = select_clause_a(context)
        candidates = _candidate_blocks(clause or context)
        candidate, candidate_score = max(
            ((_clean_text(item), score_property_block(item)) for item in candidates),
            key=lambda item: item[1],
            default=("", 0),
        )
        values = _extract_regex_values(candidate)
        values = _validate_values(values)
        confidence = _confidence(values)
        used_llm = False
        llm_error = ""

        if allow_llm and (confidence < 90 or _has_missing_values(values)) and self._llm_extractor:
            used_llm = True
            try:
                recovered = _normalise_llm_values(self._llm_extractor(candidate))
                values = _merge_with_verified_llm_values(values, recovered, candidate)
                values = _validate_values(values)
                confidence = _confidence(values)
            except Exception as exc:  # The deterministic result remains useful.
                llm_error = str(exc)[:500]

        return PropertyExtraction(
            status="processed",
            document_type="lease_contract",
            property_name=values["property_name"],
            matrix_article=values["matrix_article"],
            matrix_section=values["matrix_section"],
            area_m2=values["area_m2"],
            confidence=confidence,
            source_section="Considerando que" if has_considering else "",
            source_clause="a)" if has_clause_a else "",
            lease_score=detection.score,
            candidate_score=candidate_score,
            used_llm=used_llm,
            llm_error=llm_error,
            evidence=candidate[:1000],
            evidence_model=_contract_evidence(values),
            audit={
                "lease_indicators": list(detection.matched_indicators),
                "regex_values": _serialise_values(_extract_regex_values(candidate)),
                "context_reduced": has_considering,
                "clause_a_found": has_clause_a,
            },
        )


def detect_lease_contract(text: str) -> LeaseDetection:
    normalized = _fold(text)
    indicators = (
        ("arrendamento", 40, r"\b(?:contrato(?:\s+promessa)?\s+de\s+)?arrendamento\b"),
        ("senhorio", 20, r"\bsenhorio(?:s)?\b"),
        ("arrendatario", 20, r"\barrendatari[oa](?:s)?\b"),
        ("renda", 20, r"\brenda(?:\s+anual|\s+mensal)?\b"),
        ("prazo_do_arrendamento", 20, r"\bprazo\s+do\s+arrendamento\b"),
    )
    matched = [(name, weight) for name, weight, pattern in indicators if re.search(pattern, normalized)]
    return LeaseDetection(
        score=sum(weight for _, weight in matched),
        matched_indicators=tuple(name for name, _ in matched),
    )


def select_considering_context(text: str) -> tuple[str, bool]:
    match = re.search(r"\bconsiderando\s+que\s*:?", _fold(text), re.IGNORECASE)
    if not match:
        return text, False
    start = max(0, match.start() - 3000)
    end = min(len(text), match.end() + 5000)
    return text[start:end], True


def select_clause_a(text: str) -> tuple[str, bool]:
    start = re.search(r"(?<!\w)a[.)](?=\s)", text, re.IGNORECASE)
    if not start:
        return text, False
    end = re.search(r"(?<!\w)b[.)](?=\s)", text[start.end():], re.IGNORECASE)
    stop = start.end() + end.start() if end else len(text)
    return text[start.start():stop].strip(), True


def score_property_block(text: str) -> int:
    normalized = _fold(text)
    terms = (
        r"\bdenominad[oa]\b",
        r"\bartigo\b",
        r"\bmatriz\b",
        r"\bseccao\b",
        r"\barea\b",
        r"\bpredio\b",
    )
    return min(100, 20 * sum(bool(re.search(term, normalized)) for term in terms))


def _candidate_blocks(text: str) -> list[str]:
    if not text.strip():
        return [""]
    blocks = [item.strip() for item in re.split(r"\n{2,}|(?=(?<!\w)[a-z][.)]\s)", text) if item.strip()]
    # OCR often collapses all text into one line. Keep the full target clause in
    # that case; splitting it by sentences can separate article and section.
    return blocks or [text]


def _extract_regex_values(text: str) -> dict[str, Any]:
    return {
        "property_name": _first_property_name(text),
        "matrix_article": _first_match(
            r"\bartigo(?:\s+matricial)?\s*(?:n[.ºo°]*\s*)?[:#-]?\s*"
            r"([A-Z0-9]+(?:\s*[-/]\s*[A-Z0-9]+)*)\b",
            text,
        ),
        "matrix_section": _first_match(
            r"\bsec[cç][aã]o(?:\s+matricial)?\s*(?:n[.ºo°]*\s*)?[:#-]?\s*([A-Z]{1,3})\b",
            text,
        ),
        "area_m2": _first_area(text),
    }


def _first_property_name(text: str) -> str:
    patterns = (
        r"\bdenominad[oa]\s+por\s+(.+?)\s+compost[oa]\b",
        r"\bdenominad[oa]\s+por\s+(.+?)[,.;]",
        r"\bpr[eé]dio\s+r[uú]stico\s+(.+?)\s+compost[oa]\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            return _clean_property_name(match.group(1))
    return ""


def _first_area(text: str) -> int | float | None:
    match = re.search(
        r"\b[áa]rea(?:\s+total)?\s*(?:de|:)?\s*([\d\s.,]+)\s*(?:m[²2])\b",
        text,
        re.IGNORECASE,
    )
    return _normalise_area(match.group(1)) if match else None


def _first_match(pattern: str, text: str) -> str:
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1).strip().upper() if match else ""


def _normalise_llm_values(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "property_name": _clean_property_name(str(value.get("property_name") or "")),
        "matrix_article": str(value.get("matrix_article") or "").strip().upper(),
        "matrix_section": str(value.get("matrix_section") or "").strip().upper(),
        "area_m2": _normalise_area(value.get("area_m2")),
    }


def _merge_with_verified_llm_values(
    values: dict[str, Any], recovered: dict[str, Any], evidence: str
) -> dict[str, Any]:
    merged = dict(values)
    for field in PROPERTY_SCHEMA:
        if not merged[field] and recovered[field] not in ("", None):
            candidate = recovered[field]
            if _value_has_evidence(field, candidate, evidence):
                merged[field] = candidate
    return merged


def _value_has_evidence(field: str, value: Any, text: str) -> bool:
    if field == "property_name":
        return _fold(str(value)) in _fold(text)
    if field == "matrix_article":
        return bool(re.search(r"\bartigo(?:\s+matricial)?\D{0,20}" + re.escape(str(value)) + r"\b", text, re.IGNORECASE))
    if field == "matrix_section":
        return bool(re.search(r"\bsec[cç][aã]o\D{0,20}" + re.escape(str(value)) + r"\b", text, re.IGNORECASE))
    if field == "area_m2":
        return any(_normalise_area(match.group(1)) == value for match in re.finditer(
            r"\b[áa]rea(?:\s+total)?\s*(?:de|:)?\s*([\d\s.,]+)\s*(?:m[²2])\b", text, re.IGNORECASE
        ))
    return False


def _validate_values(values: dict[str, Any]) -> dict[str, Any]:
    validated = dict(values)
    name = _clean_property_name(str(validated["property_name"] or ""))
    validated["property_name"] = name if len(name) > 2 else ""
    article = re.sub(r"\s*([- /])\s*", r"\1", str(validated["matrix_article"] or "").upper())
    validated["matrix_article"] = article if re.fullmatch(r"[A-Z0-9]+(?:[-/][A-Z0-9]+)*", article) else ""
    section = str(validated["matrix_section"] or "").upper()
    validated["matrix_section"] = section if re.fullmatch(r"[A-Z]{1,3}", section) else ""
    area = _normalise_area(validated["area_m2"])
    validated["area_m2"] = area if area is not None and area > 0 else None
    return validated


def _confidence(values: Mapping[str, Any]) -> int:
    return 25 * sum(values[field] not in ("", None) for field in PROPERTY_SCHEMA)


def _has_missing_values(values: Mapping[str, Any]) -> bool:
    return any(values[field] in ("", None) for field in PROPERTY_SCHEMA)


def _clean_text(value: str) -> str:
    return value.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _fold(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFD", value).lower()
        if unicodedata.category(char) != "Mn"
    )


def _clean_property_name(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" \t\n\"'«»“”.,;:-")
    return value[:200]


def _normalise_area(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value if value > 0 else None
    raw = re.sub(r"[\s\u00a0]", "", str(value))
    if not raw or not re.fullmatch(r"\d+(?:[.,]\d+)*", raw):
        return None
    if "," in raw and "." in raw:
        decimal = "," if raw.rfind(",") > raw.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        normalized = raw.replace(thousands, "").replace(decimal, ".")
    elif "," in raw:
        left, right = raw.rsplit(",", 1)
        normalized = left + right if len(right) == 3 else left + "." + right
    elif "." in raw:
        left, right = raw.rsplit(".", 1)
        normalized = left + right if len(right) == 3 else left + "." + right
    else:
        normalized = raw
    try:
        number = float(normalized)
    except ValueError:
        return None
    if number <= 0:
        return None
    return int(number) if number.is_integer() else number


def _serialise_values(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: values.get(key) for key in PROPERTY_SCHEMA}


def _contract_evidence(values: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        field: {
            "value": values[field] if values[field] not in ("", None) else None,
            "source": "contract_clause" if values[field] not in ("", None) else None,
            "confidence": 85 if values[field] not in ("", None) else 0,
        }
        for field in PROPERTY_SCHEMA
    }


__all__ = [
    "LEASE_THRESHOLD",
    "PropertyExtraction",
    "PropertyExtractionPipeline",
    "detect_lease_contract",
    "score_property_block",
    "select_clause_a",
    "select_considering_context",
]
