from __future__ import annotations

from dataclasses import dataclass
import re

from .name_quality import validate_name_field


ZONE_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("lessor", "primeiro_outorgante", re.compile(r"\bprimeir[oa]s?\s+outorgantes?\b", re.IGNORECASE)),
    ("lessee", "segundo_outorgante", re.compile(r"\bsegund[oa]s?\s+outorgantes?\b", re.IGNORECASE)),
    ("lessor", "senhorio", re.compile(r"\bsenhorios?\b", re.IGNORECASE)),
    ("lessee", "arrendatario", re.compile(r"\barrendat[aá]ri[oa]s?\b", re.IGNORECASE)),
    ("lessor", "de_um_lado", re.compile(r"\bde\s+um\s+lado\b", re.IGNORECASE)),
    ("lessee", "do_outro_lado", re.compile(r"\bdo\s+outro\s+lado\b", re.IGNORECASE)),
    ("lessor", "entre", re.compile(r"\bentre\b", re.IGNORECASE)),
)

PARTY_SPLIT_RE = re.compile(
    r"\b(?:NIF|NIPC|contribuinte|portador|portadora|residente|com\s+sede|"
    r"morada|natural\s+de|casad[oa]|solteir[oa]|doravante)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ContractZone:
    role: str
    label: str
    text: str
    start_line: int


def extract_contract_zones(text: str, *, context_lines: int = 3) -> list[ContractZone]:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    zones: list[ContractZone] = []
    for index, line in enumerate(lines):
        if not line:
            continue
        for role, label, pattern in ZONE_PATTERNS:
            if not pattern.search(line):
                continue
            start = max(0, index)
            end = min(len(lines), index + context_lines + 1)
            zones.append(
                ContractZone(
                    role=role,
                    label=label,
                    text="\n".join(line for line in lines[start:end] if line),
                    start_line=index + 1,
                )
            )
    return _dedupe_zones(zones)


def party_candidates_from_zones(text: str, role: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for zone in extract_contract_zones(text):
        if zone.role != role:
            continue
        for candidate in _candidates_from_zone(zone.text, role=role, label=zone.label):
            if not validate_name_field(role, candidate):
                candidates.append((candidate, zone.text))
    return _dedupe_candidates(candidates)


def zone_text_for_role(text: str, role: str) -> str:
    return "\n".join(zone.text for zone in extract_contract_zones(text) if zone.role == role)


def _candidates_from_zone(zone_text: str, *, role: str, label: str) -> list[str]:
    text = " ".join(zone_text.split())
    candidates: list[str] = []

    if role == "lessor":
        match = re.search(r"(.{5,220}?)\s*,?\s+na\s+qualidade\s+de\s+senhorios?", text, flags=re.IGNORECASE)
        if match:
            candidates.extend(_split_party_names(match.group(1)))
            return candidates
    if role == "lessee":
        match = re.search(r"\s+e\s+(.{5,180}?)\s*,?\s+na\s+qualidade\s+de\s+arrendat[aá]ri[oa]", text, flags=re.IGNORECASE)
        if match:
            candidates.extend(_split_party_names(match.group(1)))
            return candidates
        match = re.search(r"(.{5,220}?)\s*,?\s+na\s+qualidade\s+de\s+arrendat[aá]ri[oa]", text, flags=re.IGNORECASE)
        if match:
            candidates.extend(_split_party_names(match.group(1)))
            return candidates

    if label == "entre":
        match = re.search(
            r"\bentre\b\s+(.{5,220}?)(?:,\s*na\s+qualidade\s+de\s+senhorios?|\s*,\s*e\s+|\s+e\s+[A-Z][A-Za-z .,&]+,\s+na\s+qualidade\s+de\s+arrendat)",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            candidates.extend(_split_party_names(match.group(1)))
        return candidates

    label_match = re.search(
        r"(?:primeir[oa]s?\s+outorgantes?|segund[oa]s?\s+outorgantes?|"
        r"senhorios?|arrendat[aá]ri[oa]s?|de\s+um\s+lado|do\s+outro\s+lado)"
        r"\s*[:,-]?\s*(.{5,220})",
        text,
        flags=re.IGNORECASE,
    )
    if label_match:
        candidates.extend(_split_party_names(label_match.group(1)))

    return candidates


def _split_party_names(value: str) -> list[str]:
    value = re.sub(
        r"^\s*(?:entre|de\s+um\s+lado|do\s+outro\s+lado)\b\s*",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = PARTY_SPLIT_RE.split(value, maxsplit=1)[0]
    value = re.sub(r"\s+", " ", value).strip(" ,;:-")
    if not value:
        return []
    parts = re.split(r"\s+(?:e|&)\s+|[;|]+", value, flags=re.IGNORECASE)
    output: list[str] = []
    for part in parts:
        cleaned = re.sub(r"\s+", " ", part).strip(" ,;:-")
        if cleaned and cleaned not in output:
            output.append(cleaned)
    return output


def _dedupe_zones(zones: list[ContractZone]) -> list[ContractZone]:
    output: list[ContractZone] = []
    seen: set[tuple[str, str, str]] = set()
    for zone in zones:
        key = (zone.role, zone.label, zone.text)
        if key not in seen:
            seen.add(key)
            output.append(zone)
    return output


def _dedupe_candidates(candidates: list[tuple[str, str]]) -> list[tuple[str, str]]:
    output: list[tuple[str, str]] = []
    seen: set[str] = set()
    for candidate, evidence in candidates:
        if candidate not in seen:
            seen.add(candidate)
            output.append((candidate, evidence))
    return output


__all__ = [
    "ContractZone",
    "extract_contract_zones",
    "party_candidates_from_zones",
    "zone_text_for_role",
]
