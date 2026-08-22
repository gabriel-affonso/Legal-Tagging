from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PdfCandidate:
    source_path: Path
    copied_path: Path
    sha256: str
    created_at: datetime
    modified_at: datetime


@dataclass
class ExtractionResult:
    processing_status: str = ""
    processed_ok: str = ""
    needs_review: str = ""
    review_reason: str = ""
    reviewed_by: str = ""
    reviewed_at: str = ""
    error_message: str = ""
    document_category: str = ""
    document_type: str = ""
    document_subtype: str = ""
    document_date: str = ""
    summary: str = ""
    language: str = ""
    signed_date: str = ""
    contract_type: str = ""
    lessor: str = ""
    lessee: str = ""
    property_address: str = ""
    property_name: str = ""
    property_number: str = ""
    property_display_name: str = ""
    contract_start_date: str = ""
    contract_end_date: str = ""
    rent_payment_day: str = ""
    monthly_rent: str = ""
    currency: str = ""
    payment_date: str = ""
    payer: str = ""
    payee: str = ""
    payment_amount: str = ""
    payment_method: str = ""
    payment_reference: str = ""
    payment_description: str = ""
    confidence: str = ""
    extraction_notes: str = ""
    text_source: str = ""
    native_text_chars: str = ""
    ocr_text_chars: str = ""
    raw_json: dict[str, Any] = field(default_factory=dict)


REGISTER_COLUMNS = [
    "processed_at",
    "source_file_name",
    "copied_file_path",
    "sha256",
    "file_created_at",
    "file_modified_at",
    "processing_status",
    "processed_ok",
    "needs_review",
    "review_reason",
    "reviewed_by",
    "reviewed_at",
    "error_message",
    "document_category",
    "document_type",
    "document_subtype",
    "document_date",
    "summary",
    "language",
    "signed_date",
    "contract_type",
    "lessor",
    "lessee",
    "property_address",
    "property_name",
    "property_number",
    "property_display_name",
    "contract_start_date",
    "contract_end_date",
    "rent_payment_day",
    "monthly_rent",
    "currency",
    "payment_date",
    "payer",
    "payee",
    "payment_amount",
    "payment_method",
    "payment_reference",
    "payment_description",
    "confidence",
    "extraction_notes",
    "text_source",
    "native_text_chars",
    "ocr_text_chars",
    "raw_json",
]


@dataclass(frozen=True)
class ExtractedText:
    text: str
    source: str
    native_text_chars: int
    ocr_text_chars: int = 0
    notes: str = ""
