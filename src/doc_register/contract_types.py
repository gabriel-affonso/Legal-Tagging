from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


LEASE_AGREEMENT = "contrato_de_arrendamento"
PURCHASE_OPTION = "opcao_de_compra"
PURCHASE_PROMISE = "contrato_promessa_compra_venda"
CONTRACT_POSITION_ASSIGNMENT = "acordo_cedencia_posicao_contratual"
LEASE_RATIFICATION = "ratificacao"
LEASE_ADDENDUM = "aditamento"
LEASE_RENEWAL = "renovacao"
LEASE_TERMINATION = "rescisao"


@dataclass(frozen=True)
class ContractTypeDefinition:
    code: str
    label: str
    aliases: tuple[str, ...]
    requires_monthly_rent: bool


CONTRACT_TYPE_DEFINITIONS: tuple[ContractTypeDefinition, ...] = (
    ContractTypeDefinition(
        code=CONTRACT_POSITION_ASSIGNMENT,
        label="Acordo de Cedencia de Posicao Contratual",
        aliases=(
            "acordo de cedencia de posicao contratual",
            "cedencia de posicao contratual",
            "cessao de posicao contratual",
            "cessao da posicao contratual",
            "cedente",
            "cessionario",
            "cessionaria",
        ),
        requires_monthly_rent=False,
    ),
    ContractTypeDefinition(
        code=PURCHASE_PROMISE,
        label="Contrato Promessa de Compra e Venda",
        aliases=(
            "cpcv",
            "contrato promessa de compra e venda",
            "contrato-promessa de compra e venda",
            "promessa de compra e venda",
            "promitente vendedor",
            "promitente comprador",
        ),
        requires_monthly_rent=False,
    ),
    ContractTypeDefinition(
        code=PURCHASE_OPTION,
        label="Opcao de Compra",
        aliases=(
            "opcao de compra",
            "opção de compra",
            "direito de opcao",
            "direito de opção",
            "option agreement",
        ),
        requires_monthly_rent=False,
    ),
    ContractTypeDefinition(
        code=LEASE_RATIFICATION,
        label="Ratificacao",
        aliases=("ratificacao", "ratificação"),
        requires_monthly_rent=False,
    ),
    ContractTypeDefinition(
        code=LEASE_ADDENDUM,
        label="Aditamento",
        aliases=("aditamento", "addendum"),
        requires_monthly_rent=False,
    ),
    ContractTypeDefinition(
        code=LEASE_RENEWAL,
        label="Renovacao",
        aliases=("renovacao", "renovação", "renewal"),
        requires_monthly_rent=False,
    ),
    ContractTypeDefinition(
        code=LEASE_TERMINATION,
        label="Rescisao",
        aliases=("rescisao", "rescisão", "cessacao", "cessação", "termination"),
        requires_monthly_rent=False,
    ),
    ContractTypeDefinition(
        code=LEASE_AGREEMENT,
        label="Contrato de Arrendamento",
        aliases=(
            "contrato de arrendamento",
            "arrendamento",
            "lease agreement",
            "locacao",
            "locação",
        ),
        requires_monthly_rent=True,
    ),
)


def detect_contract_type(*values: str) -> ContractTypeDefinition | None:
    normalized = _normalize(" ".join(value for value in values if value))
    if not normalized:
        return None
    for definition in CONTRACT_TYPE_DEFINITIONS:
        if any(_normalize(alias) in normalized for alias in definition.aliases):
            return definition
    return None


def canonical_contract_type(value: str) -> ContractTypeDefinition | None:
    normalized = _normalize(value)
    for definition in CONTRACT_TYPE_DEFINITIONS:
        if normalized == _normalize(definition.code) or normalized == _normalize(definition.label):
            return definition
        if any(normalized == _normalize(alias) for alias in definition.aliases):
            return definition
    return None


def is_contractual_text(*values: str) -> bool:
    normalized = _normalize(" ".join(value for value in values if value))
    return bool(
        normalized
        and (
            "CONTRATO" in normalized
            or "ACORDO" in normalized
            or detect_contract_type(normalized) is not None
        )
    )


def contract_type_options_for_prompt() -> str:
    return "\n".join(
        f"- {definition.code}: {definition.label}"
        for definition in CONTRACT_TYPE_DEFINITIONS
    )


def apply_contract_type_hints(result: object, *evidence_values: str) -> None:
    """Populate canonical contract type fields when evidence is strong enough."""
    current_values = [
        str(getattr(result, field_name, "") or "")
        for field_name in ("contract_type", "document_subtype", "document_type")
    ]
    definition = (
        canonical_contract_type(str(getattr(result, "contract_type", "") or ""))
        or canonical_contract_type(str(getattr(result, "document_subtype", "") or ""))
        or detect_contract_type(*current_values, *evidence_values)
    )
    if definition is None:
        return

    category = str(getattr(result, "document_category", "") or "").strip().lower()
    if category in {"", "other"}:
        setattr(result, "document_category", "lease_contract")

    if _is_blank_or_generic_contract_label(str(getattr(result, "document_type", "") or "")):
        setattr(result, "document_type", definition.label)
    setattr(result, "document_subtype", definition.code)
    setattr(result, "contract_type", definition.code)


def _is_blank_or_generic_contract_label(value: str) -> bool:
    normalized = _normalize(value)
    return normalized in {
        "",
        "CONTRATO",
        "ACORDO",
        "DOCUMENTO CONTRATUAL",
        "INSTRUMENTO CONTRATUAL",
    }


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value))
    plain = "".join(
        character for character in decomposed
        if not unicodedata.combining(character)
    )
    plain = re.sub(r"[_/.-]+", " ", plain)
    return " ".join(plain.upper().split())
