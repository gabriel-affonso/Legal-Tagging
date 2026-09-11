"""Focused extraction of lease annual rent and reservation percentage.

This runner is intentionally independent from the full document register.  It
uses the document type already present in the workbook as its eligibility gate
and writes its results to a separate worksheet.  No document is classified and
no existing operational row is changed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import logging
from pathlib import Path
import re
import subprocess
import unicodedata

from .config import AppConfig
from .pdf_text import run_ocrmypdf
from .registry import _column_letter, _workbook_lock
from .document_ai_v2 import DocumentAIV2Pipeline, V2Config


LOGGER = logging.getLogger(__name__)

RENT_SHEET_NAME = "Rent Extraction"
PIPELINE_VERSION = "rent-clause-2.0"
V2_PIPELINE_VERSION = "rent-document-ai-v2.0.0"
LEGACY_RENT_COLUMNS = (
    "pipeline_version",
    "processed_at",
    "status",
    "source_sheet",
    "source_row",
    "source_file_name",
    "source_file_path",
    "sha256",
    "annual_rent_eur",
    "annual_rent_text",
    "annual_rent_page",
    "annual_rent_evidence",
    "reservation_title_percent",
    "reservation_title_percent_text",
    "reservation_title_page",
    "reservation_title_evidence",
    "text_source",
    "extraction_notes",
)
RENT_COLUMNS = LEGACY_RENT_COLUMNS + (
    "rent_amount_type", "rent_rate_eur_per_ha_year", "fixed_annual_rent_eur",
    "effective_occupied_area_ha", "annual_rent_total_eur", "rent_frequency",
    "full_rent_start_trigger", "rent_update_rule", "reservation_start_triggers",
    "reservation_end_trigger", "financial_extraction_json", "review_reasons",
    "canonical_document_path", "provenance_json", "processing_report_json",
)

_PAGE_MARKER = re.compile(r"^\[Page\s+(\d+)\]\s*$", re.IGNORECASE | re.MULTILINE)
_CLAUSE_START = re.compile(
    r"(?:cl[aá]usula\s*(?:n[.º°o]\s*)?(?:quinta\b|5\s*(?:\.|º|ª|o|a)?)[^\n]{0,120}|renda\s+e\s+forma\s+de\s+pagamento)",
    re.IGNORECASE,
)
_CLAUSE_END = re.compile(
    r"cl[aá]usula\s+(?:sexta\b|6\s*(?:\.|º|ª|o|a)?)",
    re.IGNORECASE,
)
_MONEY = r"(?:€\s*)?(\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})|\d+(?:,\d{2})?)(?:\s*(EUR|€|euros?))?"
_ANNUAL_RENT = re.compile(r"renda\s+anual.{0,180}?" + _MONEY, re.IGNORECASE | re.DOTALL)
_RESERVATION_PERCENT = re.compile(
    r"(?:pagamento\s+anual\s+de\s+)?"
    r"(\d{1,3}(?:[,.]\d+)?)\s*%"
    r"(?:\s+do\s+valor\s+da\s+renda\s+anual)?"
    r"(?:(?![\n]{2}).){0,260}?"
    r"t[íi]tulo\s+de\s+reserva",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class WorkbookContract:
    source_sheet: str
    source_row: int
    source_file_name: str
    source_file_path: Path | None
    sha256: str


@dataclass(frozen=True)
class ValueEvidence:
    value: Decimal | None
    display: str
    page: int | None
    evidence: str


@dataclass(frozen=True)
class RentClauseResult:
    annual_rent: ValueEvidence
    reservation_percent: ValueEvidence
    rent_clause_found: bool = False
    rent_clause_title: str | None = None
    rent_clause_number: str | None = None
    rent_amount_type: str | None = None
    rent_rate_eur_per_ha_year: Decimal | None = None
    fixed_annual_rent_eur: Decimal | None = None
    approximate_contract_area_ha: Decimal | None = None
    effective_occupied_area_ha: Decimal | None = None
    annual_rent_total_eur: Decimal | None = None
    annual_rent_formula: str | None = None
    rent_frequency: str | None = None
    full_rent_start_trigger: str | None = None
    first_full_rent_due_rule: str | None = None
    subsequent_rent_due_rule: str | None = None
    rent_update_rule: str | None = None
    negative_update_allowed: bool | None = None
    payment_allocated_by_ownership_shares: bool | None = None
    reservation_payment_required: bool | None = None
    reservation_start_triggers: tuple[str, ...] = ()
    reservation_end_trigger: str | None = None
    reservation_first_due_rule: str | None = None
    reservation_subsequent_due_rule: str | None = None
    reservation_prorata_rule: str | None = None
    reservation_overpayment_compensation_rule: str | None = None
    source_pages: tuple[int, ...] = ()
    rent_timing_evidence: str = ""
    reservation_evidence: str = ""
    prorata_evidence: str = ""
    review_reasons: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        if self.annual_rent.value is not None and self.reservation_percent.value is not None:
            return "processed"
        if self.annual_rent.value is not None or self.reservation_percent.value is not None:
            return "needs_review_partial"
        return "needs_review_not_found"

    def as_financial_record(self) -> dict[str, object]:
        """Stable, JSON-serialisable contract-finance record for downstream users."""
        number = lambda value: float(value) if value is not None else None
        return {
            "document_financial_type": "lease_rent",
            "rent_clause_found": self.rent_clause_found,
            "rent_clause_title": self.rent_clause_title,
            "rent_clause_number": self.rent_clause_number,
            "rent_amount_type": self.rent_amount_type,
            "rent_rate_eur_per_ha_year": number(self.rent_rate_eur_per_ha_year),
            "fixed_annual_rent_eur": number(self.fixed_annual_rent_eur),
            "currency": "EUR",
            "rent_frequency": self.rent_frequency,
            "approximate_contract_area_ha": number(self.approximate_contract_area_ha),
            "effective_occupied_area_ha": number(self.effective_occupied_area_ha),
            "annual_rent_total_eur": number(self.annual_rent_total_eur),
            "annual_rent_formula": self.annual_rent_formula,
            "full_rent_start_trigger": self.full_rent_start_trigger,
            "first_full_rent_due_rule": self.first_full_rent_due_rule,
            "subsequent_rent_due_rule": self.subsequent_rent_due_rule,
            "rent_update_rule": self.rent_update_rule,
            "negative_update_allowed": self.negative_update_allowed,
            "payment_allocated_by_ownership_shares": self.payment_allocated_by_ownership_shares,
            "reservation_payment_required": self.reservation_payment_required,
            "reservation_percentage": number(self.reservation_percent.value),
            "reservation_rate": number(self.reservation_percent.value / Decimal("100")) if self.reservation_percent.value is not None else None,
            "reservation_basis": "annual_rent" if self.reservation_percent.value is not None else None,
            "reservation_frequency": "annual" if self.reservation_percent.value is not None else None,
            "reservation_start_triggers": list(self.reservation_start_triggers),
            "reservation_first_due_rule": self.reservation_first_due_rule,
            "reservation_subsequent_due_rule": self.reservation_subsequent_due_rule,
            "reservation_end_trigger": self.reservation_end_trigger,
            "reservation_prorata_rule": self.reservation_prorata_rule,
            "reservation_overpayment_compensation_rule": self.reservation_overpayment_compensation_rule,
            "source_pages": list(self.source_pages),
            "evidence": {"rent_rate_quote": self.annual_rent.evidence or None, "rent_timing_quote": self.rent_timing_evidence or None,
                         "reservation_quote": self.reservation_evidence or None, "prorata_quote": self.prorata_evidence or None},
            "ocr_observations": [], "confidence": 1.0 if self.status == "processed" else 0.65 if self.status == "needs_review_partial" else 0.0,
            "status": self.status, "review_reasons": list(self.review_reasons),
        }


class LeaseRentExtractionProcessor:
    """Read only already-labelled lease contracts and extract clause 5 values."""

    def __init__(self, config: AppConfig, *, pipeline: str = "legacy"):
        self.config = config
        if pipeline not in {"legacy", "v2"}:
            raise ValueError("pipeline must be legacy or v2")
        self.pipeline = pipeline

    def scan_once(self, *, force: bool = False) -> int:
        self.config.ensure_directories()
        contracts = _eligible_contracts(self.config.excel_path, self.config.input_dir)
        existing = _existing_hashes(self.config.excel_path)
        processed = 0
        for contract in contracts:
            identity = contract.sha256 or _path_identity(contract.source_file_path, contract.source_file_name)
            if not force and identity in existing:
                continue
            payload = self._process_contract(contract)
            payload["sha256"] = identity
            _upsert_result(self.config.excel_path, payload)
            existing.add(identity)
            processed += 1
            LOGGER.info("Rent clause extraction finished for %s: %s", contract.source_file_name, payload["status"])
        return processed

    def _process_contract(self, contract: WorkbookContract) -> dict[str, object]:
        base = {
            "pipeline_version": V2_PIPELINE_VERSION if self.pipeline == "v2" else PIPELINE_VERSION,
            "processed_at": _timestamp(),
            "source_sheet": contract.source_sheet,
            "source_row": contract.source_row,
            "source_file_name": contract.source_file_name,
            "source_file_path": str(contract.source_file_path or ""),
        }
        if contract.source_file_path is None or not contract.source_file_path.is_file():
            return {
                **base,
                "status": "source_file_not_found",
                "extraction_notes": "O Excel identifica o contrato, mas o caminho do PDF não está disponível.",
            }

        if self.pipeline == "v2":
            return self._process_contract_v2(contract, base)

        # Pages 8 and 9 are the normal location.  Native text is tried first;
        # this keeps the pass fast for born-digital contracts.
        target_pages = (8, 9)
        native_text = _read_pdf_pages(contract.source_file_path, target_pages)
        extracted = extract_rent_clause(native_text)
        text_source = "native_pages_8_9"
        notes: list[str] = []

        # OCR is only used when the targeted native pages do not give both
        # values.  OCRmyPDF itself handles born-digital pages safely via
        # --skip-text and its result is cached under the immutable file hash.
        if extracted.status != "processed" and self.config.ocr_enabled:
            ocr_path = self.config.ocr_dir / f"{_path_identity(contract.source_file_path, contract.source_file_name)}__rent_ocr.pdf"
            try:
                if not ocr_path.exists():
                    run_ocrmypdf(
                        contract.source_file_path,
                        ocr_path,
                        language=self.config.ocr_language,
                        timeout_seconds=self.config.ocr_timeout_seconds,
                    )
                    notes.append("OCR executado para confirmação das páginas 8–9.")
                else:
                    notes.append("OCR em cache reutilizado para as páginas 8–9.")
                ocr_text = _read_pdf_pages(ocr_path, target_pages)
                ocr_extracted = extract_rent_clause(ocr_text)
                if _result_score(ocr_extracted) >= _result_score(extracted):
                    extracted = ocr_extracted
                    text_source = "ocr_pages_8_9"
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                notes.append(f"OCR indisponível: {exc}")

        # A contract can have a shorter layout or move clause 5.  Do a local,
        # deterministic fallback over its configured initial page window only
        # after the normal pages failed; no other document fields are read.
        if extracted.status != "processed":
            fallback_text = _read_pdf_pages(
                contract.source_file_path,
                tuple(range(1, self.config.max_pdf_pages + 1)),
            )
            fallback = extract_rent_clause(fallback_text)
            if _result_score(fallback) > _result_score(extracted):
                extracted = fallback
                text_source = "native_fallback_pages_1_to_max"
                notes.append("Cláusula 5 não ficou completa nas páginas 8–9; aplicado fallback textual limitado.")

        return {
            **base,
            "status": extracted.status,
            "annual_rent_eur": _decimal_for_excel(extracted.annual_rent.value),
            "annual_rent_text": extracted.annual_rent.display,
            "annual_rent_page": extracted.annual_rent.page or "",
            "annual_rent_evidence": extracted.annual_rent.evidence,
            "rent_amount_type": extracted.rent_amount_type or "",
            "rent_rate_eur_per_ha_year": _decimal_for_excel(extracted.rent_rate_eur_per_ha_year),
            "fixed_annual_rent_eur": _decimal_for_excel(extracted.fixed_annual_rent_eur),
            "effective_occupied_area_ha": _decimal_for_excel(extracted.effective_occupied_area_ha),
            "annual_rent_total_eur": _decimal_for_excel(extracted.annual_rent_total_eur),
            "rent_frequency": extracted.rent_frequency or "",
            "full_rent_start_trigger": extracted.full_rent_start_trigger or "",
            "rent_update_rule": extracted.rent_update_rule or "",
            "reservation_title_percent": _decimal_for_excel(extracted.reservation_percent.value),
            "reservation_title_percent_text": extracted.reservation_percent.display,
            "reservation_title_page": extracted.reservation_percent.page or "",
            "reservation_title_evidence": extracted.reservation_percent.evidence,
            "reservation_start_triggers": ", ".join(extracted.reservation_start_triggers),
            "reservation_end_trigger": extracted.reservation_end_trigger or "",
            "financial_extraction_json": json.dumps(extracted.as_financial_record(), ensure_ascii=False, separators=(",", ":")),
            "review_reasons": "; ".join(extracted.review_reasons),
            "text_source": text_source,
            "extraction_notes": " ".join(notes),
        }

    def _process_contract_v2(self, contract: WorkbookContract, base: dict[str, object]) -> dict[str, object]:
        """Use canonical visual reading order; legacy PDF text is comparison-only."""
        cache_root = self.config.processing_dir / "document-ai-v2-cache"
        canonical = DocumentAIV2Pipeline(cache_root, V2Config()).process(contract.source_file_path)
        extracted = extract_rent_clause(canonical["canonical_text"])
        reasons = list(extracted.review_reasons)
        if canonical["quality"]["warnings"]:
            reasons.append("document_ai_v2_page_failure")
        return {
            **base,
            "status": extracted.status if not canonical["quality"]["warnings"] else "needs_review_visual_parse",
            "annual_rent_eur": _decimal_for_excel(extracted.annual_rent.value), "annual_rent_text": extracted.annual_rent.display,
            "annual_rent_page": extracted.annual_rent.page or "", "annual_rent_evidence": extracted.annual_rent.evidence,
            "rent_amount_type": extracted.rent_amount_type or "", "rent_rate_eur_per_ha_year": _decimal_for_excel(extracted.rent_rate_eur_per_ha_year),
            "fixed_annual_rent_eur": _decimal_for_excel(extracted.fixed_annual_rent_eur), "effective_occupied_area_ha": _decimal_for_excel(extracted.effective_occupied_area_ha),
            "annual_rent_total_eur": _decimal_for_excel(extracted.annual_rent_total_eur), "rent_frequency": extracted.rent_frequency or "",
            "full_rent_start_trigger": extracted.full_rent_start_trigger or "", "rent_update_rule": extracted.rent_update_rule or "",
            "reservation_title_percent": _decimal_for_excel(extracted.reservation_percent.value), "reservation_title_percent_text": extracted.reservation_percent.display,
            "reservation_title_page": extracted.reservation_percent.page or "", "reservation_title_evidence": extracted.reservation_percent.evidence,
            "reservation_start_triggers": ", ".join(extracted.reservation_start_triggers), "reservation_end_trigger": extracted.reservation_end_trigger or "",
            "financial_extraction_json": json.dumps(extracted.as_financial_record(), ensure_ascii=False, separators=(",", ":")),
            "review_reasons": "; ".join(reasons), "text_source": "canonical_visual_reconstruction_v2",
            "extraction_notes": "A camada OCR legada não determinou a ordem de leitura.",
            "canonical_document_path": str(cache_root / canonical["document"]["sha256"]),
            "provenance_json": json.dumps(_rent_provenance(canonical, extracted), ensure_ascii=False, separators=(",", ":")),
            "processing_report_json": json.dumps(canonical["processing"], ensure_ascii=False, separators=(",", ":")),
        }


def extract_rent_clause(text: str) -> RentClauseResult:
    """Extract the financial rules in the rent clause without inferring missing area.

    This remains deterministic: the wider record is an auditable schema, not an
    LLM interpretation.  Every populated value is tied to the bounded clause.
    """
    clause_text = _clause_five_text(text)
    if not clause_text:
        return RentClauseResult(_empty_evidence(), _empty_evidence(), review_reasons=("rent_clause_not_located",))
    annual_matches = [m for m in _ANNUAL_RENT.finditer(clause_text) if re.search(r"(?:EUR|€|euros?)", m[0], re.I)]
    reservation_matches = list(_RESERVATION_PERCENT.finditer(clause_text))
    annual = _money_evidence(annual_matches[0], clause_text) if annual_matches else _empty_evidence()
    reservation = _reservation_evidence(clause_text, reservation_matches)
    rent_context = _local_context(clause_text, annual_matches[0]) if annual_matches else ""
    reservation_context = _local_context(clause_text, reservation_matches[0]) if reservation_matches else reservation.evidence
    per_hectare = bool(re.search(r"por\s+hectare|/\s*ha\b|hectare\s+efetivamente\s+ocup", rent_context, re.I))
    amount_type = "rate_per_area" if annual.value is not None and per_hectare else "fixed_annual_rent" if annual.value is not None else None
    start_triggers = []
    if reservation.value is not None:
        if re.search(r"aprova[cç][aã]o\s+ambiental|declara[cç][aã]o\s+de\s+impacte", reservation_context, re.I):
            start_triggers.append("environmental_approval")
        if re.search(r"licen[cç]a\s+de\s+produ[cç][aã]o", reservation_context, re.I):
            start_triggers.append("production_license_issued")
    pages = tuple(sorted(set(_all_pages_for_text(clause_text))))
    reasons = []
    if annual.value is None: reasons.append("annual_rent_not_found_in_rent_clause")
    if reservation.value is None: reasons.append("reservation_percentage_not_found_in_rent_clause")
    if amount_type == "rate_per_area" and re.search(r"(?:a definir|ser[aá]\s+definida|ap[oó]s\s+.*impacte)", clause_text, re.I):
        reasons.append("effective_occupied_area_not_determined")
    return RentClauseResult(
        annual_rent=annual, reservation_percent=reservation, rent_clause_found=True,
        rent_clause_title="Renda e Forma de Pagamento" if re.search(r"renda\s+e\s+forma\s+de\s+pagamento", clause_text, re.I) else None,
        rent_clause_number="5" if re.search(r"cl[aá]usula\s*(?:n[.º°o]\s*)?(?:quinta|5)", clause_text, re.I) else None,
        rent_amount_type=amount_type, rent_rate_eur_per_ha_year=annual.value if amount_type == "rate_per_area" else None,
        fixed_annual_rent_eur=annual.value if amount_type == "fixed_annual_rent" else None,
        annual_rent_formula="rent_rate_eur_per_ha_year * effective_occupied_area_ha" if amount_type == "rate_per_area" else None,
        rent_frequency="annual" if annual.value is not None else None,
        full_rent_start_trigger=_trigger(clause_text, r"condi[cç][aã]o\s+suspensiva", "suspensive_condition_satisfied"),
        first_full_rent_due_rule=_rule_after(clause_text, r"primeira\s+renda\s+(?:ser[aá]|dever[aá]|vence|paga).{0,160}"),
        subsequent_rent_due_rule=_rule_after(clause_text, r"(?:rendas?|pagamentos?)\s+seguintes\s+(?:ser[aá]|dever[aá]|vence|pagos?).{0,160}"),
        rent_update_rule=_rule_after(clause_text, r"atualiza[cç][aã]o.{0,180}"),
        negative_update_allowed=False if re.search(r"n[aã]o\s+poder[aá]\s+ser\s+inferior|nunca\s+inferior", clause_text, re.I) else None,
        payment_allocated_by_ownership_shares=bool(re.search(r"propor[cç][aã]o\s+das\s+respetivas?\s+quotas", clause_text, re.I)) or None,
        reservation_payment_required=reservation.value is not None,
        reservation_start_triggers=tuple(start_triggers),
        reservation_end_trigger="construction_license_issued" if re.search(r"at[eé]\s+[aà]\s+emiss[aã]o\s+da\s+licen[cç]a\s+de\s+constru[cç][aã]o", reservation_context, re.I) else None,
        reservation_first_due_rule=_rule_after(reservation_context, r"(?:at[eé]|at[eé]\s+ao).{0,180}"),
        reservation_subsequent_due_rule=_rule_after(reservation_context, r"(?:pagamento|reserva)\s+anual(?:mente)?\s+(?:ser[aá]|dever[aá]|vence|pago).{0,160}"),
        reservation_prorata_rule=_rule_after(clause_text, r"(?:proporcional|pro\s*rata).{0,220}"),
        reservation_overpayment_compensation_rule=_rule_after(clause_text, r"(?:compensad|excesso).{0,220}"),
        source_pages=pages, rent_timing_evidence=_excerpt_for_pattern(clause_text, r"(?:paga|pagamento).{0,180}"),
        reservation_evidence=reservation.evidence, prorata_evidence=_excerpt_for_pattern(clause_text, r"(?:proporcional|pro\s*rata|compensad).{0,220}"),
        review_reasons=tuple(reasons),
    )


def _clause_five_text(text: str) -> str:
    start = _CLAUSE_START.search(text)
    if not start:
        return ""
    end = _CLAUSE_END.search(text, start.end())
    # Retain the nearest page marker so evidence stays traceable even when the
    # clause starts partway through the physical page.
    preceding_markers = list(_PAGE_MARKER.finditer(text[: start.start() + 1]))
    clause_start = preceding_markers[-1].start() if preceding_markers else start.start()
    return text[clause_start: end.start() if end else len(text)]


def _local_context(text: str, match: re.Match[str], radius: int = 360) -> str:
    """Keep event wording adjacent to a supported financial match."""
    return text[max(0, match.start() - radius): min(len(text), match.end() + radius)]


def _rule_after(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    return _excerpt(text, match.start(), match.end()) if match else None


def _trigger(text: str, pattern: str, value: str) -> str | None:
    return value if re.search(pattern, text, re.IGNORECASE) else None


def _excerpt_for_pattern(text: str, pattern: str) -> str:
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    return _excerpt(text, match.start(), match.end()) if match else ""


def _all_pages_for_text(text: str) -> list[int]:
    return [int(match.group(1)) for match in _PAGE_MARKER.finditer(text)]


def _money_evidence(match: re.Match[str], text: str) -> ValueEvidence:
    number, currency = match.group(1), match.group(2) or "EUR"
    value = _parse_portuguese_number(number)
    return ValueEvidence(value, f"{number} {currency}", _page_for_offset(text, match.start()), _excerpt(text, match.start(), match.end()))


def _percent_evidence(match: re.Match[str], text: str) -> ValueEvidence:
    number = match.group(1)
    return ValueEvidence(_parse_portuguese_number(number), f"{number}%", _page_for_offset(text, match.start()), _excerpt(text, match.start(), match.end()))


def _reservation_evidence(text: str, numeric_matches: list[re.Match[str]]) -> ValueEvidence:
    if numeric_matches:
        return _percent_evidence(numeric_matches[0], text)
    # Written forms are accepted only inside the same bounded reservation rule;
    # this avoids turning unrelated prose percentages into financial data.
    words = re.search(r"(?:pagamento\s+anual\s+de\s+)?(?:25|vinte\s+e\s+cinco)\s+por\s+cento.{0,260}?t[íi]tulo\s+de\s+reserva", text, re.I | re.DOTALL)
    if words:
        return ValueEvidence(Decimal("25"), "25%", _page_for_offset(text, words.start()), _excerpt(text, words.start(), words.end()))
    return _empty_evidence()


def _empty_evidence() -> ValueEvidence:
    return ValueEvidence(None, "", None, "")


def _parse_portuguese_number(value: str) -> Decimal | None:
    normalized = value.replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def _page_for_offset(text: str, offset: int) -> int | None:
    markers = list(_PAGE_MARKER.finditer(text[: offset + 1]))
    return int(markers[-1].group(1)) if markers else None


def _excerpt(text: str, start: int, end: int) -> str:
    excerpt = " ".join(text[max(0, start - 80): min(len(text), end + 100)].split())
    return excerpt[:600]


def _result_score(result: RentClauseResult) -> int:
    return int(result.annual_rent.value is not None) + int(result.reservation_percent.value is not None)


def _rent_provenance(canonical: dict[str, object], result: RentClauseResult) -> dict[str, list[dict[str, object]]]:
    """Map rent evidence to visual blocks for audit/highlighting.

    It intentionally returns no fabricated provenance when a visual page failed.
    """
    values = {"annual_rent": result.annual_rent, "reservation_percent": result.reservation_percent}
    output: dict[str, list[dict[str, object]]] = {name: [] for name in values}
    for name, evidence in values.items():
        if not evidence.evidence:
            continue
        needle = _plain(evidence.display or evidence.evidence)
        for page in canonical.get("pages", []):
            for block in page.get("blocks", []):
                haystack = _plain(str(block.get("text", "")))
                if needle and needle in haystack:
                    output[name].append({"page": page["number"], "block_id": block["id"], "bbox": block["bbox"],
                                         "source_text": block["text"], "source_engine": block["source_engine"]})
    return output


def _read_pdf_pages(path: Path, page_numbers: tuple[int, ...]) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("Missing dependency: install pypdf with `pip install -r requirements.txt`.") from exc
    reader = PdfReader(str(path))
    chunks: list[str] = []
    for number in sorted(set(page_numbers)):
        if number < 1 or number > len(reader.pages):
            continue
        page_text = (reader.pages[number - 1].extract_text() or "").strip()
        if page_text:
            chunks.append(f"[Page {number}]\n{page_text}")
    return "\n\n".join(chunks)


def _eligible_contracts(workbook_path: Path, input_dir: Path) -> list[WorkbookContract]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("Missing dependency: install openpyxl with `pip install -r requirements.txt`.") from exc
    if not workbook_path.exists():
        raise FileNotFoundError(f"Excel file does not exist: {workbook_path}")
    filename_index = _input_filename_index(input_dir)
    contracts: dict[str, WorkbookContract] = {}
    with _workbook_lock(workbook_path):
        workbook = load_workbook(workbook_path, read_only=True, data_only=True)
        try:
            for sheet in workbook.worksheets:
                if sheet.title == RENT_SHEET_NAME or sheet.max_row < 2:
                    continue
                headers = _normalized_headers(sheet)
                if not headers:
                    continue
                for row_index, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
                    row = {name: values[index - 1] if index <= len(values) else "" for name, index in headers.items()}
                    if not _is_lease_contract_row(row):
                        continue
                    source_name = str(row.get("source_file_name") or row.get("file_name") or "").strip()
                    source_path = _source_path(row, filename_index)
                    digest = str(row.get("sha256") or "").strip()
                    identity = digest or _path_identity(source_path, source_name)
                    if identity and identity not in contracts:
                        contracts[identity] = WorkbookContract(sheet.title, row_index, source_name or (source_path.name if source_path else ""), source_path, digest)
        finally:
            workbook.close()
    return list(contracts.values())


def _normalized_headers(sheet) -> dict[str, int]:
    return {
        _header_key(cell.value): index
        for index, cell in enumerate(next(sheet.iter_rows(min_row=1, max_row=1, values_only=False)), start=1)
        if cell.value is not None and _header_key(cell.value)
    }


def _header_key(value: object) -> str:
    key = _plain(str(value)).replace(" ", "_")
    aliases = {
        "caminho_do_ficheiro": "source_file_path",
        "caminho_ficheiro": "source_file_path",
        "caminho_do_arquivo": "source_file_path",
        "file_path": "source_file_path",
        "documento_tipo": "document_type",
        "tipo_de_documento": "document_type",
        "tipo_documento": "document_type",
        "categoria_documento": "document_category",
    }
    return aliases.get(key, key)


def _is_lease_contract_row(row: dict[str, object]) -> bool:
    category = _plain(str(row.get("document_category") or ""))
    type_values = " ".join(
        str(row.get(name) or "")
        for name in ("document_type", "document_subtype", "contract_type")
    )
    document_type = _plain(type_values)
    return category in {"lease contract", "lease_contract"} or any(
        marker in document_type
        for marker in ("arrendamento", "lease contract", "lease_contract", "contrato de locacao")
    )


def _source_path(row: dict[str, object], filename_index: dict[str, list[Path]]) -> Path | None:
    for field in ("copied_file_path", "source_file_path", "path"):
        value = str(row.get(field) or "").strip()
        if value:
            candidate = Path(value).expanduser()
            if candidate.is_file():
                return candidate
    source_name = str(row.get("source_file_name") or row.get("file_name") or "").strip()
    matches = filename_index.get(source_name.casefold(), [])
    return matches[0] if len(matches) == 1 else None


def _input_filename_index(input_dir: Path) -> dict[str, list[Path]]:
    if not input_dir.exists():
        return {}
    index: dict[str, list[Path]] = {}
    for path in input_dir.rglob("*.pdf"):
        if path.is_file():
            index.setdefault(path.name.casefold(), []).append(path)
    return index


def _existing_hashes(workbook_path: Path) -> set[str]:
    if not workbook_path.exists():
        return set()
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("Missing dependency: install openpyxl with `pip install -r requirements.txt`.") from exc
    with _workbook_lock(workbook_path):
        workbook = load_workbook(workbook_path, read_only=True, data_only=True)
        try:
            if RENT_SHEET_NAME not in workbook.sheetnames:
                return set()
            sheet = workbook[RENT_SHEET_NAME]
            headers = _normalized_headers(sheet)
            column = headers.get("sha256")
            if not column:
                return set()
            return {str(row[column - 1]) for row in sheet.iter_rows(min_row=2, values_only=True) if row[column - 1]}
        finally:
            workbook.close()


def _upsert_result(workbook_path: Path, payload: dict[str, object]) -> None:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("Missing dependency: install openpyxl with `pip install -r requirements.txt`.") from exc
    with _workbook_lock(workbook_path):
        workbook = load_workbook(workbook_path)
        if RENT_SHEET_NAME in workbook.sheetnames:
            sheet = workbook[RENT_SHEET_NAME]
        else:
            sheet = workbook.create_sheet(RENT_SHEET_NAME)
            sheet.append(RENT_COLUMNS)
        _ensure_result_headers(sheet)
        headers = _normalized_headers(sheet)
        identity = str(payload.get("sha256") or "")
        matches = [
            row for row in range(2, sheet.max_row + 1)
            if str(sheet.cell(row=row, column=headers["sha256"]).value or "") == identity
        ]
        destination = matches[0] if matches else sheet.max_row + 1
        for column, name in enumerate(RENT_COLUMNS, start=1):
            sheet.cell(row=destination, column=column, value=payload.get(name, ""))
        for duplicate in reversed(matches[1:]):
            sheet.delete_rows(duplicate)
        _format_result_sheet(sheet)
        workbook.save(workbook_path)
        workbook.close()


def _ensure_result_headers(sheet) -> None:
    existing = [str(sheet.cell(row=1, column=index).value or "") for index in range(1, sheet.max_column + 1)]
    if tuple(existing[:len(RENT_COLUMNS)]) == RENT_COLUMNS:
        return
    # v1 is a prefix of v2.  Append the new fields without invalidating prior
    # runs; an unrelated worksheet layout remains a deliberate hard error.
    if tuple(existing) == LEGACY_RENT_COLUMNS or tuple(existing) == RENT_COLUMNS[:len(existing)]:
        for column, name in enumerate(RENT_COLUMNS, start=1):
            sheet.cell(row=1, column=column, value=name)
        return
    if sheet.max_row <= 1:
        for column, name in enumerate(RENT_COLUMNS, start=1): sheet.cell(row=1, column=column, value=name)
        return
    raise RuntimeError(f"Worksheet '{RENT_SHEET_NAME}' has an incompatible layout.")


def _format_result_sheet(sheet) -> None:
    sheet.freeze_panes = "A2"
    widths = {
        "source_file_name": 36, "source_file_path": 54, "sha256": 66,
        "annual_rent_evidence": 72, "reservation_title_evidence": 72,
        "extraction_notes": 60, "text_source": 28, "status": 26,
    }
    for index, name in enumerate(RENT_COLUMNS, start=1):
        if name in widths:
            sheet.column_dimensions[_column_letter(index)].width = widths[name]
    for name in ("annual_rent_eur", "reservation_title_percent"):
        column = RENT_COLUMNS.index(name) + 1
        for row in range(2, sheet.max_row + 1):
            sheet.cell(row=row, column=column).number_format = "0.00"


def _decimal_for_excel(value: Decimal | None) -> float | str:
    return float(value) if value is not None else ""


def _path_identity(path: Path | None, source_name: str) -> str:
    if path and path.is_file():
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    return f"unresolved::{source_name.casefold()}"


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _plain(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return " ".join("".join(character for character in normalized if not unicodedata.combining(character)).casefold().split())
