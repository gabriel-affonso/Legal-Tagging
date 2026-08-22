from __future__ import annotations

from dataclasses import asdict
import json
import re
from typing import Any
from urllib import error, request

from .models import ExtractionResult


SYSTEM_PROMPT = """You extract structured metadata from Portuguese and English real-estate and business PDFs.
Return only valid JSON. If a field is not present, use an empty string.
Dates should use ISO format YYYY-MM-DD when possible. Monetary values should keep only the number.
Do not invent facts. Prefer low confidence when the text is incomplete, OCR-like, or ambiguous."""


CLASSIFICATION_PROMPT_TEMPLATE = """Classify the PDF text below.

Use exactly one of these document_category values:
- lease_contract: contrato de arrendamento, lease, rental agreement
- payment_proof: comprovativo/comprovante de pagamento, recibo, transferencia, bank transfer proof
- invoice_or_receipt: fatura, recibo, invoice, receipt not clearly a payment proof
- identification: identity, company registration, tax id, passport, citizen card
- tax_document: tax, fiscal, declaration, certidao
- correspondence: letter, email export, notification
- other: anything else

Return this exact JSON object:
{{
  "document_category": "",
  "document_type": "",
  "document_subtype": "",
  "document_date": "",
  "summary": "",
  "language": "",
  "confidence": "low|medium|high",
  "extraction_notes": ""
}}

PDF text:
{pdf_text}
"""


EXTRACTION_PROMPTS = {
    "lease_contract": """Extract lease/rental contract metadata from the PDF text.

Required focus:
- signed_date
- contract_type
- lessor/landlord/owner
- lessee/tenant
- property_address
- property_name: the building, unit, estate, project, apartment, room, shop, or asset name/label if visible
- property_number: the unit, fraction, lot, apartment number, shop number, internal property id, or asset number if visible
- property_display_name: combine property_name and property_number in one readable value
- contract_start_date
- contract_end_date
- rent_payment_day
- monthly_rent
- currency
""",
    "payment_proof": """Extract payment proof metadata from the PDF text.

Required focus:
- payment_date
- payer
- payee
- payment_amount
- currency
- payment_method
- payment_reference
- payment_description
- property_name and property_number if the paid rent/property is identified
""",
    "invoice_or_receipt": """Extract invoice or receipt metadata from the PDF text.

Required focus:
- document_date
- payer or customer when visible
- payee or vendor when visible
- property_name and property_number when visible
- payment_amount or invoice total
- currency
- payment_reference
- payment_description
""",
    "identification": """Extract identification metadata from the PDF text.

Required focus:
- document_type
- document_subtype
- document_date
- parties or person/company names when visible
- summary
""",
    "tax_document": """Extract tax document metadata from the PDF text.

Required focus:
- document_type
- document_subtype
- document_date
- person/company names when visible
- payment_amount or tax amount when visible
- currency
- summary
""",
    "correspondence": """Extract correspondence metadata from the PDF text.

Required focus:
- document_type
- document_date
- sender as payer if useful
- recipient as payee if useful
- summary
""",
    "other": """Extract generic document register metadata from the PDF text.

Required focus:
- document_type
- document_subtype
- document_date
- parties, amounts, references, and concise summary when visible
- property_name and property_number when visible
""",
}


EXTRACTION_PROMPT_TEMPLATE = """Document category selected in step 1: {document_category}

{category_instructions}

Return this exact JSON object:
{{
  "document_category": "",
  "document_type": "",
  "document_subtype": "",
  "document_date": "",
  "summary": "",
  "language": "",
  "signed_date": "",
  "contract_type": "",
  "lessor": "",
  "lessee": "",
  "property_address": "",
  "property_name": "",
  "property_number": "",
  "property_display_name": "",
  "contract_start_date": "",
  "contract_end_date": "",
  "rent_payment_day": "",
  "monthly_rent": "",
  "currency": "",
  "payment_date": "",
  "payer": "",
  "payee": "",
  "payment_amount": "",
  "payment_method": "",
  "payment_reference": "",
  "payment_description": "",
  "confidence": "low|medium|high",
  "extraction_notes": ""
}}

PDF text:
{pdf_text}
"""


def extract_with_ollama(base_url: str, model: str, pdf_text: str) -> ExtractionResult:
    if len(pdf_text.strip()) < 200:
        return ExtractionResult(
            document_type="",
            confidence="low",
            extraction_notes="Pouco texto extraido do PDF. Pode ser necessario OCR ou revisao manual.",
        )

    classification = classify_with_ollama(base_url, model, pdf_text)
    category = _normalize_category(classification.get("document_category", "other"))
    extraction = extract_metadata_with_ollama(base_url, model, pdf_text, category)
    merged = {**classification, **extraction, "document_category": category}
    return _result_from_dict(
        {
            **merged,
            "raw_json": {
                "classification": classification,
                "extraction": extraction,
            },
        }
    )


def classify_with_ollama(base_url: str, model: str, pdf_text: str) -> dict[str, Any]:
    return _chat_json(
        base_url,
        model,
        CLASSIFICATION_PROMPT_TEMPLATE.format(pdf_text=_classification_text(pdf_text)),
    )


def extract_metadata_with_ollama(
    base_url: str,
    model: str,
    pdf_text: str,
    document_category: str,
) -> dict[str, Any]:
    instructions = EXTRACTION_PROMPTS.get(document_category, EXTRACTION_PROMPTS["other"])
    return _chat_json(
        base_url,
        model,
        EXTRACTION_PROMPT_TEMPLATE.format(
            document_category=document_category,
            category_instructions=instructions,
            pdf_text=pdf_text,
        ),
    )


def _chat_json(base_url: str, model: str, user_prompt: str) -> dict[str, Any]:
    payload = {
        "model": model,
        "format": "json",
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "options": {"temperature": 0},
    }

    body = json.dumps(payload).encode("utf-8")
    http_request = request.Request(
        f"{base_url}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(http_request, timeout=180) as response:
            raw_response = json.loads(response.read().decode("utf-8"))
    except error.URLError as exc:
        raise RuntimeError(f"Could not connect to Ollama at {base_url}: {exc}") from exc

    content = raw_response.get("message", {}).get("content", "")
    return _parse_json_object(content)


def _parse_json_object(content: str) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            raise RuntimeError(f"Ollama did not return JSON: {content[:500]}")
        value = json.loads(match.group(0))

    if not isinstance(value, dict):
        raise RuntimeError("Ollama JSON response was not an object.")
    return value


def _result_from_dict(raw: dict[str, Any]) -> ExtractionResult:
    allowed = set(asdict(ExtractionResult()).keys()) - {"raw_json"}
    cleaned = {key: _clean(raw.get(key, "")) for key in allowed}
    raw_json = raw.get("raw_json", raw)
    return ExtractionResult(**cleaned, raw_json=raw_json if isinstance(raw_json, dict) else raw)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _classification_text(pdf_text: str) -> str:
    return pdf_text[:8000]


def _normalize_category(category: str) -> str:
    value = category.strip().lower()
    allowed = set(EXTRACTION_PROMPTS)
    return value if value in allowed else "other"
