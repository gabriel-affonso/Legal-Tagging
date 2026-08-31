from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from ..models import ExtractionResult
from ..validators.contract_final_resolution import inject_vision_candidates
from ..validators.name_quality import validate_name_field
from ..validators.signature_date import normalize_date
from ..validators.validation_rules import normalize_text
from .models import VisionFieldCandidate, VisionRecoveryReport
from .ollama_vision_client import VisionRecoveryError, request_visual_candidates, unload_model
from .page_selector import build_vision_plan
from .pdf_renderer import render_pdf_page


def recover_contract_with_vision(
    result: ExtractionResult,
    *,
    config: Any,
    file_name: str,
    pdf_path: Path,
    signals: Any,
    final_resolution_report: Any,
) -> tuple[ExtractionResult, bool]:
    """Run the opt-in visual fallback and return whether candidates were injected.

    All errors remain local to the document.  The raw image is never persisted
    or attached to the report; only its digest and dimensions are audit data.
    """
    started_at = time.monotonic()
    report = VisionRecoveryReport(model=str(getattr(config, "vision_model", "")))
    plan = build_vision_plan(
        result,
        signals=signals,
        final_resolution_report=final_resolution_report,
        config=config,
    )
    report.trigger_reason = plan.reason
    report.reviewed_fields = list(plan.fields)
    report.selected_pages = [item.page_number for item in plan.pages]
    if not plan.eligible:
        report.status = "NOT_REQUIRED" if plan.reason != "vision_disabled" else "DISABLED"
        report.duration_seconds = time.monotonic() - started_at
        _attach_report(result, report)
        return result, False

    report.attempted = True
    text_model = str(getattr(config, "ollama_model", "") or "")
    vision_model = str(getattr(config, "vision_model", "") or "")
    if text_model and text_model != vision_model:
        unload_model(base_url=str(getattr(config, "ollama_url", "http://localhost:11434")), model=text_model)
    for page_plan in plan.pages:
        try:
            rendered = render_pdf_page(
                pdf_path,
                page_plan.page_number,
                dpi=int(getattr(config, "vision_render_dpi", 160)),
                max_pixels=int(getattr(config, "vision_max_image_pixels", 3_000_000)),
            )
            candidates, request_audit = request_visual_candidates(
                base_url=str(getattr(config, "ollama_url", "http://localhost:11434")),
                model=str(getattr(config, "vision_model", "qwen3-vl:4b-instruct")),
                file_name=file_name,
                rendered_page=rendered,
                fields=plan.fields,
                timeout_seconds=int(getattr(config, "vision_timeout_seconds", 240)),
                keep_alive=str(getattr(config, "vision_keep_alive", "0")),
            )
            request_audit["trigger_reasons"] = list(page_plan.reasons)
            report.page_requests.append(request_audit)
            report.candidates.extend(candidates)
        except (VisionRecoveryError, RuntimeError, ValueError) as exc:
            report.failures.append(f"page_{page_plan.page_number}: {str(exc)[:300]}")

    injected: list[VisionFieldCandidate] = []
    for candidate in report.candidates:
        reason = _validate_candidate(candidate, plan.fields)
        if reason:
            report.rejected.append({"field": candidate.field_name, "reason": reason})
            continue
        if candidate.content_type != "printed":
            _mark_human_required(report, candidate, "visual_handwriting_or_nonprinted")
            continue
        if candidate.uncertain_tokens:
            _mark_human_required(report, candidate, "visual_uncertain_tokens")
            continue
        if not bool(getattr(config, "vision_apply_proposals", False)):
            report.rejected.append({"field": candidate.field_name, "reason": "shadow_mode"})
            continue
        if not bool(getattr(config, "vision_auto_accept_enabled", False)):
            _mark_human_required(report, candidate, "visual_auto_accept_disabled")
            continue
        if candidate.confidence < float(getattr(config, "vision_auto_accept_confidence", 0.95)):
            _mark_human_required(report, candidate, "visual_confidence_below_auto_accept_threshold")
            continue
        injected.append(candidate)

    if injected:
        report.accepted_fields = inject_vision_candidates(final_resolution_report, injected)
    if report.failures and not report.candidates:
        report.status = "FAILED"
    elif report.accepted_fields:
        report.status = "APPLIED"
    elif report.human_required_fields:
        report.status = "HUMAN_REVIEW_REQUIRED"
    elif report.candidates:
        report.status = "SHADOW_COMPLETED" if not bool(getattr(config, "vision_apply_proposals", False)) else "COMPLETED"
    else:
        report.status = "COMPLETED_NO_CANDIDATES"
    report.duration_seconds = time.monotonic() - started_at
    _attach_report(result, report)
    return result, bool(report.accepted_fields)


