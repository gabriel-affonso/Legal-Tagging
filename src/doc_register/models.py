from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .schemas import REGISTER_COLUMNS


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
    confidence: str = ""
    extraction_notes: str = ""
    signed_date: str = ""
    contract_type: str = ""
    lessor: str = ""
    lessor_2: str = ""
    lessee: str = ""
    lessee_tax_id: str = ""
    property_name: str = ""
    property_number: str = ""
    property_display_name: str = ""
    property_article: str = ""
    property_section: str = ""
    property_parish: str = ""
    property_municipality: str = ""
    property_district: str = ""
    property_location: str = ""
    property_address: str = ""
    owner_name: str = ""
    owner_tax_id: str = ""
    owner_address: str = ""
    contract_start_date: str = ""
    contract_end_date: str = ""
    rent_payment_day: str = ""
    monthly_rent: str = ""
    currency: str = ""
    option_price: str = ""
    purchase_price: str = ""
    assignment_price: str = ""
    bank_account_holder: str = ""
    iban: str = ""
    nib: str = ""
    bic_swift: str = ""
    bank_name: str = ""
    bank_account_number: str = ""
    payment_date: str = ""
    payer: str = ""
    payee: str = ""
    payment_amount: str = ""
    payment_method: str = ""
    payment_reference: str = ""
    payment_description: str = ""
    text_source: str = ""
    native_text_chars: str = ""
    ocr_text_chars: str = ""
    ocr_quality_score: str = ""
    ocr_quality_flags: str = ""
    quality_score: str = ""
    quality_band: str = ""
    validation_status: str = ""
    validation_issues: str = ""
    review_priority: str = ""
    ai_review_status: str = ""
    ai_reviewed_fields: str = ""
    ai_accepted_fields: str = ""
    ai_rejected_fields: str = ""
    ai_review_confidence: str = ""
    ai_review_model: str = ""
    ai_review_duration_seconds: str = ""
    ai_review_reason: str = ""
    human_review_required: str = ""
    deterministic_json: dict[str, Any] = field(default_factory=dict)
    llm_json: dict[str, Any] = field(default_factory=dict)
    raw_json: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractedText:
    text: str
    source: str
    native_text_chars: int
    ocr_text_chars: int = 0
    notes: str = ""
