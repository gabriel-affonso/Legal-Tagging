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

    focused_pages = tuple(
        int(page)
        for page in (getattr(final_resolution_report, "focused_visual_pages", ()) or ())
        if int(page) > 0
    )
    fields = _uncertain_critical_fields(result, final_resolution_report, config)
    if bool(getattr(config, "step_3_7_enabled", False)) and focused_pages:
        if not fields:
            # The page itself still needs confirmation even when contract-side
            # values are complete. These two fields provide a short, useful
            # cadastral read without widening the visual request.
            fields = ["property_article", "property_section"]
        reasons = tuple(
            str(reason)
            for reason in (
                getattr(final_resolution_report, "focused_visual_reasons", ()) or ()
            )
        ) or ("focused_cadastral_confirmation",)
        pages = tuple(
            VisionPagePlan(page_number, reasons) for page_number in focused_pages
        )
        return VisionPlan(
            True,
            "step3_7_cadastral_confirmation",
            tuple(fields),
            pages,
        )

    if not fields:
        return VisionPlan(False, "critical_points_confident")

    if bool(getattr(config, "vision_recall_mode", False)):
        return _build_recall_plan(result, final_resolution_report, config, fields)

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


def _build_recall_plan(result: ExtractionResult, report: Any, config: Any, fields: list[str]) -> VisionPlan:
    """Target the evidence-bearing pages instead of re-reading a whole PDF."""
    qualities = list(getattr(report, "page_quality", ()) or ())
    zones = list(getattr(report, "zones", ()) or ())
    page_numbers = {int(getattr(page, "page", 0) or 0) for page in qualities}
    page_numbers.update(int(getattr(zone, "page_number", 0) or 0) for zone in zones)
    page_numbers.discard(0)
    if not page_numbers:
        return VisionPlan(False, "no_page_evidence_for_recall", tuple(fields))
    last_page = max(page_numbers)
    first_count = max(1, int(getattr(config, "vision_recall_first_pages", 5)))
    last_count = max(1, int(getattr(config, "vision_recall_last_pages", 3)))
    selected: dict[int, list[str]] = {}
    for page in range(1, min(last_page, first_count) + 1):
        selected.setdefault(page, []).append("recall_first_pages")
    for page in range(max(1, last_page - last_count + 1), last_page + 1):
        selected.setdefault(page, []).append("recall_last_pages")
    wanted_zones = {
        "signature_page", "signature_recognition", "property_recital", "object_clause",
        "cadastral_record", "land_registry_certificate", "party_identification",
    }
    for zone in zones:
        page = getattr(zone, "page_number", None)
        if page and getattr(zone, "zone_type", "") in wanted_zones:
            selected.setdefault(int(page), []).append(f"recall_{getattr(zone, 'zone_type')}")
    limit = max(first_count + last_count, int(getattr(config, "vision_max_pages_per_document", 8)))
    plans = tuple(
        VisionPagePlan(page, tuple(reasons))
        for page, reasons in sorted(selected.items())[:limit]
    )
    return VisionPlan(bool(plans), "targeted_vision_recall", tuple(fields), plans)


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
    conflict_text = " ".join(str(value or "") for value in (result.evidence_conflicts, result.cadastral_conflicts, result.review_reason)).lower()
    if "conflict" in conflict_text or "ownership" in conflict_text:
        uncertain.extend(["lessor", "lessee", "property_article", "property_section"])
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
