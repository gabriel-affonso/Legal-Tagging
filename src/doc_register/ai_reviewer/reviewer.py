from __future__ import annotations

from ..ollama_client import _chat_json
from .models import AIFieldProposal, AIReviewRequest
from .prompt_builder import build_review_prompt, review_output_schema
from .response_parser import parse_review_response


def request_ai_review(
    *,
    base_url: str,
    model: str,
    request: AIReviewRequest,
    timeout_seconds: int,
) -> list[AIFieldProposal]:
    raw = _chat_json(
        base_url.rstrip("/"),
        model,
        build_review_prompt(request),
        timeout_seconds=timeout_seconds,
        output_schema=review_output_schema(),
        num_predict=500,
    )
    return parse_review_response(raw, request.fields)
