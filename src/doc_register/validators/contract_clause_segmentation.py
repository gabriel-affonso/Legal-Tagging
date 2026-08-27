from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata


CLAUSE_TYPES = {
    "title",
    "parties",
    "property",
    "term",
    "rent",
    "payment",
    "deposit",
    "expenses",
    "obligations",
    "communications",
    "signatures",
    "annexes",
    "other",
}

PAGE_MARKER_RE = re.compile(r"\[Page\s+(\d+)\]", re.IGNORECASE)
FORMAL_HEADING_RE = re.compile(
    r"^(?:cl[aá]usula\s+)?"
    r"(?:primeir[ao]|segund[ao]|terceir[ao]|quart[ao]|quint[ao]|sext[ao]|"
    r"s[eé]tim[ao]|oitav[ao]|non[ao]|d[eé]cim[ao]|\d{1,2})"
    r"\s*(?:[.ªº)]|[-:])",
    re.IGNORECASE,
)
CLAUSE_BREAK_RE = re.compile(
    r"\s+((?:cl[aá]usula\s+)?"
    r"(?:primeir[ao]|segund[ao]|terceir[ao]|quart[ao]|quint[ao]|sext[ao]|"
    r"s[eé]tim[ao]|oitav[ao]|non[ao]|d[eé]cim[ao]|\d{1,2})"
    r"\s*(?:[.ªº)]|[-:]))",
    re.IGNORECASE,
)
SEMANTIC_BREAKS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\s+(?=Primeir[oa]\s+Outorgante\b)", re.IGNORECASE),
    re.compile(r"\s+(?=Segund[oa]\s+Outorgante\b)", re.IGNORECASE),
    re.compile(r"\s+(?=Senhorios?\b)", re.IGNORECASE),
    re.compile(r"\s+(?=Arrendat[aá]ri[oa]s?\b)", re.IGNORECASE),
    re.compile(r"\s+(?=O\s+(?:pr[eé]dio|im[oó]vel|locado|arrendado)\b)", re.IGNORECASE),
    re.compile(r"\s+(?=A\s+renda\b|O\s+valor\s+da\s+renda\b)", re.IGNORECASE),
    re.compile(r"\s+(?=O\s+prazo\b|A\s+dura[cç][aã]o\b)", re.IGNORECASE),
    re.compile(r"\s+(?=Assinad[oa]\b|Feito\b|Celebrado\b)", re.IGNORECASE),
)


@dataclass(frozen=True)
class ContractClause:
    clause_type: str
    clause_title: str
    page: int | None
    text: str
    confidence: str
    matched_pattern: str

    def to_dict(self, *, max_text_chars: int = 1200) -> dict[str, object]:
        text = self.text.strip()
        return {
            "clause_type": self.clause_type,
            "clause_title": self.clause_title,
            "page": self.page,
            "confidence": self.confidence,
            "matched_pattern": self.matched_pattern,
            "text": text[:max_text_chars],
            "text_truncated": len(text) > max_text_chars,
        }


@dataclass
class ContractClauseSegmentationReport:
    status: str = "NOT_RUN"
    method: str = "deterministic_clause_patterns_v1"
    clauses: list[ContractClause] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def clauses_of_type(self, *clause_types: str) -> list[ContractClause]:
        wanted = {clause_type for clause_type in clause_types if clause_type}
        return [clause for clause in self.clauses if clause.clause_type in wanted]

    def text_for_types(self, *clause_types: str, max_chars: int = 8000) -> str:
        chunks = [clause.text.strip() for clause in self.clauses_of_type(*clause_types)]
        return "\n\n".join(chunk for chunk in chunks if chunk)[:max_chars]

    def to_dict(self, *, max_clause_text_chars: int = 1200) -> dict[str, object]:
        counts: dict[str, int] = {}
        for clause in self.clauses:
            counts[clause.clause_type] = counts.get(clause.clause_type, 0) + 1
        return {
            "status": self.status,
            "method": self.method,
            "clause_count": len(self.clauses),
            "clause_type_counts": counts,
            "notes": list(self.notes),
            "clauses": [
                clause.to_dict(max_text_chars=max_clause_text_chars)
                for clause in self.clauses
            ],
        }


def segment_contract_clauses(document_text: str) -> ContractClauseSegmentationReport:
    """Segment a contract into auditable local clauses.

    This is intentionally deterministic and privacy-preserving. If you later
    replace it with a local model, keep the function signature and return
    `ContractClauseSegmentationReport` so the rest of Step 2.4 keeps working.
    """
    report = ContractClauseSegmentationReport()
    if not document_text.strip():
        report.status = "NO_TEXT"
        return report

    clauses: list[ContractClause] = []
    for page_number, page_text in _iter_pages(document_text):
        clauses.extend(_segment_page(page_text, page_number))

    clauses = _dedupe_clauses(clauses)
    if not clauses:
        report.status = "NO_CLAUSES_FOUND"
        report.notes.append("No formal or semantic contract clauses were detected.")
        return report

    report.status = "SEGMENTED"
    report.clauses = clauses
    return report


