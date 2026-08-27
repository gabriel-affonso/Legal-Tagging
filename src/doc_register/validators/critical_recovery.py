from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib import error, request

from ..contract_types import canonical_contract_type, detect_contract_type
from ..models import ExtractionResult

GENERIC_PARTIES = {
    "SENHORIO", "SENHORIOS", "ARRENDATARIO", "ARRENDATARIA",
    "ARRENDATARIOS", "ARRENDATARIAS", "PRIMEIRO OUTORGANTE",
    "PRIMEIRA OUTORGANTE", "SEGUNDO OUTORGANTE", "SEGUNDA OUTORGANTE",
    "OUTORGANTE", "OUTORGANTES",
}

KNOWN_LESSEE_CANONICAL = {
    "GESTO ENERGIA": "GESTO ENERGIA, S.A.",
    "GESTAO ENERGIA": "GESTO ENERGIA, S.A.",
    "Q ENERGY": "Q ENERGY",
    "UTU ENERGIA": "UTU ENERGIA, S.A.",
}

DATE_PATTERNS = (
    re.compile(
        r"(?:assinado|celebrado|outorgado)\s+(?:em|aos?)\s+.{0,40}?"
        r"(\d{1,2}[/-]\d{1,2}[/-](?:19|20)\d{2})",
        re.IGNORECASE,
    ),
    re.compile(r"\b((?:19|20)\d{2}-\d{2}-\d{2})\b"),
)

ARTICLE_PATTERNS = (
    re.compile(
        r"(?:artigo|matriz(?:\s+predial)?(?:\s+n[.ºo°]*)?)"
        r"\s*[:#-]?\s*(\d{1,8})",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:art\.?|matriz)\s*(\d{1,8})\b", re.IGNORECASE),
)

SECTION_PATTERNS = (
    re.compile(
        r"sec(?:cao|ção|c[.ªa])\s*[:#-]?\s*([A-Z]{1,3})\b",
        re.IGNORECASE,
    ),
)

MONEY_PATTERNS = (
    re.compile(
        r"(?:renda\s+mensal|mensalmente)\D{0,40}?"
        r"(\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})?)\s*(€|EUR)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})?)\s*(€|EUR)"
        r"\s*(?:por\s+m[eê]s|mensais)",
        re.IGNORECASE,
    ),
)

PERSON_LINE_PATTERNS = (
    re.compile(
        r"(?:senhorios?|locadores?|primeiros?\s+outorgantes?)"
        r"\s*[:,-]?\s*([^\n.;]{5,180})",
        re.IGNORECASE,
    ),
)


@dataclass
class RecoveryChange:
    field: str
    old_value: str
    new_value: str
    method: str
    confidence: str
    evidence: str = ""


@dataclass
class RecoveryReport:
    changes: list[RecoveryChange] = field(default_factory=list)
    attempted_fields: list[str] = field(default_factory=list)
    unresolved_fields: list[str] = field(default_factory=list)
    ai_attempted: bool = False
    ai_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "changes": [asdict(change) for change in self.changes],
            "attempted_fields": list(self.attempted_fields),
            "unresolved_fields": list(self.unresolved_fields),
            "ai_attempted": self.ai_attempted,
            "ai_error": self.ai_error,
        }


def recover_critical_fields(
    result: ExtractionResult,
    *,
    file_name: str,
    document_text: str,
    ollama_url: str = "http://localhost:11434",
    ollama_model: str = "qwen3:8b",
    ai_enabled: bool = False,
    timeout_seconds: int = 180,
) -> tuple[ExtractionResult, RecoveryReport]:
    """Recover missing or generic critical fields in a lease contract.

    Deterministic recovery always runs first. The optional focused Ollama call
    runs only for fields that remain unresolved. A recovery failure never stops
    the document batch.
    """
    report = RecoveryReport()

    if (result.document_category or "").strip().lower() != "lease_contract":
        return result, report

    report.attempted_fields = _recovery_targets(result)
    if not report.attempted_fields:
        _attach_report(result, report)
        return result, report

    _recover_known_lessee(result, document_text, report)
    _recover_lessor_from_filename_and_text(
        result, file_name, document_text, report
    )
    _recover_structured_fields(result, document_text, report)

    remaining = _recovery_targets(result)
    if remaining and ai_enabled:
        report.ai_attempted = True
        try:
            recovered = _focused_ollama_recovery(
                result=result,
                file_name=file_name,
                document_text=document_text,
                fields=remaining,
                base_url=ollama_url,
                model=ollama_model,
                timeout_seconds=timeout_seconds,
            )
            _apply_ai_recovery(result, recovered, document_text, report)
        except Exception as exc:
            report.ai_error = str(exc)[:500]

    report.unresolved_fields = _recovery_targets(result)
    _attach_report(result, report)
    return result, report


