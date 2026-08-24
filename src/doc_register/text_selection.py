from __future__ import annotations

import re


KEYWORDS = [
    "contrato",
    "arrendamento",
    "senhorio",
    "arrendatario",
    "arrendatário",
    "renda",
    "prazo",
    "predio",
    "prédio",
    "artigo",
    "seccao",
    "secção",
    "caderneta",
    "matriz",
    "freguesia",
    "concelho",
    "distrito",
    "artigo matricial",
    "valor patrimonial",
    "certidao",
    "certidão",
    "conservatoria",
    "conservatória",
    "descricao",
    "descrição",
    "inscricao",
    "inscrição",
    "averbamento",
    "IBAN",
    "NIB",
    "SWIFT",
    "BIC",
    "titular",
    "conta",
    "banco",
    "pagamento",
    "transferencia",
    "transferência",
    "montante",
    "valor",
    "beneficiario",
    "beneficiário",
    "ordenante",
]

KEYWORD_RE = re.compile("|".join(re.escape(keyword) for keyword in sorted(KEYWORDS, key=len, reverse=True)), re.IGNORECASE)
PAGE_RE = re.compile(r"(?=\[Page \d+\])")


def select_relevant_text(text: str, *, max_chars: int = 12000, context_lines: int = 3) -> str:
    cleaned = _clean_ocr_noise(text)
    if len(cleaned) <= max_chars:
        return cleaned

    sections: list[str] = []
    sections.extend(_first_pages(cleaned, page_count=2))
    sections.extend(_keyword_windows(cleaned, context_lines=context_lines))

    selected = _dedupe_sections(sections)
    if not selected:
        selected = cleaned[:max_chars]
    return selected[:max_chars]


def _first_pages(text: str, page_count: int) -> list[str]:
    pages = [page.strip() for page in PAGE_RE.split(text) if page.strip()]
    if not pages:
        return [text[:4000]]
    return pages[:page_count]


def _keyword_windows(text: str, *, context_lines: int) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) <= 1:
        return _keyword_page_windows(text)

    windows = []
    for index, line in enumerate(lines):
        if not KEYWORD_RE.search(line):
            continue
        start = max(0, index - context_lines)
        end = min(len(lines), index + context_lines + 1)
        windows.append("\n".join(lines[start:end]))
    return windows


def _keyword_page_windows(text: str) -> list[str]:
    pages = [page.strip() for page in PAGE_RE.split(text) if page.strip()]
    if not pages:
        return []
    return [page for page in pages if KEYWORD_RE.search(page)]


def _clean_ocr_noise(text: str) -> str:
    cleaned_lines = []
    for raw_line in text.replace("\x00", " ").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        letters_or_digits = sum(ch.isalnum() for ch in line)
        if len(line) > 20 and letters_or_digits / len(line) < 0.35:
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def _dedupe_sections(sections: list[str]) -> str:
    seen = set()
    result = []
    for section in sections:
        cleaned = section.strip()
        key = cleaned[:240]
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return "\n\n---\n\n".join(result)

