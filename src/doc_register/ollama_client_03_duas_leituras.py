from __future__ import annotations

from dataclasses import asdict
import json
import re
from typing import Any
from urllib import error, request

from .detectors import DeterministicSignals
from .models import ExtractionResult
from .schemas import CATEGORY_EXTRACTION_FIELDS, OFFICIAL_CATEGORIES


class OllamaTimeoutError(RuntimeError):
    pass


SYSTEM_PROMPT = """You extract structured metadata from confidential Portuguese and English PDFs.
You run locally through Ollama. Never suggest or require external/cloud processing.
Return only valid JSON, without markdown. If a field is not present, use an empty string.
Do not invent facts. Use low confidence when text is incomplete, OCR-like, or ambiguous.
Never use IBAN, NIB, BIC/SWIFT, account numbers, or banking references as payment_amount.
Never use a bank name or bank account holder as property_name.
property_address must be the property location, not the owner's fiscal address."""


CLASSIFICATION_PROMPT_TEMPLATE = """Classify the document using only the official categories.
This is the first evaluation. Use only the initial document text provided below: the first two text pages, or the first 500 words when page boundaries are not available.

File name:
{file_name}

Deterministic signals:
{deterministic_json}

Official categories:
{category_list}

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

Initial text for classification:
{classification_text}
"""


EXTRACTION_PROMPT_TEMPLATE = """Extract metadata for category "{document_category}".
This is the second evaluation. Follow the decision tree and category-specific prompt below.

File name:
{file_name}

First evaluation result:
{classification_json}

Deterministic signals:
{deterministic_json}

Only return these fields:
{schema_json}

Category-specific guardrails:
{guardrails}

Highlighted relevant excerpts:
{highlighted_text}
"""


CATEGORY_PROMPT_TREE = {
    "lease_contract": (
        "Large type: lease/contractual document.\n"
        "Possibilities: lease agreement, lease amendment, renewal, termination, addendum, rent update.\n"
        "Extract lessor, lessee, property, dates, rent day, monthly rent and currency only when explicit.\n"
        "Do not fill bank fields unless bank data is clearly part of payment instructions."
    ),
    "property_document": (
        "Large type: property registry/tax/property evidence.\n"
        "Possibilities: caderneta predial, certidao predial, CRP, registo predial, matriz, averbamento.\n"
        "Prioritize property article, section, parish, municipality, district, location/address and owner fields.\n"
        "Do not fill lessor or lessee unless a real lease relationship appears."
    ),
    "bank_details": (
        "Large type: bank/account information.\n"
        "Possibilities: IBAN certificate, account ownership declaration, bank details form, NIB/BIC/SWIFT information.\n"
        "Extract IBAN, NIB, BIC/SWIFT, bank name, account holder and account number.\n"
        "Do not classify as payment proof unless there is a clear completed payment with date, payer, payee and amount."
    ),
    "payment_proof": (
        "Large type: completed payment evidence.\n"
        "Possibilities: bank transfer proof, payment receipt, rent payment proof, settlement confirmation.\n"
        "payment_amount requires monetary context such as EUR, €, amount, montante, valor, total or clear payment wording.\n"
        "Do not confuse IBAN, NIB, BIC/SWIFT, account number or reference with payment_amount."
    ),
    "invoice_or_receipt": (
        "Large type: billing or receipt document.\n"
        "Possibilities: invoice, receipt, rent receipt, services invoice, fee note.\n"
        "Extract invoice/receipt date, parties, total and description.\n"
        "Only use payment_proof semantics when this is clearly a bank transfer/payment proof."
    ),
    "identity_document": (
        "Large type: identity or company identification.\n"
        "Possibilities: citizen card, passport, company certificate, commercial registry, tax ID evidence.\n"
        "Extract identity or company identification metadata only when explicit."
    ),
    "tax_document": (
        "Large type: fiscal/tax document.\n"
        "Possibilities: tax certificate, fiscal declaration, settlement note, tax proof.\n"
        "Extract fiscal metadata, taxpayer names/IDs and amounts only when explicit."
    ),
    "correspondence": (
        "Large type: communication/correspondence.\n"
        "Possibilities: letter, notice, email export, notification, legal communication.\n"
        "Extract sender/recipient-like parties only if useful, plus concise summary."
    ),
    "other": (
        "Large type: unknown or mixed document.\n"
        "Possibilities: anything not safely covered by the official categories.\n"
        "Extract only conservative metadata. Empty fields are better than guesses."
    ),
}


