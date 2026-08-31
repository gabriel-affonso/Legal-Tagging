from __future__ import annotations

from typing import Any

from ..models import ExtractionResult
from ..validators.validation_rules import CRITICAL_FIELDS, requires_monthly_rent
from .models import VisionPagePlan, VisionPlan


def build_vision_plan(
    result: ExtractionResult,
    *,
    signals: Any,
    final_resolution_report: Any,
    config: Any,
) -> VisionPlan:
    """Select only poor first contract pages with unresolved critical facts."""
    if not bool(getattr(config, "vision_enabled", False)):
        return VisionPlan(False, "vision_disabled")
    if not _is_contract_candidate(result, signals):
        return VisionPlan(False, "not_contract_candidate")

    fields = _uncertain_critical_fields(result, final_resolution_report, config)
    if not fields:
        return VisionPlan(False, "critical_points_confident")

    page_limit = max(
        1,
        min(
            int(getattr(config, "vision_first_page_count", 2)),
            int(getattr(config, "vision_max_pages_per_document", 2)),
        ),
    )
    threshold = float(getattr(config, "vision_ocr_quality_threshold", 0.60))
    selected: list[VisionPagePlan] = []
    for page in list(getattr(final_resolution_report, "page_quality", ())):
        page_number = getattr(page, "page", None)
        if page_number is None or page_number > page_limit:
            continue
        reasons = _low_quality_reasons(page, threshold, fields)
        if reasons:
            selected.append(VisionPagePlan(page_number, tuple(reasons)))

    if not selected:
        return VisionPlan(False, "first_contract_pages_have_usable_ocr", tuple(fields))
    return VisionPlan(True, "low_ocr_and_uncertain_critical_points", tuple(fields), tuple(selected))


def _is_contract_candidate(result: ExtractionResult, signals: Any) -> bool:
    category = str(getattr(result, "document_category", "") or "").lower()
    suggested = str(getattr(signals, "suggested_category", "") or "").lower()
    return category == "lease_contract" or suggested == "lease_contract" or bool(
        getattr(signals, "contract_type", "")
    )


def _uncertain_critical_fields(
    result: ExtractionResult,
    report: Any,
    config: Any,
) -> list[str]:
    fields = list(CRITICAL_FIELDS["lease_contract"])
    if requires_monthly_rent(result):
        fields.append("monthly_rent")
    issues = _issues(result)
    score_threshold = float(getattr(config, "vision_critical_candidate_threshold", 0.78))
    decisions = getattr(report, "decisions", {}) or {}
    uncertain: list[str] = []
    for field_name in fields:
        value = str(getattr(result, field_name, "") or "").strip()
        decision = decisions.get(field_name, {}) if isinstance(decisions, dict) else {}
        score = float(decision.get("confidence", 0.0) or 0.0) if isinstance(decision, dict) else 0.0
        field_issues = any(field_name in issue for issue in issues)
        if not value or field_issues or score < score_threshold:
            uncertain.append(field_name)
    if str(result.confidence or "").lower() != "high" and not uncertain:
        uncertain = fields
    return list(dict.fromkeys(uncertain))


def _issues(result: ExtractionResult) -> list[str]:
    values: list[str] = []
    for raw in (result.validation_issues, result.review_reason):
        for item in str(raw or "").replace(",", ";").split(";"):
            item = item.strip()
            if item and item not in values:
                values.append(item)
    return values


def _low_quality_reasons(page: Any, threshold: float, fields: list[str]) -> list[str]:
    reasons: list[str] = []
    overall = float(getattr(page, "overall_page_quality", 0.0) or 0.0)
    language = float(getattr(page, "page_language_quality", 0.0) or 0.0)
    entity = float(getattr(page, "entity_recoverability", 0.0) or 0.0)
    numeric = float(getattr(page, "numeric_recoverability", 0.0) or 0.0)
    flags = set(getattr(page, "flags", ()) or ())
    if overall < threshold:
        reasons.append("overall_page_quality_low")
    if language < 0.55:
        reasons.append("page_language_quality_low")
    if {"lessor", "lessee"} & set(fields) and entity < 0.55:
        reasons.append("entity_recoverability_low")
    numeric_fields = {"signed_date", "property_article", "property_section", "monthly_rent"}
    if numeric_fields & set(fields) and numeric < 0.50:
        reasons.append("numeric_recoverability_low")
    if "semantic_ocr_degradation" in flags:
        reasons.append("semantic_ocr_degradation")
    return reasons
