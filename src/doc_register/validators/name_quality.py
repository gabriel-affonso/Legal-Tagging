from __future__ import annotations

import re
import unicodedata


GENERIC_NAME_LABELS = {
    "SENHORIO", "SENHORIOS", "ARRENDATARIO", "ARRENDATARIA",
    "ARRENDATARIOS", "ARRENDATARIAS", "PROPRIETARIO", "PROPRIETARIA",
    "PROPRIETARIOS", "PROPRIETARIAS", "TITULAR", "TITULARES",
    "BENEFICIARIO", "BENEFICIARIA", "BENEFICIARIOS", "BENEFICIARIAS",
    "PAGADOR", "ORDENANTE", "RECEBEDOR", "PRIMEIRO OUTORGANTE",
    "PRIMEIRA OUTORGANTE", "SEGUNDO OUTORGANTE", "SEGUNDA OUTORGANTE",
    "OUTORGANTE", "OUTORGANTES", "CEDENTE", "CESSIONARIO", "CESSIONARIA",
}

COMPANY_MARKERS = {
    "SA", "S A", "S.A", "S.A.", "LDA", "LDA.", "UNIPESSOAL",
    "LIMITADA", "SOCIEDADE", "ENERGIA", "IMOBILIARIA", "IMOBILIARIA",
    "HOLDING", "SGPS", "SUCURSAL",
}

NON_NAME_MARKERS = (
    "NIF", "NIPC", "CONTRIBUINTE", "RESIDENTE", "RESIDENCIA",
    "NATURAL DE", "CASADO", "CASADA", "SOLTEIRO", "SOLTEIRA",
    "VIUVO", "VIUVA", "PORTADOR", "PORTADORA", "CARTAO", "CARTÃO",
    "BILHETE DE IDENTIDADE", "MORADA", "RUA", "AVENIDA", "PRACA",
    "PRAÇA", "CODIGO POSTAL", "CÓDIGO POSTAL", "FREGUESIA", "CONCELHO",
    "DISTRITO", "EMAIL", "TELEFONE",
)

NAME_FIELDS = {
    "lessor", "lessee", "owner_name", "bank_account_holder",
    "payer", "payee",
}


def validate_name_field(field_name: str, value: str) -> list[str]:
    if field_name not in NAME_FIELDS:
        return []
    raw = str(value or "").strip()
    if not raw:
        return []

    normalized = normalize_name_text(raw)
    issues: list[str] = []
    if normalized in GENERIC_NAME_LABELS:
        issues.append(f"generic_{field_name}")
        return issues

    corruption_reason = ocr_corruption_reason(raw)
    if corruption_reason:
        issues.append(f"invalid_{field_name}_format")
        issues.append("ocr_corrupted_party_name")
        return _unique(issues)

    if any(marker in normalized for marker in NON_NAME_MARKERS):
        issues.append(f"invalid_{field_name}_format")
    if _looks_like_clause(raw):
        issues.append(f"invalid_{field_name}_format")
    if not _has_plausible_name_shape(raw):
        issues.append(f"invalid_{field_name}_format")

    return _unique(issues)


def is_plausible_name(value: str) -> bool:
    return not validate_name_field("owner_name", value)


def ocr_corruption_reason(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""

    chars = [char for char in raw if not char.isspace()]
    if not chars:
        return "empty"

    letters = sum(char.isalpha() for char in chars)
    digits = sum(char.isdigit() for char in chars)
    punctuation = len(chars) - letters - digits
    normalized = normalize_name_text(raw)
    tokens = [token for token in re.split(r"\s+", normalized) if token]
    alpha_tokens = [token for token in tokens if re.search(r"[A-Z]", token)]
    short_tokens = [
        token for token in alpha_tokens
        if len(token) == 1 and token not in {"E"}
    ]

    if letters / len(chars) < 0.55:
        return "low_letter_ratio"
    if digits and digits / len(chars) > 0.08:
        return "too_many_digits"
    if punctuation / len(chars) > 0.35:
        return "too_many_symbols"
    if len(short_tokens) >= 3:
        return "fragmented_tokens"
    if any(marker in normalized for marker in ("NATURAL DE", "RESIDENTE", "PORTADOR")) and len(alpha_tokens) < 4:
        return "ocr_fragment_with_personal_detail"
    return ""


def normalize_name_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value))
    plain = "".join(
        character for character in decomposed
        if not unicodedata.combining(character)
    )
    plain = re.sub(r"[^A-Za-z0-9&.,' -]", " ", plain)
    plain = re.sub(r"\s+", " ", plain)
    return plain.strip().upper()


def _has_plausible_name_shape(value: str) -> bool:
    normalized = normalize_name_text(value)
    if any(marker in normalized for marker in COMPANY_MARKERS):
        return bool(re.search(r"[A-Z]{3,}", normalized))
    words = [
        word for word in re.findall(r"[A-ZÀ-ÖØ-Þ]{2,}", normalized)
        if word not in {"DE", "DA", "DO", "DOS", "DAS", "E"}
    ]
    return len(words) >= 2


def _looks_like_clause(value: str) -> bool:
    stripped = str(value or "").strip()
    if len(stripped) > 180:
        return True
    normalized = normalize_name_text(stripped)
    if stripped.count(".") >= 2 and not any(marker in normalized for marker in COMPANY_MARKERS):
        return True
    clause_markers = (" DORAVANTE ", " NA QUALIDADE ", " COM SEDE ", " DECLARA ")
    return any(marker in f" {normalized} " for marker in clause_markers)


def _unique(values: list[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        if value and value not in output:
            output.append(value)
    return output


__all__ = [
    "GENERIC_NAME_LABELS",
    "NAME_FIELDS",
    "is_plausible_name",
    "normalize_name_text",
    "ocr_corruption_reason",
    "validate_name_field",
]
