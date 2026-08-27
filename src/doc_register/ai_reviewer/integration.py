from __future__ import annotations

import time
from ..config import AppConfig
from ..models import ExtractionResult
from ..validators.validation_rules import run_all_validations
from .evidence_selector import select_evidence
from .models import AIReviewReport, AIReviewRequest
from .proposal_validator import AcceptancePolicy, validate_proposal
from .review_policy import should_review
from .reviewer import request_ai_review


def review_if_needed(
    result: ExtractionResult,
    *,
    config: AppConfig,
    file_name: str,
    document_text: str,
) -> ExtractionResult:
    report = AIReviewReport(model=config.ollama_model)
    started_at = time.monotonic()

    if not getattr(config, "ai_review_enabled", True):
        report.status = "NOT_REQUIRED"
        report.reason = "ai_review_disabled"
        _attach_report(result, report)
        return result

    plan = should_review(result, document_text=document_text)
    report.status = plan.status
    report.reviewed_fields = list(plan.fields)
    report.remaining_issues = list(plan.issues)
    report.reason = plan.reason
    if not plan.eligible:
        report.human_review_required = plan.status == "NOT_ELIGIBLE"
        report.duration_seconds = time.monotonic() - started_at
        _attach_report(result, report)
        return result

    report.attempted = True
    evidence_text = select_evidence(
        document_text,
        plan.fields,
        max_chars=int(getattr(config, "ai_review_max_evidence_chars", 6500)),
    )
    request = AIReviewRequest(
        result=result,
        file_name=file_name,
        fields=plan.fields,
        issues=plan.issues,
        evidence_text=evidence_text,
    )

    try:
        proposals = request_ai_review(
            base_url=config.ollama_url,
            model=config.ollama_model,
            request=request,
            timeout_seconds=int(getattr(config, "ai_review_timeout_seconds", 180)),
        )
    except Exception as exc:
        report.status = "FAILED"
        report.reason = f"ai_review_failed: {str(exc)[:300]}"
        report.human_review_required = True
        report.duration_seconds = time.monotonic() - started_at
        _attach_report(result, report)
        return result

    policy = AcceptancePolicy(
        auto_accept_confidence=float(getattr(config, "ai_review_auto_accept_confidence", 0.90)),
        human_confidence=float(getattr(config, "ai_review_human_confidence", 0.70)),
    )
    decisions = [
        validate_proposal(
            proposal,
            result,
            document_text=document_text,
            evidence_text=evidence_text,
            policy=policy,
        )
        for proposal in proposals
    ]
    report.field_decisions = decisions
    for decision in decisions:
        if decision.decision == "AUTO_ACCEPTED":
            setattr(result, decision.field_name, decision.proposed_value)
            if decision.field_name not in report.accepted_fields:
                report.accepted_fields.append(decision.field_name)
        elif decision.decision == "AI_PROPOSED_HUMAN_REQUIRED":
            if decision.field_name not in report.human_required_fields:
                report.human_required_fields.append(decision.field_name)
            report.human_review_required = True
        else:
            if decision.field_name not in report.rejected_fields:
                report.rejected_fields.append(decision.field_name)

    report.remaining_issues = run_all_validations(result)
    report.status = _status_for(report, plan.fields)
    if report.remaining_issues:
        report.human_review_required = True
    report.duration_seconds = time.monotonic() - started_at
    _attach_report(result, report)
    return result


def _status_for(report: AIReviewReport, requested_fields: list[str]) -> str:
    if not report.attempted:
        return report.status
    if report.accepted_fields and len(report.accepted_fields) == len(requested_fields):
        return "RESOLVED"
    if report.accepted_fields:
        return "PARTIALLY_RESOLVED"
    return "COMPLETED"


def _attach_report(result: ExtractionResult, report: AIReviewReport) -> None:
    result.ai_review_status = report.status
    result.ai_reviewed_fields = "; ".join(report.reviewed_fields)
    result.ai_accepted_fields = "; ".join(report.accepted_fields)
    result.ai_rejected_fields = "; ".join(
        [*report.rejected_fields, *report.human_required_fields]
    )
    result.ai_review_model = report.model
    result.ai_review_duration_seconds = f"{report.duration_seconds:.3f}" if report.duration_seconds else ""
    result.ai_review_reason = report.reason
    result.human_review_required = "yes" if report.human_review_required else "no"
    if report.human_required_fields:
        human_fields = ", ".join(report.human_required_fields)
        result.ai_review_reason = (
            f"{report.reason}; human_validation_required_for: {human_fields}"
            if report.reason
            else f"human_validation_required_for: {human_fields}"
        )

    if report.field_decisions:
        confidences = [decision.confidence for decision in report.field_decisions]
        result.ai_review_confidence = f"{max(confidences):.2f}"
    else:
        result.ai_review_confidence = ""

    raw = result.raw_json if isinstance(result.raw_json, dict) else {}
    result.raw_json = dict(raw)
    result.raw_json["ai_review"] = report.to_dict()

    if report.accepted_fields:
        note = "Step 2 AI review accepted: " + ", ".join(report.accepted_fields)
        result.extraction_notes = " | ".join(
            part for part in (result.extraction_notes, note) if part
        )