def _validate_candidate(candidate: VisionFieldCandidate, requested_fields: tuple[str, ...]) -> str:
    field = candidate.field_name
    value = candidate.proposed_value.strip()
    evidence = candidate.evidence.strip()
    if field not in requested_fields:
        return "field_not_requested"
    if not value:
        return "empty_value"
    if not evidence:
        return "missing_visual_evidence"
    if candidate.confidence <= 0:
        return "invalid_confidence"
    if field in {"lessor", "lessee"}:
        if validate_name_field(field, value):
            return "invalid_party_name"
        if normalize_text(value) not in normalize_text(evidence):
            return "party_value_not_in_visual_evidence"
        marker = r"senhor|locador|outorgante|arrendat|locat"
        if not re.search(marker, evidence, re.I):
            return "party_role_context_missing"
    elif field == "lessee_tax_id":
        normalized = re.sub(r"\D", "", value)
        if not _valid_tax_id(normalized):
            return "invalid_tax_id"
        if normalized not in re.sub(r"\D", "", evidence):
            return "tax_id_not_in_visual_evidence"
    elif field == "signed_date":
        normalized_date = normalize_date(value)
        if not normalized_date:
            return "invalid_signed_date"
        if not re.search(r"assin|outorg|celebr", evidence, re.I):
            return "signature_context_missing"
        evidence_dates = [
            normalize_date(match.group(0))
            for match in re.finditer(r"\b(?:\d{1,2}[/-]\d{1,2}[/-](?:19|20)\d{2}|(?:19|20)\d{2}-\d{2}-\d{2})\b", evidence)
        ]
        if normalized_date not in evidence_dates:
            return "signed_date_not_in_visual_evidence"
    elif field == "property_article":
        if not re.fullmatch(r"\d{1,8}", value):
            return "invalid_property_article"
        if not re.search(r"artigo|matriz|predial", evidence, re.I):
            return "property_article_context_missing"
        if value not in re.sub(r"\D", "", evidence):
            return "property_article_not_in_visual_evidence"
    elif field == "property_section":
        if not re.fullmatch(r"[A-Za-z]", value):
            return "invalid_property_section"
        if not re.search(r"sec[cç][aã]o|matriz|predial", evidence, re.I):
            return "property_section_context_missing"
        if not re.search(rf"(?:sec[cç][aã]o|matriz)\D{{0,30}}\b{re.escape(value)}\b", evidence, re.I):
            return "property_section_not_in_visual_evidence"
    elif field == "monthly_rent":
        if not re.search(r"\d", value) or not re.search(r"renda|mensal", evidence, re.I):
            return "monthly_rent_context_missing"
        value_digits = re.sub(r"\D", "", value)
        if value_digits and value_digits not in re.sub(r"\D", "", evidence):
            return "monthly_rent_not_in_visual_evidence"
    else:
        return "unsupported_field"
    return ""


def _mark_human_required(report: VisionRecoveryReport, candidate: VisionFieldCandidate, reason: str) -> None:
    if candidate.field_name not in report.human_required_fields:
        report.human_required_fields.append(candidate.field_name)
    report.rejected.append({"field": candidate.field_name, "reason": reason})


def _valid_tax_id(value: str) -> bool:
    if not re.fullmatch(r"\d{9}", value):
        return False
    total = sum(int(value[index]) * (9 - index) for index in range(8))
    digit = 11 - (total % 11)
    if digit >= 10:
        digit = 0
    return digit == int(value[-1])


def _attach_report(result: ExtractionResult, report: VisionRecoveryReport) -> None:
    result.vision_status = report.status
    result.vision_pages = "; ".join(str(page) for page in report.selected_pages)
    result.vision_model = report.model
    result.vision_trigger_reason = report.trigger_reason
    result.vision_reviewed_fields = "; ".join(report.reviewed_fields)
    result.vision_accepted_fields = "; ".join(report.accepted_fields)
    result.vision_human_required_fields = "; ".join(report.human_required_fields)
    result.vision_duration_seconds = f"{report.duration_seconds:.3f}" if report.duration_seconds else ""
    if report.human_required_fields:
        result.human_review_required = "yes"
        marker = "vision_human_validation_required"
        result.review_reason = "; ".join(part for part in (result.review_reason, marker) if part)
    raw = result.raw_json if isinstance(result.raw_json, dict) else {}
    result.raw_json = dict(raw)
    result.raw_json["vision_recovery"] = report.to_dict()
