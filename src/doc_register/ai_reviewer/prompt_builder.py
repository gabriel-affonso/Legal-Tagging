from __future__ import annotations

import json

from .models import AIReviewRequest


def build_review_prompt(request: AIReviewRequest) -> str:
    current_values = {
        field_name: getattr(request.result, field_name, "")
        for field_name in request.fields
    }
    return (
        "You are the Step 2 AI Reviewer for a local confidential document "
        "pipeline. Propose values only for the requested fields. Use only the "
        "evidence excerpts below. Do not summarize the document. Do not fill a "
        "field without explicit evidence. Return JSON only.\n\n"
        "Requested fields:\n"
        f"{json.dumps(request.fields, ensure_ascii=False)}\n\n"
        "Current validation issues:\n"
        f"{json.dumps(request.issues, ensure_ascii=False)}\n\n"
        "Current values:\n"
        f"{json.dumps(current_values, ensure_ascii=False)}\n\n"
        "Return exactly this JSON object shape:\n"
        "{\n"
        '  "proposals": [\n'
        "    {\n"
        '      "field_name": "one requested field",\n'
        '      "proposed_value": "string, empty when not found",\n'
        '      "evidence": "short exact excerpt supporting the value",\n'
        '      "confidence": 0.0\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Field rules:\n"
        "- lessor, lessee and owner_name must be names only; never generic labels, IDs, addresses or clauses.\n"
        "- For lease parties, prefer explicit contract-structure zones: primeiro/segundo outorgante, senhorio/arrendatario, de um lado/do outro lado.\n"
        "- signed_date hierarchy is signature date first, contract execution date second, document date only when explicit. Never use validity, registry, licence, issue or matrix dates.\n"
        "- property_article must appear in a property block near artigo, matriz, inscrito, predio or caderneta wording.\n"
        "- property_section must appear in that same property block near seccao/secção wording and be a short section code.\n"
        "- monthly_rent requires explicit monthly wording such as renda mensal, por mes or mensais.\n\n"
        "File name is a hint only, not evidence:\n"
        f"{request.file_name}\n\n"
        "Evidence excerpts:\n"
        f"{request.evidence_text}"
    )


def review_output_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "proposals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field_name": {"type": "string"},
                        "proposed_value": {"type": "string"},
                        "evidence": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["field_name", "proposed_value", "evidence", "confidence"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["proposals"],
        "additionalProperties": False,
    }
