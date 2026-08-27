from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
import unicodedata


MONTHS = {
    "JANEIRO": 1, "FEVEREIRO": 2, "MARCO": 3, "MARÇO": 3, "ABRIL": 4,
    "MAIO": 5, "JUNHO": 6, "JULHO": 7, "AGOSTO": 8, "SETEMBRO": 9,
    "OUTUBRO": 10, "NOVEMBRO": 11, "DEZEMBRO": 12,
}

SIGNATURE_PATTERNS = (
    re.compile(
        r"\b(?:assinado|assinad[ao]s|celebrado|outorgado|feito)\s+(?:em|aos?|no\s+dia)?\s*.{0,40}?"
        r"(\d{1,2}[/-]\d{1,2}[/-](?:19|20)\d{2})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\baos?\s+(\d{1,2})\s+dias?\s+do\s+m[eê]s\s+de\s+([A-Za-zçÇ]+)\s+de\s+((?:19|20)\d{2})",
        re.IGNORECASE,
    ),
)
DATE_RE = re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-](?:19|20)\d{2}|(?:19|20)\d{2}-\d{2}-\d{2})\b")
BAD_DATE_CONTEXT_RE = re.compile(r"\b(?:validade|registo|registro|licen[cç]a|emiss[aã]o|certid[aã]o|matriz|caderneta)\b", re.IGNORECASE)


@dataclass(frozen=True)
class SignatureDateCandidate:
    value: str
    evidence: str
    method: str
    confidence: str


def recover_signature_date(text: str) -> SignatureDateCandidate | None:
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    candidates: list[SignatureDateCandidate] = []

    for index, line in enumerate(lines):
        window = " ".join(lines[max(0, index - 1): index + 2])
        for pattern in SIGNATURE_PATTERNS:
            match = pattern.search(window)
            if not match:
                continue
            value = _value_from_match(match)
            if value:
                candidates.append(
                    SignatureDateCandidate(value, window[:240], "signature_date_context", "high")
                )

    if candidates:
        return candidates[-1]

    tail = "\n".join(lines[-12:])
    if re.search(r"\b(?:assinado|assinatura|celebrado|feito|outorgado)\b", tail, flags=re.IGNORECASE):
        for match in DATE_RE.finditer(tail):
            evidence = tail[max(0, match.start() - 80): match.end() + 80]
            if BAD_DATE_CONTEXT_RE.search(evidence):
                continue
            value = normalize_date(match.group(1))
            if value:
                return SignatureDateCandidate(value, evidence[:240], "document_end_date_context", "medium")
    return None


def normalize_date(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-]((?:19|20)\d{2})", value)
    if match:
        day, month, year = map(int, match.groups())
        try:
            datetime(year, month, day)
        except ValueError:
            return ""
        return f"{year:04d}-{month:02d}-{day:02d}"
    return ""


def _value_from_match(match: re.Match[str]) -> str:
    if len(match.groups()) == 1:
        return normalize_date(match.group(1))
    day = int(match.group(1))
    month = MONTHS.get(_normalize(match.group(2)))
    year = int(match.group(3))
    if not month:
        return ""
    try:
        datetime(year, month, day)
    except ValueError:
        return ""
    return f"{year:04d}-{month:02d}-{day:02d}"


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value))
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(plain.upper().split())


__all__ = ["SignatureDateCandidate", "normalize_date", "recover_signature_date"]