def extract_with_ollama(
    base_url: str,
    model: str,
    *,
    file_name: str,
    classification_text: str,
    highlighted_text: str,
    signals: DeterministicSignals,
    timeout_seconds: int = 600,
) -> ExtractionResult:
    if len(classification_text.strip()) < 200 and len(highlighted_text.strip()) < 200:
        return ExtractionResult(
            document_category=signals.suggested_category,
            document_type="",
            confidence="low",
            extraction_notes="Pouco texto extraido do PDF. Pode ser necessario OCR ou revisao manual.",
            deterministic_json=signals.to_dict(),
            llm_json={},
            raw_json={"deterministic": signals.to_dict(), "llm": {}},
        )

    try:
        classification = classify_with_ollama(base_url, model, file_name, classification_text, signals, timeout_seconds)
    except OllamaTimeoutError as exc:
        return _timeout_fallback_result(signals, "classification", exc)

    category = _normalize_category(classification.get("document_category", signals.suggested_category))
    try:
        extraction = extract_metadata_with_ollama(
            base_url,
            model,
            file_name,
            highlighted_text,
            signals,
            category,
            classification,
            timeout_seconds,
        )
    except OllamaTimeoutError as exc:
        extraction = {
            "confidence": "low",
            "extraction_notes": f"Timeout na segunda avaliacao do Ollama local: {exc}",
        }
    merged = {**classification, **extraction, "document_category": category}
    llm_json = {"classification": classification, "extraction": extraction}
    return _result_from_dict(
        {
            **merged,
            "deterministic_json": signals.to_dict(),
            "llm_json": llm_json,
            "raw_json": {
                "deterministic": signals.to_dict(),
                "llm": llm_json,
            },
        }
    )


def classify_with_ollama(
    base_url: str,
    model: str,
    file_name: str,
    classification_text: str,
    signals: DeterministicSignals,
    timeout_seconds: int,
) -> dict[str, Any]:
    return _chat_json(
        base_url,
        model,
        CLASSIFICATION_PROMPT_TEMPLATE.format(
            file_name=file_name,
            deterministic_json=json.dumps(signals.to_dict(), ensure_ascii=False, indent=2),
            category_list=_category_list(),
            classification_text=classification_text,
        ),
        timeout_seconds=timeout_seconds,
    )


def extract_metadata_with_ollama(
    base_url: str,
    model: str,
    file_name: str,
    highlighted_text: str,
    signals: DeterministicSignals,
    document_category: str,
    classification: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    schema = _blank_schema(document_category)
    return _chat_json(
        base_url,
        model,
        EXTRACTION_PROMPT_TEMPLATE.format(
            document_category=document_category,
            file_name=file_name,
            classification_json=json.dumps(classification, ensure_ascii=False, indent=2),
            deterministic_json=json.dumps(signals.to_dict(), ensure_ascii=False, indent=2),
            schema_json=json.dumps(schema, ensure_ascii=False, indent=2),
            guardrails=CATEGORY_PROMPT_TREE.get(document_category, CATEGORY_PROMPT_TREE["other"]),
            highlighted_text=highlighted_text,
        ),
        timeout_seconds=timeout_seconds,
    )


def _chat_json(base_url: str, model: str, user_prompt: str, *, timeout_seconds: int) -> dict[str, Any]:
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
        with request.urlopen(http_request, timeout=timeout_seconds) as response:
            raw_response = json.loads(response.read().decode("utf-8"))
    except TimeoutError as exc:
        raise OllamaTimeoutError(f"timeout after {timeout_seconds}s") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Could not connect to local Ollama at {base_url}: {exc}") from exc

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
    allowed = set(asdict(ExtractionResult()).keys()) - {"raw_json", "deterministic_json", "llm_json"}
    cleaned = {key: _clean(raw.get(key, "")) for key in allowed}
    deterministic_json = raw.get("deterministic_json", {})
    llm_json = raw.get("llm_json", {})
    raw_json = raw.get("raw_json", raw)
    return ExtractionResult(
        **cleaned,
        deterministic_json=deterministic_json if isinstance(deterministic_json, dict) else {},
        llm_json=llm_json if isinstance(llm_json, dict) else {},
        raw_json=raw_json if isinstance(raw_json, dict) else raw,
    )


def _timeout_fallback_result(signals: DeterministicSignals, step: str, exc: Exception) -> ExtractionResult:
    deterministic_json = signals.to_dict()
    llm_json = {"error": {"step": step, "message": str(exc)}}
    return ExtractionResult(
        document_category=signals.suggested_category,
        document_type="",
        confidence="low",
        extraction_notes=f"Timeout na {step} do Ollama local; resultado baseado apenas em regras deterministicas.",
        deterministic_json=deterministic_json,
        llm_json=llm_json,
        raw_json={"deterministic": deterministic_json, "llm": llm_json},
    )


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _normalize_category(category: str) -> str:
    value = category.strip().lower()
    if value == "identification":
        value = "identity_document"
    return value if value in OFFICIAL_CATEGORIES else "other"


def _blank_schema(document_category: str) -> dict[str, str]:
    return {field_name: "" for field_name in CATEGORY_EXTRACTION_FIELDS.get(document_category, CATEGORY_EXTRACTION_FIELDS["other"])}


def _category_list() -> str:
    return "\n".join(f"- {category}: {description}" for category, description in OFFICIAL_CATEGORIES.items())
