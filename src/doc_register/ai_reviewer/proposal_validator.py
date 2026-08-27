from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any

from ..models import ExtractionResult
from ..validators.validation_rules import (
    CURRENT_YEAR,
    GENERIC_NAMES,
    MAX_ACCEPTED_FUTURE_YEAR_OFFSET,
    MONEY_PATTERN,
    VALID_PROPERTY_SECTION_PATTERN,
    compact_alphanumeric,
    normalize_text,
)
from .models import AIFieldDecision, AIFieldProposal

METHOD = "focused_local_ollama"


@dataclass(frozen=True)
class AcceptancePolicy:
    auto_accept_confidence: float = 0.90
    human_confidence: float = 0.70


def validate_proposal(
    proposal: AIFieldProposal,
    result: ExtractionResult,
    *,
    document_text: str,
    evidence_text: str,
    policy: AcceptancePolicy | None = None,
) -> AIFieldDecision:
    rules = policy or AcceptancePolicy()
    original_value = str(getattr(result, proposal.field_name, "") or "").strip()
    checks: list[str] = []

    if not proposal.proposed_value:
        return _decision(proposal, original_value, "REJECTED", checks, "empty_proposal")
    if not proposal.evidence:
        return _decision(proposal, original_value, "REJECTED", checks, "missing_evidence")
    if not _text_contains_evidence(document_text, proposal.evidence):
        return _decision(proposal, original_value, "REJECTED", checks, "evidence_not_found_in_document")
    checks.append("evidence_found_in_document")

    field_validator = {
        "lessor": _validate_party,
        "lessee": _validate_party,
        "owner_name": _validate_party,
        "signed_date": _validate_signed_date,
        "property_article": _validate_property_article,
        "property_section": _validate_property_section,
        "monthly_rent": _validate_monthly_rent,
    }.get(proposal.field_name)
    if field_validator is None:
        return _decision(proposal, original_value, "REJECTED", checks, "unsupported_field")

    reason = field_validator(proposal, document_text, evidence_text, checks)
    if reason:
        return _decision(proposal, original_value, "REJECTED", checks, reason)

    if proposal.confidence >= rules.auto_accept_confidence:
        return _decision(proposal, original_value, "AUTO_ACCEPTED", checks, "")
    if proposal.confidence >= rules.human_confidence:
        return _decision(
            proposal,
            original_value,
            "AI_PROPOSED_HUMAN_REQUIRED",
            checks,
            "confidence_requires_human_review",
        )
    return _decision(proposal, original_value, "REJECTED", checks, "low_confidence")


def _validate_party(
    proposal: AIFieldProposal,
    document_text: str,
    evidence_text: str,
    checks: list[str],
) -> str:
    value = proposal.proposed_value
    normalized = normalize_text(value)
    if normalized in GENERIC_NAMES:
        return "generic_party_label"
    if len(value) < 5 or len(value) > 250:
        return "implausible_party_length"
    if re.search(r"\b(?:NIF|NIPC|CONTRIBUINTE|RESIDENTE|MORADA|CASADO|SOLTEIRO|VIUVO|CARTAO|BI)\b", normalized):
        return "party_contains_non_name_details"
    if "." in value and len(value.split()) > 18:
        return "party_looks_like_clause"

    candidates = [part.strip() for part in re.split(r"\s*;\s*", value) if part.strip()]
    if not candidates:
        return "empty_party"
    unsupported = [
        candidate
        for candidate in candidates
        if not _candidate_supported_by_text(candidate, document_text)
    ]
    if unsupported:
        return "party_name_not_supported_by_text"
    checks.append("names_found_in_document")
    checks.append("not_generic")
    return ""


def _validate_signed_date(
    proposal: AIFieldProposal,
    document_text: str,
    evidence_text: str,
    checks: list[str],
) -> str:
    parsed = _parse_date(proposal.proposed_value)
    if parsed is None:
        return "invalid_date"
    if parsed.year > CURRENT_YEAR + MAX_ACCEPTED_FUTURE_YEAR_OFFSET:
        return "future_date"
    normalized_evidence = normalize_text(proposal.evidence)
    if not any(marker in normalized_evidence for marker in ("ASSINADO", "ASSINATURA", "CELEBRADO", "OUTORGADO", "AOS")):
        return "signature_context_missing"
    if any(marker in normalized_evidence for marker in ("VALIDADE", "REGISTO", "REGISTRO", "LICENCA", "LICENÇA", "EMISSAO", "EMISSÃO")):
        return "date_context_is_not_signature"
    if not _date_variant_found(parsed, document_text):
        return "date_not_found_in_document"
    checks.append("valid_signature_date")
    return ""