def _recovery_targets(result: ExtractionResult) -> list[str]:
    targets: list[str] = []
    fields = (
        "lessor", "lessee", "signed_date", "property_article",
        "property_section", "monthly_rent",
    )
    for field_name in fields:
        if field_name == "monthly_rent" and not _should_recover_monthly_rent(result):
            continue
        value = str(getattr(result, field_name, "") or "").strip()
        is_generic_party = (
            field_name in {"lessor", "lessee"}
            and _normalize(value) in GENERIC_PARTIES
        )
        if not value or is_generic_party:
            targets.append(field_name)
    return targets


def _should_recover_monthly_rent(result: ExtractionResult) -> bool:
    canonical = (
        canonical_contract_type(str(getattr(result, "contract_type", "") or ""))
        or canonical_contract_type(str(getattr(result, "document_subtype", "") or ""))
        or detect_contract_type(
            str(getattr(result, "document_type", "") or ""),
            str(getattr(result, "document_subtype", "") or ""),
            str(getattr(result, "contract_type", "") or ""),
        )
    )
    if canonical is not None:
        return canonical.requires_monthly_rent
    return True


def _recover_known_lessee(
    result: ExtractionResult,
    text: str,
    report: RecoveryReport,
) -> None:
    if "lessee" not in _recovery_targets(result):
        return

    normalized_text = _normalize(text)
    for fragment, canonical in KNOWN_LESSEE_CANONICAL.items():
        if fragment in normalized_text:
            _set_recovered(
                result, report, "lessee", canonical,
                "known_lessee_text_match", "high", fragment,
            )
            return


def _recover_lessor_from_filename_and_text(
    result: ExtractionResult,
    file_name: str,
    text: str,
    report: RecoveryReport,
) -> None:
    """Recover one or more filename lessor candidates confirmed in text."""
    if "lessor" not in _recovery_targets(result):
        return

    file_stem = file_name.rsplit(".", 1)[0]
    file_stem = re.sub(
        r"__?[0-9a-fA-F]{12,64}(?:__ocr)?$", "", file_stem,
        flags=re.IGNORECASE,
    )
    file_stem = re.sub(
        r"(?:_|-|\s)*SP\d+(?:_|-|\s)*$", "", file_stem,
        flags=re.IGNORECASE,
    )
    file_stem = re.sub(r"^(?:_?\[?\d+\]?[_\s-]*)", "", file_stem)

    marker_match = re.search(
        r"(?:_CA(?:V|OPC)?_|_CAV_|CONTRATO[^_]*_)(.+)$",
        file_stem,
        flags=re.IGNORECASE,
    )
    raw_candidate = marker_match.group(1) if marker_match else ""

    if raw_candidate:
        raw_candidate = raw_candidate.replace("_", " ")
        raw_candidate = re.sub(
            r"\b(?:IBAN|RATIFICACAO|RATIFICAÇÃO|RECTIFICADO|RECTIFICADA|"
            r"ASSINADO|ASSINADA)\b.*$",
            "",
            raw_candidate,
            flags=re.IGNORECASE,
        )
        raw_candidate = re.sub(r"\s+", " ", raw_candidate).strip(" ,;:_-")

        candidate_names = _split_party_names(raw_candidate)
        supported_names = [
            candidate
            for candidate in candidate_names
            if _candidate_supported_by_text(candidate, text)
        ]
        if supported_names:
            joined = "; ".join(supported_names)
            _set_recovered(
                result, report, "lessor", joined,
                "filename_candidate_confirmed_in_text", "high", joined,
            )
            return

    for pattern in PERSON_LINE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        candidate = _clean_party_candidate(match.group(1))
        if candidate:
            _set_recovered(
                result, report, "lessor", candidate,
                "labelled_text_pattern", "medium", match.group(0)[:180],
            )
            return