def _iter_pages(text: str) -> list[tuple[int | None, str]]:
    matches = list(PAGE_MARKER_RE.finditer(text))
    if not matches:
        return [(None, text)]

    pages: list[tuple[int | None, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        page_number = int(match.group(1))
        pages.append((page_number, text[start:end].strip()))
    return pages


def _segment_page(page_text: str, page_number: int | None) -> list[ContractClause]:
    prepared = _prepare_page_text(page_text)
    lines = [line.strip() for line in prepared.splitlines() if _has_text(line)]
    if not lines:
        return []

    clauses: list[ContractClause] = []
    current: list[str] = []
    current_hint = "other"

    for line in lines:
        hint = _classify_text(line)
        starts_clause = bool(FORMAL_HEADING_RE.search(line))
        semantic_boundary = (
            bool(current)
            and hint != "other"
            and current_hint != "other"
            and hint != current_hint
        )
        if current and (starts_clause or semantic_boundary):
            clauses.append(_build_clause(current, page_number, current_hint))
            current = []
            current_hint = "other"

        current.append(line)
        if current_hint == "other" and hint != "other":
            current_hint = hint

    if current:
        clauses.append(_build_clause(current, page_number, current_hint))
    return clauses


def _prepare_page_text(text: str) -> str:
    prepared = PAGE_MARKER_RE.sub(" ", text.replace("\x00", " "))
    prepared = CLAUSE_BREAK_RE.sub(r"\n\1", prepared)
    for pattern in SEMANTIC_BREAKS:
        prepared = pattern.sub("\n", prepared)
    prepared = re.sub(r"[ \t]+", " ", prepared)
    prepared = re.sub(r"\n{2,}", "\n", prepared)
    return prepared.strip()


def _build_clause(lines: list[str], page_number: int | None, hint: str) -> ContractClause:
    text = "\n".join(lines).strip()
    clause_title = _title_from_lines(lines)
    clause_type, matched_pattern = _classify_clause(clause_title, text, hint)
    confidence = "high" if matched_pattern.startswith("heading_") else "medium"
    if clause_type == "other":
        confidence = "low"
    return ContractClause(
        clause_type=clause_type,
        clause_title=clause_title,
        page=page_number,
        text=text,
        confidence=confidence,
        matched_pattern=matched_pattern,
    )


def _title_from_lines(lines: list[str]) -> str:
    if not lines:
        return ""
    first = re.sub(r"\s+", " ", lines[0]).strip(" .;-")
    if len(first) <= 140:
        return first
    for separator in (". ", "; ", ": "):
        index = first.find(separator)
        if 15 <= index <= 140:
            return first[: index + 1]
    return first[:140].rstrip(" ,;:-")


def _classify_clause(title: str, text: str, hint: str = "other") -> tuple[str, str]:
    combined = _normalize(f"{title}\n{text[:1800]}")
    title_norm = _normalize(title)
    for clause_type, label, keywords in _classification_rules():
        if any(_has_keyword(title_norm, keyword) for keyword in keywords):
            return clause_type, f"heading_{label}"
    for clause_type, label, keywords in _classification_rules():
        if any(_has_keyword(combined, keyword) for keyword in keywords):
            return clause_type, f"text_{label}"
    if hint in CLAUSE_TYPES:
        return hint, "semantic_boundary"
    return "other", "fallback"


def _classify_text(text: str) -> str:
    clause_type, _matched = _classify_clause("", text, "other")
    return clause_type


def _classification_rules() -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    return (
        ("signatures", "signature", ("ASSINADO", "ASSINATURA", "OUTORGADO", "FEITO EM", "CELEBRADO EM")),
        ("parties", "parties", ("OUTORGANTE", "SENHORIO", "ARRENDATARIO", "LOCADOR", "LOCATARIO", "NIF", "NIPC")),
        ("property", "property", ("IMOVEL", "PREDIO", "FRACAO", "MATRIZ", "ARTIGO MATRICIAL", "LOCAL ARRENDADO")),
        ("term", "term", ("PRAZO", "DURACAO", "VIGENCIA", "INICIO", "TERMO", "RENOVACAO")),
        ("rent", "rent", ("RENDA", "RENDA MENSAL", "MENSALIDADE", "VALOR DA RENDA")),
        ("payment", "payment", ("PAGAMENTO", "TRANSFERENCIA", "IBAN", "NIB", "VENCIMENTO", "ATE AO DIA")),
        ("deposit", "deposit", ("CAUCAO", "DEPOSITO", "GARANTIA")),
        ("expenses", "expenses", ("DESPESAS", "ENCARGOS", "CONDOMINIO", "IMI", "AGUA", "ELETRICIDADE", "LUZ")),
        ("communications", "communications", ("COMUNICACOES", "NOTIFICACOES", "MORADA PARA", "EMAIL")),
        ("annexes", "annexes", ("ANEXO", "ANEXOS", "DOCUMENTOS ANEXOS")),
        ("obligations", "obligations", ("OBRIGACOES", "DEVERES", "OBRAS", "CONSERVACAO", "MANUTENCAO")),
        ("title", "title", ("CONTRATO DE ARRENDAMENTO", "ARRENDAMENTO")),
    )


def _has_keyword(normalized_text: str, normalized_keyword: str) -> bool:
    if " " in normalized_keyword:
        return normalized_keyword in normalized_text
    return bool(re.search(rf"\b{re.escape(normalized_keyword)}\b", normalized_text))


def _dedupe_clauses(clauses: list[ContractClause]) -> list[ContractClause]:
    output: list[ContractClause] = []
    seen: set[tuple[int | None, str, str]] = set()
    for clause in clauses:
        fingerprint = _normalize(clause.text)[:280]
        key = (clause.page, clause.clause_type, fingerprint)
        if key in seen:
            continue
        seen.add(key)
        output.append(clause)
    return output


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value))
    plain = "".join(character for character in decomposed if not unicodedata.combining(character))
    plain = re.sub(r"[_/.-]+", " ", plain)
    return " ".join(plain.upper().split())


def _has_text(value: str) -> bool:
    return sum(character.isalnum() for character in value) >= 8


__all__ = [
    "CLAUSE_TYPES",
    "ContractClause",
    "ContractClauseSegmentationReport",
    "segment_contract_clauses",
]
