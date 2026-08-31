from __future__ import annotations

import json


SUPPORTED_FIELDS = (
    "lessor",
    "lessee",
    "lessee_tax_id",
    "signed_date",
    "property_article",
    "property_section",
    "monthly_rent",
)


def build_prompt(*, page_number: int, fields: tuple[str, ...], file_name: str) -> str:
    requested = [field for field in fields if field in SUPPORTED_FIELDS]
    return (
        "Read this single page of a confidential Portuguese real-estate contract. "
        "This is a visual recovery pass after OCR was unreliable. Read only facts "
        "that are visibly present on this page. Do not guess, complete partial words, "
        "or use outside knowledge. A signature can establish that a signature is present, "
        "but never proves a person's identity. For handwriting, set content_type to "
        "handwritten and leave a candidate empty when any important character is unclear. "
        "For every returned candidate, evidence must be an exact, short visible excerpt "
        "containing both the value and its contractual context. Return JSON only.\n\n"
        f"File name (weak hint only): {file_name}\n"
        f"Physical page number: {page_number}\n"
        f"Requested fields: {json.dumps(requested, ensure_ascii=False)}\n\n"
        "Allowed fields: lessor, lessee, lessee_tax_id, signed_date, property_article, "
        "property_section, monthly_rent. The lessor is the senhorio/locador, and the "
        "lessee is the arrendatario/locatario. signed_date needs an explicit signature, "
        "outorgado or celebrado context. monthly_rent must explicitly be monthly."
    )


def response_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "page_legibility": {"type": "string", "enum": ["low", "medium", "high"]},
            "handwriting_present": {"type": "boolean"},
            "contract_evidence": {
                "type": "object",
                "properties": {
                    "is_contract": {"type": "boolean"},
                    "confidence": {"type": "number"},
                },
                "required": ["is_contract", "confidence"],
            },
            "field_candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field_name": {"type": "string", "enum": list(SUPPORTED_FIELDS)},
                        "proposed_value": {"type": "string"},
                        "evidence": {"type": "string"},
                        "confidence": {"type": "number"},
                        "content_type": {"type": "string", "enum": ["printed", "handwritten", "stamp", "mixed"]},
                        "block_id": {"type": "string"},
                        "uncertain_tokens": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["field_name", "proposed_value", "evidence", "confidence", "content_type"],
                },
            },
        },
        "required": ["page_legibility", "handwriting_present", "field_candidates"],
    }
