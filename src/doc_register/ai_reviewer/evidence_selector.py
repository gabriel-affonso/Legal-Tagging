from __future__ import annotations

import hashlib

FIELD_KEYWORDS = {
    "lessor": (
        "senhorio", "senhorios", "locador", "locadores", "outorgante",
        "outorgantes", "entre", "proprietario", "proprietaria",
    ),
    "lessee": (
        "arrendatario", "arrendataria", "arrendatarios", "arrendatarias",
        "locatario", "locataria", "outorgante", "outorgantes", "gesto",
        "q energy", "utu energia",
    ),
    "signed_date": (
        "assinado", "assinatura", "celebrado", "outorgado", "aos ",
        "dias do mes", "data",
    ),
    "property_article": (
        "artigo", "matriz", "matricial", "inscrito", "predio rustico",
        "predio urbano", "caderneta",
    ),
    "property_section": (
        "seccao", "secção", "sec.", "matriz", "artigo", "predio rustico",
    ),
    "monthly_rent": (
        "renda mensal", "mensalmente", "por mes", "por mês", "mensais",
        "renda", "valor mensal",
    ),
}


def select_evidence(text: str, fields: list[str], *, max_chars: int = 6500) -> str:
    clean_text = text.strip()
    if not clean_text or max_chars <= 0:
        return ""
    if len(clean_text) <= max_chars:
        return _label("FULL DOCUMENT TEXT", clean_text)

    front_budget = max(800, int(max_chars * 0.25))
    end_budget = max(1000, int(max_chars * 0.30))
    line_budget = max_chars - front_budget - end_budget

    keywords = _keywords_for(fields)
    relevant_lines = _select_keyword_lines(clean_text, keywords, max_chars=line_budget)
    chunks = [
        ("DOCUMENT BEGINNING", clean_text[:front_budget]),
        ("FIELD-RELEVANT LINES", relevant_lines),
        ("DOCUMENT END", clean_text[-end_budget:]),
    ]
    return _join_distinct(chunks, max_chars=max_chars)


def _keywords_for(fields: list[str]) -> tuple[str, ...]:
    selected: list[str] = []
    for field_name in fields:
        for keyword in FIELD_KEYWORDS.get(field_name, ()):
            lowered = keyword.lower()
            if lowered not in selected:
                selected.append(lowered)
    return tuple(selected)


def _select_keyword_lines(text: str, keywords: tuple[str, ...], *, max_chars: int) -> str:
    if not keywords or max_chars <= 0:
        return ""
    lines = text.splitlines()
    selected: list[str] = []
    used = 0
    for index, line in enumerate(lines):
        lowered = line.lower()
        if not any(keyword in lowered for keyword in keywords):
            continue
        window = "\n".join(lines[max(0, index - 1): index + 2]).strip()
        if not window:
            continue
        if used + len(window) + 2 > max_chars:
            break
        selected.append(window)
        used += len(window) + 2
    return "\n---\n".join(selected)


def _join_distinct(chunks: list[tuple[str, str]], *, max_chars: int) -> str:
    output: list[str] = []
    seen: set[str] = set()
    remaining = max_chars
    for label, chunk in chunks:
        cleaned = chunk.strip()
        if not cleaned:
            continue
        fingerprint = hashlib.sha256(" ".join(cleaned.lower().split()).encode("utf-8")).hexdigest()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        labelled = _label(label, cleaned)
        if len(labelled) > remaining:
            labelled = labelled[:remaining].rstrip()
        if not labelled:
            break
        output.append(labelled)
        remaining -= len(labelled) + 2
        if remaining <= 0:
            break
    return "\n\n".join(output)


def _label(label: str, text: str) -> str:
    return f"[{label}]\n{text.strip()}"
