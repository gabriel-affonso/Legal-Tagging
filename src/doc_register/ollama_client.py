from __future__ import annotations

from dataclasses import asdict
import json
import logging
import re
from typing import Any
from urllib import error, request

from .contract_types import contract_type_options_for_prompt
from .detectors import DeterministicSignals
from .models import ExtractionResult
from .schemas import CATEGORY_EXTRACTION_FIELDS, OFFICIAL_CATEGORIES

logger = logging.getLogger(__name__)


class OllamaTimeoutError(RuntimeError):
    pass


class OllamaConnectionError(RuntimeError):
    """Raised when the local Ollama endpoint is unavailable or disconnects."""

    pass


SYSTEM_PROMPT = """You extract structured metadata from confidential Portuguese and English PDFs.
You run locally through Ollama. Never suggest or require external/cloud processing.
Return only valid JSON, without markdown. If a field is not present, use an empty string.
Do not invent, infer, translate, geographically normalize, or complete facts from external knowledge.
Use low confidence when text is incomplete, OCR-like, ambiguous, or internally inconsistent.
Never use IBAN, NIB, BIC/SWIFT, account numbers, or banking references as payment_amount.
Never use a bank name or bank account holder as property_name.
property_address must be the property location, not the owner's fiscal address.
When an output schema is supplied, obey it exactly: use every key, add no keys, rename no keys,
and keep the JSON object flat unless the schema explicitly defines nesting."""


CLASSIFICATION_PROMPT_TEMPLATE = """Classify the document using only the official categories.
This is the first evaluation. Use only the initial document text provided below: the first two text pages, or the first 500 words when page boundaries are not available.

File name:
{file_name}

Deterministic signals:
{deterministic_json}

Official categories:
{category_list}

Contract subtypes for document_category="lease_contract":
{contract_type_list}

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
This is the second evaluation. Return exactly one FLAT JSON object matching the required schema.

STRICT OUTPUT RULES:
1. Use exactly the keys shown in the required schema.
2. Return every key, even when its value is an empty string.
3. Do not add, remove, rename, pluralize or nest fields.
4. Do not return a "fields" object.
5. Do not return "property_details", "contract_duration", "deterministic_signals" or "file_category".
6. Every value must be a string.
7. If a value is not explicitly supported by document text, return an empty string.
8. Do not guess or use external geographical knowledge.
9. The file name is only a classification hint; it is not evidence for extracted values.
10. Return JSON only, without markdown or explanatory text.

File name:
{file_name}

First evaluation result (context only; do not repeat its fields):
{classification_json}

The deterministic signals below are unverified candidates and may contain OCR or regex errors.
Never copy them automatically. Prefer explicit labelled evidence in the document excerpts.
If a candidate conflicts with document text, use the document text:
{deterministic_json}

Required flat JSON schema:
{schema_json}

Category-specific extraction rules:
{guardrails}

Highlighted relevant excerpts:
{highlighted_text}
"""


PROPERTY_EXTRACTION_PROMPT_TEMPLATE = """Extract property data from this single
lease-contract clause. Return JSON only.

Rules:
1. Use only information explicitly present in the clause.
2. Do not infer, correct, or invent any value.
3. Return an empty string when a value does not exist.
4. area_m2 must contain only the numeric value in square metres, without a unit.

Required JSON object:
{
  "property_name": "",
  "matrix_article": "",
  "matrix_section": "",
  "area_m2": ""
}

Lease-contract clause:
{clause_text}
"""