def _split_party_names(value: str) -> list[str]:
    """Split a filename party segment into individual names."""
    if not value:
        return []

    normalized_value = re.sub(r"\s+", " ", value).strip(" ,;:_-")
    parts = re.split(
        r"\s+(?:e|&)\s+|[;|]+",
        normalized_value,
        flags=re.IGNORECASE,
    )

    output: list[str] = []
    for part in parts:
        cleaned = re.sub(r"\s+", " ", part).strip(" ,;:_-")
        if len(cleaned) < 5 or _normalize(cleaned) in GENERIC_PARTIES:
            continue
        if cleaned not in output:
            output.append(cleaned)
    return output


def _candidate_supported_by_text(candidate: str, document_text: str) -> bool:
    """Confirm a candidate using a full match or ordered nearby tokens."""
    normalized_candidate = _normalize(candidate)
    normalized_document = _normalize(document_text)

    if not normalized_candidate or not normalized_document:
        return False
    if normalized_candidate in normalized_document:
        return True

    tokens = [token for token in normalized_candidate.split() if len(token) >= 3]
    if len(tokens) < 2:
        return False

    ordered_pattern = r"\b" + r"\W{0,30}".join(
        re.escape(token) for token in tokens
    ) + r"\b"
    if re.search(ordered_pattern, normalized_document):
        return True

    matched = sum(
        1 for token in tokens
        if re.search(rf"\b{re.escape(token)}\b", normalized_document)
    )
    return matched >= max(2, len(tokens) - 1)


def _recover_structured_fields(
    result: ExtractionResult,
    text: str,
    report: RecoveryReport,
) -> None:
    if "signed_date" in _recovery_targets(result):
        for pattern in DATE_PATTERNS:
            match = pattern.search(text)
            if match:
                _set_recovered(
                    result, report, "signed_date",
                    _normalize_date(match.group(1)), "signed_date_pattern",
                    "medium", match.group(0)[:180],
                )
                break

    if "property_article" in _recovery_targets(result):
        for pattern in ARTICLE_PATTERNS:
            match = pattern.search(text)
            if match:
                _set_recovered(
                    result, report, "property_article", match.group(1),
                    "property_article_pattern", "medium", match.group(0)[:180],
                )
                break

    if "property_section" in _recovery_targets(result):
        for pattern in SECTION_PATTERNS:
            match = pattern.search(text)
            if match:
                _set_recovered(
                    result, report, "property_section", match.group(1).upper(),
                    "property_section_pattern", "medium", match.group(0)[:180],
                )
                break

    if "monthly_rent" in _recovery_targets(result):
        for pattern in MONEY_PATTERNS:
            match = pattern.search(text)
            if match:
                amount = match.group(1).replace(" ", "")
                currency = "EUR" if match.group(2).upper() in {"EUR", "€"} else match.group(2)
                _set_recovered(
                    result, report, "monthly_rent", f"{amount} {currency}",
                    "monthly_rent_pattern", "medium", match.group(0)[:180],
                )
                if not getattr(result, "currency", ""):
                    result.currency = "EUR"
                break


