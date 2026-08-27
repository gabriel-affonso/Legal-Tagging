from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import re

from ..contract_types import canonical_contract_type, detect_contract_type
from ..models import ExtractionResult
from .contract_clause_segmentation import (
    ContractClauseSegmentationReport,
    segment_contract_clauses,
)
from .contract_structure import party_candidates_from_zones
from .name_quality import validate_name_field
from .property_recovery import recover_property_group
from .signature_date import normalize_date, recover_signature_date
from .validation_rules import GENERIC_NAMES, normalize_text


DATE_RE = r"(?:\d{1,2}[/-]\d{1,2}[/-](?:19|20)\d{2}|(?:19|20)\d{2}-\d{2}-\d{2})"
MONTHLY_RENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:renda\s+mensal|renda|mensalidade)\b.{0,120}?"
        r"((?:\d{1,3}(?:[ .]\d{3})*|\d+)(?:,\d{1,2})?)\s*(EUR|€|euros?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b((?:\d{1,3}(?:[ .]\d{3})*|\d+)(?:,\d{1,2})?)\s*(EUR|€|euros?)"
        r".{0,80}\b(?:mensais|por\s+m[eê]s|renda\s+mensal)\b",
        re.IGNORECASE,
    ),
)
RENT_PAYMENT_DAY_RE = re.compile(
    r"\b(?:at[eé]\s+ao\s+dia|at[eé]\s+o\s+dia|dia)\s+(\d{1,2})"
    r"(?:\s+de\s+cada\s+m[eê]s|\s+do\s+m[eê]s)?\b",
    re.IGNORECASE,
)
START_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:tem\s+in[ií]cio|inicia-se|come[cç]a|produz\s+efeitos)\b.{0,100}?(" + DATE_RE + ")", re.IGNORECASE),
    re.compile(r"\bin[ií]cio\b.{0,60}?(" + DATE_RE + ")", re.IGNORECASE),
)
END_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:termina|termo|cessa|fim)\b.{0,100}?(" + DATE_RE + ")", re.IGNORECASE),
)


@dataclass
class ContractClauseIntegrationReport:
    segmentation: ContractClauseSegmentationReport
    changes: list[dict[str, str]] = field(default_factory=list)
    status: str = "NOT_RUN"
    reason: str = ""

    def to_dict(self, *, max_clause_text_chars: int = 1200) -> dict[str, object]:
        payload = self.segmentation.to_dict(max_clause_text_chars=max_clause_text_chars)
        payload["integration_status"] = self.status
        payload["integration_reason"] = self.reason
        payload["changes"] = list(self.changes)
        return payload


def apply_contract_clause_segmentation(
    result: ExtractionResult,
    *,
    document_text: str,
    enabled: bool = True,
    max_clause_text_chars: int = 1200,
) -> ExtractionResult:
    report = _build_report(result, document_text=document_text, enabled=enabled)
    _attach_report(result, report, max_clause_text_chars=max_clause_text_chars)
    return result


def _build_report(
    result: ExtractionResult,
    *,
    document_text: str,
    enabled: bool,
) -> ContractClauseIntegrationReport:
    segmentation = ContractClauseSegmentationReport(status="NOT_RUN")
    report = ContractClauseIntegrationReport(segmentation=segmentation)

    if not enabled:
        report.status = "NOT_REQUIRED"
        report.reason = "contract_clause_segmentation_disabled"
        return report
    if not _is_contract_candidate(result, document_text):
        report.status = "NOT_APPLICABLE"
        report.reason = "not_a_contract_candidate"
        return report
    if not document_text.strip():
        report.status = "NO_TEXT"
        report.reason = "empty_document_text"
        return report

    try:
        segmentation = segment_contract_clauses(document_text)
    except Exception as exc:
        report.segmentation.status = "FAILED"
        report.status = "FAILED"
        report.reason = f"contract_clause_segmentation_failed: {str(exc)[:300]}"
        return report

    report.segmentation = segmentation
    if segmentation.status != "SEGMENTED":
        report.status = segmentation.status
        report.reason = "no_reusable_contract_clauses"
        return report

    _recover_parties_from_clauses(result, report)
    _recover_property_from_clauses(result, report)
    _recover_dates_from_clauses(result, report)
    _recover_rent_from_clauses(result, report)
    report.status = "APPLIED" if report.changes else "SEGMENTED"
    report.reason = "clause_guided_recovery_applied" if report.changes else "no_field_changes_needed"
    return report


def _is_contract_candidate(result: ExtractionResult, document_text: str) -> bool:
    category = str(result.document_category or "").strip().lower()
    if category == "lease_contract":
        return True
    subtype_values = " ".join(
        str(getattr(result, field_name, "") or "")
        for field_name in ("document_type", "document_subtype", "contract_type")
    )
    return detect_contract_type(subtype_values, document_text[:1600]) is not None


def _recover_parties_from_clauses(
    result: ExtractionResult,
    report: ContractClauseIntegrationReport,
) -> None:
    parties_text = report.segmentation.text_for_types("parties", "title", max_chars=6000)
    if not parties_text:
        return
    for field_name in ("lessor", "lessee"):
        if not _needs_party_recovery(result, field_name):
            continue
        candidates = party_candidates_from_zones(parties_text, field_name)
        if not candidates:
            continue
        values: list[str] = []
        evidence: list[str] = []
        for candidate, candidate_evidence in candidates:
            if candidate not in values:
                values.append(candidate)
                evidence.append(candidate_evidence)
        if values:
            _set_field(
                result,
                report,
                field_name,
                "; ".join(values[:4]),
                "clause_segmentation_party_zone",
                "\n".join(evidence)[:260],
            )


