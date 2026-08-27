from __future__ import annotations

import re


KEYWORDS = [
    "contrato",
    "acordo",
    "arrendamento",
    "cpcv",
    "contrato-promessa",
    "promessa de compra e venda",
    "compra e venda",
    "promitente",
    "promitente comprador",
    "promitente vendedor",
    "cedencia",
    "cedência",
    "cessao",
    "cessão",
    "posicao contratual",
    "posição contratual",
    "cedente",
    "cessionario",
    "cessionário",
    "cessionaria",
    "cessionária",
    "cedido",
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
    "data",
    "proprietario",
    "proprietário",
    "propriedade",
    "imovel",
    "imóvel",
    "locador",
    "locatario",
    "locatário",
    "inquilino",
    "beneficiaria",
    "beneficiária",
    "euros",
]

KEYWORD_RE = re.compile("|".join(re.escape(keyword) for keyword in sorted(KEYWORDS, key=len, reverse=True)), re.IGNORECASE)
PAGE_RE = re.compile(r"(?=\[Page \d+\])")
DATE_RE = re.compile(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}\.\d{1,2}\.\d{2,4})\b")
MONTH_RE = re.compile(
    r"\b(?:janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro|"
    r"january|february|march|april|may|june|july|august|september|october|november|december)\b",
    re.IGNORECASE,
)
MONEY_RE = re.compile(
    r"(?:(?:EUR|USD|GBP)\s*)?\b\d{1,3}(?:[ .]\d{3})*(?:,\d{2}|\.\d{2})?\b\s*(?:EUR|USD|GBP|€|\$|£|euros?)",
    re.IGNORECASE,
)


def select_classification_text(text: str, *, page_count: int = 2, fallback_words: int = 500) -> str:
    cleaned = _clean_ocr_noise(text)
    pages = [page.strip() for page in PAGE_RE.split(cleaned) if _has_text(page)] if PAGE_RE.search(cleaned) else []
    if pages:
        return "\n\n---\n\n".join(pages[:page_count])
    return _first_words(cleaned, fallback_words)


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


def select_highlighted_relevant_text(text: str, *, max_chars: int = 12000, context_lines: int = 3) -> str:
    selected = select_relevant_text(text, max_chars=max_chars, context_lines=context_lines)
    return highlight_important_text(selected)


def highlight_important_text(text: str) -> str:
    placeholders: dict[str, str] = {}

    def protect(pattern: re.Pattern[str], label: str, value: str) -> str:
        def replace(match: re.Match[str]) -> str:
            key = f"@@HL{len(placeholders)}@@"
            placeholders[key] = f"[[{label}:{match.group(0)}]]"
            return key

        return pattern.sub(replace, value)

    highlighted = protect(DATE_RE, "DATE", text)
    highlighted = protect(MONEY_RE, "MONEY", highlighted)
    highlighted = protect(MONTH_RE, "MONTH", highlighted)
    highlighted = protect(KEYWORD_RE, "KEYWORD", highlighted)

    for key, value in placeholders.items():
        highlighted = highlighted.replace(key, value)
    return highlighted


def _first_pages(text: str, page_count: int) -> list[str]:
    pages = [page.strip() for page in PAGE_RE.split(text) if _has_text(page)]
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


def _first_words(text: str, max_words: int) -> str:
    words = text.split()
    return " ".join(words[:max_words])


def _has_text(value: str) -> bool:
    return sum(ch.isalnum() for ch in value) >= 20