def _validate_property_article(
    proposal: AIFieldProposal,
    document_text: str,
    evidence_text: str,
    checks: list[str],
) -> str:
    value = compact_alphanumeric(proposal.proposed_value)
    if not re.fullmatch(r"\d{1,8}", value):
        return "invalid_property_article_format"
    if not _context_has(proposal.evidence, ("ARTIGO", "MATRIZ", "INSCRITO", "PREDIAL")):
        return "property_article_context_missing"
    if not _context_has(proposal.evidence, ("MATRIZ", "INSCRITO", "PREDIO", "PRÉDIO", "PREDIAL", "CADERNETA", "RUSTICO", "RÚSTICO", "URBANO")):
        return "property_block_context_missing"
    if value not in compact_alphanumeric(document_text):
        return "property_article_not_found_in_document"
    checks.append("property_article_found_in_document")
    return ""


def _validate_property_section(
    proposal: AIFieldProposal,
    document_text: str,
    evidence_text: str,
    checks: list[str],
) -> str:
    value = compact_alphanumeric(proposal.proposed_value)
    if not VALID_PROPERTY_SECTION_PATTERN.fullmatch(value):
        return "invalid_property_section_format"
    if not _context_has(proposal.evidence, ("SECCAO", "SEÇÃO", "SECAO", "SECÇÃO", "SEC")):
        return "property_section_context_missing"
    if not _context_has(proposal.evidence, ("MATRIZ", "INSCRITO", "PREDIO", "PRÉDIO", "PREDIAL", "CADERNETA", "RUSTICO", "RÚSTICO", "URBANO")):
        return "property_block_context_missing"
    if value not in compact_alphanumeric(document_text):
        return "property_section_not_found_in_document"
    checks.append("property_section_found_in_document")
    return ""


def _validate_monthly_rent(
    proposal: AIFieldProposal,
    document_text: str,
    evidence_text: str,
    checks: list[str],
) -> str:
    value = " ".join(proposal.proposed_value.split())
    if not MONEY_PATTERN.fullmatch(value):
        return "invalid_monthly_rent_format"
    normalized_evidence = normalize_text(proposal.evidence)
    monthly_markers = ("RENDA MENSAL", "MENSALMENTE", "POR MES", "POR MÊS", "MENSAIS", "VALOR MENSAL")
    if not any(marker in normalized_evidence for marker in monthly_markers):
        return "monthly_context_missing"
    forbidden = (
        "POR ANO", "ANUAL", "HECTARE", "ASSINATURA", "PRECO DE COMPRA",
        "PREÇO DE COMPRA", "SINAL", "PROMESSA DE COMPRA", "CEDENCIA",
        "CEDÊNCIA", "CESSAO", "CESSÃO",
    )
    if any(marker in normalized_evidence for marker in forbidden):
        return "non_monthly_money_context"
    checks.append("monthly_rent_context_confirmed")
    return ""


def _decision(
    proposal: AIFieldProposal,
    original_value: str,
    decision: str,
    checks: list[str],
    reason: str,
) -> AIFieldDecision:
    return AIFieldDecision(
        field_name=proposal.field_name,
        original_value=original_value,
        proposed_value=proposal.proposed_value,
        decision=decision,
        confidence=proposal.confidence,
        method=METHOD,
        evidence=proposal.evidence,
        validation_results=list(checks),
        reason=reason,
    )


def _text_contains_evidence(document_text: str, evidence: str) -> bool:
    normalized_doc = normalize_text(document_text)
    normalized_evidence = normalize_text(evidence)
    if not normalized_doc or not normalized_evidence:
        return False
    if normalized_evidence in normalized_doc:
        return True
    tokens = [token for token in normalized_evidence.split() if len(token) >= 4]
    if not tokens:
        return False
    matched = sum(1 for token in tokens if token in normalized_doc)
    return matched >= max(3, int(len(tokens) * 0.75))


def _candidate_supported_by_text(candidate: str, document_text: str) -> bool:
    normalized_candidate = normalize_text(candidate)
    normalized_document = normalize_text(document_text)
    if not normalized_candidate or not normalized_document:
        return False
    if normalized_candidate in normalized_document:
        return True
    tokens = [token for token in normalized_candidate.split() if len(token) >= 3]
    if len(tokens) < 2:
        return False
    ordered_pattern = r"\b" + r"\W{0,35}".join(re.escape(token) for token in tokens) + r"\b"
    if re.search(ordered_pattern, normalized_document):
        return True
    matched = sum(1 for token in tokens if re.search(rf"\b{re.escape(token)}\b", normalized_document))
    return matched >= max(2, len(tokens) - 1)


def _parse_date(value: str) -> datetime | None:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _date_variant_found(date_value: datetime, text: str) -> bool:
    variants = {
        date_value.strftime("%Y-%m-%d"),
        date_value.strftime("%d/%m/%Y"),
        date_value.strftime("%d-%m-%Y"),
        date_value.strftime("%-d/%-m/%Y") if hasattr(date_value, "strftime") else "",
    }
    normalized_text = normalize_text(text)
    return any(normalize_text(variant) in normalized_text for variant in variants if variant)


def _context_has(value: str, markers: tuple[str, ...]) -> bool:
    normalized = normalize_text(value)
    return any(marker in normalized for marker in markers)