def _recover_property_from_clauses(
    result: ExtractionResult,
    report: ContractClauseIntegrationReport,
) -> None:
    property_text = report.segmentation.text_for_types("property", max_chars=5000)
    if not property_text:
        return
    group = recover_property_group(property_text)
    if _is_blank(result.property_article) and group.article:
        _set_field(
            result,
            report,
            "property_article",
            group.article,
            "clause_segmentation_property_group",
            group.evidence,
        )
    if _is_blank(result.property_section) and group.section:
        _set_field(
            result,
            report,
            "property_section",
            group.section,
            "clause_segmentation_property_group",
            group.evidence,
        )


def _recover_dates_from_clauses(
    result: ExtractionResult,
    report: ContractClauseIntegrationReport,
) -> None:
    signature_text = report.segmentation.text_for_types("signatures", max_chars=4000)
    if _is_blank(result.signed_date) and signature_text:
        candidate = recover_signature_date(signature_text)
        if candidate:
            _set_field(
                result,
                report,
                "signed_date",
                candidate.value,
                "clause_segmentation_signature_date",
                candidate.evidence,
            )

    term_text = report.segmentation.text_for_types("term", max_chars=5000)
    if not term_text:
        return
    if _is_blank(result.contract_start_date):
        start_date = _first_normalized_date(term_text, START_DATE_PATTERNS)
        if start_date:
            _set_field(
                result,
                report,
                "contract_start_date",
                start_date,
                "clause_segmentation_term_start",
                _date_evidence(term_text, start_date),
            )
    if _is_blank(result.contract_end_date):
        end_date = _first_normalized_date(term_text, END_DATE_PATTERNS)
        if end_date:
            _set_field(
                result,
                report,
                "contract_end_date",
                end_date,
                "clause_segmentation_term_end",
                _date_evidence(term_text, end_date),
            )


def _recover_rent_from_clauses(
    result: ExtractionResult,
    report: ContractClauseIntegrationReport,
) -> None:
    rent_text = report.segmentation.text_for_types("rent", "payment", max_chars=6000)
    if not rent_text:
        return

    if _is_blank(result.monthly_rent) and _requires_monthly_rent(result):
        for pattern in MONTHLY_RENT_PATTERNS:
            match = pattern.search(rent_text)
            if not match:
                continue
            amount = match.group(1).replace(" ", "")
            currency = "EUR" if match.group(2).upper() in {"EUR", "€", "EUROS"} else match.group(2).upper()
            _set_field(
                result,
                report,
                "monthly_rent",
                f"{amount} {currency}",
                "clause_segmentation_monthly_rent",
                _match_evidence(rent_text, match),
            )
            if _is_blank(result.currency):
                result.currency = currency
            break

    if _is_blank(result.rent_payment_day):
        match = RENT_PAYMENT_DAY_RE.search(rent_text)
        if match:
            day = int(match.group(1))
            if 1 <= day <= 31:
                _set_field(
                    result,
                    report,
                    "rent_payment_day",
                    str(day),
                    "clause_segmentation_rent_payment_day",
                    _match_evidence(rent_text, match),
                )


def _requires_monthly_rent(result: ExtractionResult) -> bool:
    canonical = (
        canonical_contract_type(str(result.contract_type or ""))
        or canonical_contract_type(str(result.document_subtype or ""))
        or detect_contract_type(
            str(result.document_type or ""),
            str(result.document_subtype or ""),
            str(result.contract_type or ""),
        )
    )
    return canonical.requires_monthly_rent if canonical else True


def _first_normalized_date(text: str, patterns: tuple[re.Pattern[str], ...]) -> str:
    for pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue
        value = normalize_date(match.group(1))
        if value:
            return value
    return ""


def _date_evidence(text: str, value: str) -> str:
    if not value:
        return text[:240]
    day_month_year = value[8:10] + "/" + value[5:7] + "/" + value[:4]
    index = text.find(day_month_year)
    if index < 0:
        index = text.find(value)
    if index < 0:
        return text[:240]
    start = max(0, index - 90)
    end = min(len(text), index + 120)
    return text[start:end]


def _match_evidence(text: str, match: re.Match[str]) -> str:
    start = max(0, match.start() - 90)
    end = min(len(text), match.end() + 120)
    return text[start:end]


def _needs_party_recovery(result: ExtractionResult, field_name: str) -> bool:
    value = str(getattr(result, field_name, "") or "").strip()
    if not value:
        return True
    if normalize_text(value) in GENERIC_NAMES:
        return True
    return bool(validate_name_field(field_name, value))


def _is_blank(value: Any) -> bool:
    return not str(value or "").strip()


def _set_field(
    result: ExtractionResult,
    report: ContractClauseIntegrationReport,
    field_name: str,
    value: str,
    method: str,
    evidence: str,
) -> None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return
    setattr(result, field_name, cleaned)
    report.changes.append(
        {
            "field": field_name,
            "value": cleaned,
            "method": method,
            "confidence": "high" if method.startswith("clause_segmentation_") else "medium",
            "evidence": str(evidence or "").strip()[:300],
        }
    )


def _attach_report(
    result: ExtractionResult,
    report: ContractClauseIntegrationReport,
    *,
    max_clause_text_chars: int,
) -> None:
    raw = result.raw_json if isinstance(result.raw_json, dict) else {}
    result.raw_json = dict(raw)
    result.raw_json["contract_clause_segmentation"] = report.to_dict(
        max_clause_text_chars=max_clause_text_chars
    )
    if report.changes:
        note = "Step 2.4 clause segmentation recovered: " + ", ".join(
            change["field"] for change in report.changes
        )
        result.extraction_notes = " | ".join(
            part for part in (result.extraction_notes, note) if part
        )


__all__ = [
    "ContractClauseIntegrationReport",
    "apply_contract_clause_segmentation",
]
