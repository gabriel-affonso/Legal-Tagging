from __future__ import annotations

import json
from typing import Any
from urllib import error, request

from .models import RenderedPage, VisionFieldCandidate
from .prompt_builder import build_prompt, response_schema


class VisionRecoveryError(RuntimeError):
    """A non-fatal Step 3.5 model or response failure."""


def unload_model(*, base_url: str, model: str, timeout_seconds: int = 15) -> None:
    """Best-effort release before loading the visual model on a 16 GB host."""
    payload = json.dumps({"model": model, "prompt": "", "keep_alive": 0}).encode("utf-8")
    http_request = request.Request(
        base_url.rstrip("/") + "/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(http_request, timeout=timeout_seconds):
            pass
    except (error.URLError, OSError, TimeoutError):
        # A failed unload is not a document failure. The visual request still
        # has its own timeout and Ollama may unload automatically under pressure.
        return


def request_visual_candidates(
    *,
    base_url: str,
    model: str,
    file_name: str,
    rendered_page: RenderedPage,
    fields: tuple[str, ...],
    timeout_seconds: int,
    keep_alive: str,
) -> tuple[list[VisionFieldCandidate], dict[str, Any]]:
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "keep_alive": keep_alive,
        "format": response_schema(),
        "messages": [{
            "role": "user",
            "content": build_prompt(
                page_number=rendered_page.page_number,
                fields=fields,
                file_name=file_name,
            ),
            "images": [rendered_page.image_base64],
        }],
        "options": {"temperature": 0, "num_predict": 650},
    }
    raw = _post_chat(base_url, payload, timeout_seconds)
    content = raw.get("message", {}).get("content", "")
    parsed = _parse_object(content)
    if not isinstance(parsed.get("field_candidates"), list):
        raise VisionRecoveryError("Vision response did not contain field_candidates")
    page_meta = {
        "page": rendered_page.page_number,
        "image_sha256": rendered_page.image_sha256,
        "image_width": rendered_page.width,
        "image_height": rendered_page.height,
        "page_legibility": str(parsed.get("page_legibility") or ""),
        "handwriting_present": bool(parsed.get("handwriting_present", False)),
        "contract_evidence": parsed.get("contract_evidence", {}),
    }
    return _parse_candidates(parsed["field_candidates"], rendered_page.page_number), page_meta


def _post_chat(base_url: str, payload: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    http_request = request.Request(
        base_url.rstrip("/") + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(http_request, timeout=timeout_seconds) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except TimeoutError as exc:
        raise VisionRecoveryError(f"timeout after {timeout_seconds}s") from exc
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise VisionRecoveryError(f"Ollama HTTP {exc.code}: {details[:300]}") from exc
    except (error.URLError, OSError) as exc:
        raise VisionRecoveryError(f"local Ollama vision request failed: {exc}") from exc
    if not isinstance(parsed, dict):
        raise VisionRecoveryError("Ollama vision response was not an object")
    if str(parsed.get("done_reason") or "") == "length":
        raise VisionRecoveryError("vision response reached output limit")
    return parsed


def _parse_object(content: object) -> dict[str, Any]:
    raw = str(content or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else ""
        if raw.endswith("```"):
            raw = raw[:-3]
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VisionRecoveryError("vision response was not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise VisionRecoveryError("vision JSON was not an object")
    return parsed


def _parse_candidates(values: list[object], page_number: int) -> list[VisionFieldCandidate]:
    parsed: list[VisionFieldCandidate] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        try:
            confidence = max(0.0, min(1.0, float(item.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        uncertain = item.get("uncertain_tokens", [])
        parsed.append(VisionFieldCandidate(
            field_name=str(item.get("field_name") or "").strip(),
            proposed_value=str(item.get("proposed_value") or "").strip(),
            evidence=str(item.get("evidence") or "").strip(),
            page_number=page_number,
            confidence=confidence,
            content_type=str(item.get("content_type") or "printed").strip().lower(),
            block_id=str(item.get("block_id") or "").strip(),
            uncertain_tokens=tuple(str(value).strip() for value in uncertain if str(value).strip()) if isinstance(uncertain, list) else (),
        ))
    return parsed