CATEGORY_PROMPT_TREE = {
    "lease_contract": (
        "Large type: real-estate contractual document.\n"
        "Possibilities: lease agreement, lease amendment, renewal, termination, addendum, rent update, purchase option, CPCV/contrato-promessa de compra e venda, or acordo de cedencia/cessao de posicao contratual.\n"
        "Use canonical contract_type/document_subtype codes when possible: contrato_de_arrendamento, opcao_de_compra, contrato_promessa_compra_venda, acordo_cedencia_posicao_contratual, ratificacao, aditamento, renovacao, rescisao.\n"
        "For CPCV/contrato-promessa de compra e venda, identify it as contrato_promessa_compra_venda; capture the explicit purchase/promised sale price in purchase_price when present, and never convert it into monthly_rent.\n"
        "For purchase option agreements, identify opcao_de_compra and capture the explicit option price in option_price when present.\n"
        "For acordo de cedencia/cessao de posicao contratual, identify it as acordo_cedencia_posicao_contratual; capture the explicit assignment/cession consideration in assignment_price when present; do not treat cedente/cessionario/cedido as lessor/lessee unless the text explicitly says they are senhorio/arrendatario or the assigned contract relationship makes that mapping explicit.\n"
        "Extract lessor, lessee, signed date, property and rent only when explicit and applicable to the subtype.\n"
        "signed_date is the date on which the parties signed the contract. Do not use an effective, licence, registration or certification date as signed_date.\n"
        "If the contract takes effect after a licence is obtained, keep contract_start_date empty unless an explicit calendar date is stated.\n"
        "When there are multiple lessors, put all names in lessor separated by semicolons. Never use a key named lessors.\n"
        "lessor and lessee must contain names only. Do not include identification numbers, tax IDs, citizen card numbers, marital status, matrimonial regime, addresses, dates, or descriptive prose.\n"
        "The extraction excerpts are structurally labelled. For lessor/owner use only PARTIES — LESSOR; for lessee use only PARTIES — LESSEE; for property use only PROPERTY RECITAL; for dates use TERM; and for commercial amounts use COMMERCIAL CLAUSES. If the permitted section does not explicitly support a value, return an empty string.\n"
        "Do not use Passport, Government, Canada, Citizenship or Immigration as a lease party unless the same entity is explicitly and repeatedly declared as a party in the labelled party section.\n"
        "Keep every extracted value concise. Never copy complete clauses or paragraphs.\n"
        "When OCR is corrupted, extract only a name that remains clearly readable; otherwise return an empty string.\n"
        "Extract monthly_rent only for an actual lease/rent obligation when the document explicitly states a monthly rent; do not convert annual rent, option price, purchase price, deposit, signal, transfer price, assignment price or other consideration.\n"
        "Extract property data only from the leased-property description, not from the parties' fiscal addresses.\n"
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
        classification = classify_with_ollama(
            base_url,
            model,
            file_name,
            classification_text,
            signals,
            timeout_seconds,
        )
    except OllamaTimeoutError as exc:
        return _timeout_fallback_result(signals, "classification", exc)
    except OllamaConnectionError:
        # Keep connection failures distinct so the processor can stop or retry
        # the batch instead of treating the PDF itself as defective.
        raise
    except RuntimeError as exc:
        return _runtime_fallback_result(signals, "classification", exc)

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
            "extraction_notes": (
                f"Timeout na segunda avaliacao do Ollama local: {exc}"
            ),
        }
    except OllamaConnectionError:
        raise
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(
            "Second Ollama evaluation failed for %s: %s",
            file_name,
            exc,
     )
        extraction = {
         "confidence": "low",
         "extraction_notes": (
            "Falha na segunda avaliacao do Ollama local: "
            f"{str(exc)[:300]}"
         ),
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
            contract_type_list=contract_type_options_for_prompt(),
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
    raw_response = _chat_json(
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
        output_schema=_json_schema_for_fields(schema),
        num_predict=750,
    )
    normalized, diagnostics = _normalize_extraction_response(raw_response, document_category)
    if not diagnostics["schema_valid"]:
        logger.warning(
            "Ollama extraction schema mismatch for %s: missing=%s unknown=%s invalid_types=%s",
            file_name, diagnostics["missing_keys"], diagnostics["unknown_keys"], diagnostics["invalid_value_types"],
        )
        # extraction_notes belongs to classification, so preserve the warning in logs;
        # review rules can later use schema diagnostics if added to the data model.
    return normalized


def extract_property_with_ollama(
    base_url: str,
    model: str,
    clause_text: str,
    *,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    """Recover only property fields from a short, already-selected clause."""
    schema = {
        "property_name": "",
        "matrix_article": "",
        "matrix_section": "",
        "area_m2": "",
    }
    response = _chat_json(
        base_url,
        model,
        PROPERTY_EXTRACTION_PROMPT_TEMPLATE.format(clause_text=clause_text),
        timeout_seconds=timeout_seconds,
        output_schema=_json_schema_for_fields(schema),
        num_predict=180,
    )
    return {field: response.get(field, "") for field in schema}


def _chat_json(
    base_url: str,
    model: str,
    user_prompt: str,
    *,
    timeout_seconds: int,
    output_schema: dict[str, Any] | None = None,
    num_predict: int = 500,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "format": output_schema if output_schema is not None else "json",
        "stream": False,
        "think": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "options": {"temperature": 0, "num_predict": num_predict},
    }
    body = json.dumps(payload).encode("utf-8")
    http_request = request.Request(
        f"{base_url}/api/chat", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with request.urlopen(http_request, timeout=timeout_seconds) as response:
            raw_response = json.loads(response.read().decode("utf-8"))
    except TimeoutError as exc:
        raise OllamaTimeoutError(f"timeout after {timeout_seconds}s") from exc
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Ollama HTTP error {exc.code}: {details[:1000]}"
        ) from exc
    except error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise OllamaConnectionError(
            f"Could not connect to local Ollama at {base_url}: {reason}"
        ) from exc
    except ConnectionResetError as exc:
        raise OllamaConnectionError(
            "Ollama closed the connection unexpectedly. The local Ollama "
            "process may have crashed, restarted or run out of resources."
        ) from exc
    except PermissionError as exc:
        raise OllamaConnectionError(
            f"Windows blocked access to the local Ollama endpoint at {base_url}. "
            "Check whether Ollama is running and whether port 11434 is accessible."
        ) from exc
    except OSError as exc:
        raise OllamaConnectionError(
            f"Local Ollama connection failed at {base_url}: {exc}"
        ) from exc

    _log_ollama_metrics(raw_response, model)
    done_reason = str(raw_response.get("done_reason") or "unknown")
    if done_reason == "length":
        raise RuntimeError(
            "Ollama reached the output token limit before completing the "
            f"response (num_predict={num_predict})."
        )

    message = raw_response.get("message", {})
    if not isinstance(message, dict):
        raise RuntimeError("Ollama response did not contain a valid message object.")
    content = str(message.get("content") or "")
    if not content.strip():
        done_reason = raw_response.get("done_reason") or "unknown"
        thinking_chars = len(str(message.get("thinking") or ""))
        raise RuntimeError(
            "Ollama returned empty content "
            f"(done_reason={done_reason}, thinking_chars={thinking_chars})."
        )
    return _parse_json_object(content)


def _log_ollama_metrics(
    raw_response: dict[str, Any],
    model: str,
) -> None:
    def seconds(key: str) -> float:
        value = raw_response.get(key) or 0
        try:
            return float(value) / 1_000_000_000
        except (TypeError, ValueError):
            return 0.0

    message = raw_response.get("message", {})
    if not isinstance(message, dict):
        message = {}

    done_reason = str(
        raw_response.get("done_reason") or "unknown"
    )
    thinking = str(message.get("thinking") or "")
    content = str(message.get("content") or "")

    total_seconds = seconds("total_duration")
    load_seconds = seconds("load_duration")
    prompt_tokens = raw_response.get("prompt_eval_count") or 0
    prompt_seconds = seconds("prompt_eval_duration")
    output_tokens = raw_response.get("eval_count") or 0
    eval_seconds = seconds("eval_duration")

    logger.info(
        "Ollama metrics "
        f"model={model} "
        f"total={total_seconds:.2f}s "
        f"load={load_seconds:.2f}s "
        f"prompt_tokens={prompt_tokens} "
        f"prompt_eval={prompt_seconds:.2f}s "
        f"output_tokens={output_tokens} "
        f"eval={eval_seconds:.2f}s "
        f"done_reason={done_reason} "
        f"thinking_chars={len(thinking)} "
        f"content_chars={len(content)}"
    )


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


def _runtime_fallback_result(
    signals: DeterministicSignals,
    step: str,
    exc: Exception,
) -> ExtractionResult:
    deterministic_json = signals.to_dict()
    llm_json = {"error": {"step": step, "message": str(exc)}}
    return ExtractionResult(
        document_category=signals.suggested_category,
        document_type="",
        confidence="low",
        extraction_notes=(
            f"Falha na {step} do Ollama local; resultado baseado apenas em "
            f"regras deterministicas: {str(exc)[:300]}"
        ),
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


CLASSIFICATION_FIELDS = {
    "document_category", "document_type", "document_subtype", "document_date",
    "summary", "language", "confidence", "extraction_notes",
}


def _blank_schema(document_category: str) -> dict[str, str]:
    fields = CATEGORY_EXTRACTION_FIELDS.get(document_category, CATEGORY_EXTRACTION_FIELDS["other"])
    return {name: "" for name in fields if name not in CLASSIFICATION_FIELDS}


def _json_schema_for_fields(fields: dict[str, str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {name: {"type": "string"} for name in fields},
        "required": list(fields),
        "additionalProperties": False,
    }


def _flatten_known_response_shapes(response: dict[str, Any]) -> dict[str, Any]:
    """Defensive compatibility for non-compliant or older LLM response shapes."""
    flattened = dict(response)
    fields = response.get("fields")
    if isinstance(fields, dict):
        flattened.update(fields)
        prop = fields.get("property_details")
        if isinstance(prop, dict):
            flattened.update(prop)
        duration = fields.get("contract_duration")
        if isinstance(duration, dict):
            flattened.setdefault("contract_start_date", duration.get("start_date", ""))
            flattened.setdefault("contract_end_date", duration.get("end_date", ""))
    if not flattened.get("lessor") and flattened.get("lessors"):
        flattened["lessor"] = flattened["lessors"]
    return flattened


def _normalize_extraction_response(response: dict[str, Any], document_category: str) -> tuple[dict[str, str], dict[str, Any]]:
    expected = _blank_schema(document_category)
    expected_keys, actual_keys = set(expected), set(response)
    flattened = _flatten_known_response_shapes(response)
    missing = sorted(expected_keys - actual_keys)
    unknown = sorted(actual_keys - expected_keys)
    invalid = sorted(k for k in expected_keys & actual_keys if not isinstance(response[k], str))
    normalized = {key: _clean(flattened.get(key, "")) for key in expected}
    return normalized, {
        "schema_valid": not missing and not unknown and not invalid,
        "missing_keys": missing, "unknown_keys": unknown, "invalid_value_types": invalid,
    }


def _category_list() -> str:
    return "\n".join(f"- {category}: {description}" for category, description in OFFICIAL_CATEGORIES.items())