def _focused_ollama_recovery(
    *,
    result: ExtractionResult,
    file_name: str,
    document_text: str,
    fields: list[str],
    base_url: str,
    model: str,
    timeout_seconds: int,
) -> dict[str, str]:
    schema = {field_name: "" for field_name in fields}
    excerpt = _focused_excerpt(document_text, 6500)
    current = {field_name: getattr(result, field_name, "") for field_name in fields}
    prompt = (
        "Recover only the missing critical fields from this confidential "
        "Portuguese lease contract. Use only explicit evidence in the text. "
        "Names must be people or companies, never labels. signed_date must be "
        "the signature date. monthly_rent must be explicitly monthly and must "
        "not be a purchase price, option price or cedencia/cessao consideration. Return "
        "exactly the supplied flat JSON keys, string values only.\n\n"
        f"File name (candidate hint only): {file_name}\n"
        f"Current values: {json.dumps(current, ensure_ascii=False)}\n"
        f"Required JSON: {json.dumps(schema, ensure_ascii=False)}\n\n"
        f"Focused text:\n{excerpt}"
    )
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": "Return valid JSON only. Never invent facts."},
            {"role": "user", "content": prompt},
        ],
        "options": {"temperature": 0, "num_predict": 350},
    }
    url = base_url.rstrip("/") + "/api/chat"
    http_request = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(http_request, timeout=timeout_seconds) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except (error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Focused Ollama recovery failed: {exc}") from exc

    content = raw.get("message", {}).get("content", "")
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise RuntimeError("Focused Ollama recovery did not return a JSON object")
    return {
        key: str(parsed.get(key, "") or "").strip()
        for key in fields
    }


def _apply_ai_recovery(
    result: ExtractionResult,
    values: dict[str, str],
    text: str,
    report: RecoveryReport,
) -> None:
    normalized_text = _normalize(text)
    for field_name, value in values.items():
        if not value or field_name not in _recovery_targets(result):
            continue
        if field_name in {"lessor", "lessee"}:
            normalized_value = _normalize(value)
            if normalized_value in GENERIC_PARTIES:
                continue
            if normalized_value not in normalized_text:
                continue
        _set_recovered(
            result, report, field_name, value,
            "focused_local_ollama", "medium", value[:180],
        )


def _set_recovered(
    result: ExtractionResult,
    report: RecoveryReport,
    field_name: str,
    new_value: str,
    method: str,
    confidence: str,
    evidence: str,
) -> None:
    cleaned_value = str(new_value).strip()
    if not cleaned_value:
        return
    old_value = str(getattr(result, field_name, "") or "").strip()
    if old_value == cleaned_value:
        return
    setattr(result, field_name, cleaned_value)
    report.changes.append(
        RecoveryChange(
            field=field_name,
            old_value=old_value,
            new_value=cleaned_value,
            method=method,
            confidence=confidence,
            evidence=evidence,
        )
    )


def _attach_report(result: ExtractionResult, report: RecoveryReport) -> None:
    raw = result.raw_json if isinstance(result.raw_json, dict) else {}
    result.raw_json = dict(raw)
    result.raw_json["critical_recovery"] = report.to_dict()
    if report.changes:
        recovered_fields = ", ".join(change.field for change in report.changes)
        note = f"Sprint 1B recovered: {recovered_fields}"
        result.extraction_notes = " | ".join(
            part for part in (result.extraction_notes, note) if part
        )


def _focused_excerpt(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    front = text[: max_chars // 2]
    end = text[-max_chars // 3 :]
    keywords = (
        "senhorio", "arrendat", "outorgante", "renda", "artigo",
        "secção", "seccao", "assinado",
    )
    lines = [
        line for line in text.splitlines()
        if any(keyword in line.lower() for keyword in keywords)
    ]
    middle_budget = max_chars - len(front) - len(end)
    middle = "\n".join(lines)[: max(0, middle_budget)]
    return front + "\n[RELEVANT LINES]\n" + middle + "\n[DOCUMENT END]\n" + end


def _clean_party_candidate(value: str) -> str:
    value = re.split(
        r"\b(?:NIF|contribuinte|portador|residente|com sede|doravante)\b",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    value = re.sub(r"\s+", " ", value).strip(" ,;:-")
    if _normalize(value) in GENERIC_PARTIES or len(value) < 5:
        return ""
    return value[:250]


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value))
    plain = "".join(
        character for character in decomposed
        if not unicodedata.combining(character)
    )
    return " ".join(plain.upper().split())


def _normalize_date(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    match = re.fullmatch(
        r"(\d{1,2})[/-](\d{1,2})[/-]((?:19|20)\d{2})",
        value,
    )
    if match:
        day, month, year = map(int, match.groups())
        return f"{year:04d}-{month:02d}-{day:02d}"
    return value


__all__ = [
    "RecoveryChange",
    "RecoveryReport",
    "recover_critical_fields",
    "GENERIC_PARTIES",
    "KNOWN_LESSEE_CANONICAL",
]
