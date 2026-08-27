from __future__ import annotations


OFFICIAL_CATEGORIES = {
    "lease_contract": "Contrato de arrendamento.",
    "property_document": "Caderneta, certidao, CRP, registo predial, matriz ou averbamento.",
    "bank_details": "Dados bancarios com IBAN, NIB, BIC/SWIFT, titular ou conta, sem prova clara de pagamento.",
    "payment_proof": "Comprovativo de transferencia ou pagamento com data, pagador, beneficiario e valor.",
    "invoice_or_receipt": "Fatura ou recibo que nao seja claramente comprovativo bancario.",
    "identity_document": "Documento de identificacao pessoal ou empresarial.",
    "tax_document": "Documento fiscal.",
    "correspondence": "Carta, notificacao ou comunicacao.",
    "other": "Fallback local.",
}


GENERAL_FIELDS = [
    "document_category",
    "document_type",
    "document_subtype",
    "document_date",
    "summary",
    "language",
    "confidence",
    "extraction_notes",
]

LEASE_FIELDS = [
    "signed_date",
    "contract_type",
    "lessor",
    "lessee",
    "contract_start_date",
    "contract_end_date",
    "rent_payment_day",
    "monthly_rent",
    "currency",
]

PROPERTY_FIELDS = [
    "property_name",
    "property_number",
    "property_display_name",
    "property_article",
    "property_section",
    "property_parish",
    "property_municipality",
    "property_district",
    "property_location",
    "property_address",
    "owner_name",
    "owner_tax_id",
    "owner_address",
]

BANK_FIELDS = [
    "bank_account_holder",
    "iban",
    "nib",
    "bic_swift",
    "bank_name",
    "bank_account_number",
]

PAYMENT_FIELDS = [
    "payment_date",
    "payer",
    "payee",
    "payment_amount",
    "payment_method",
    "payment_reference",
    "payment_description",
]

TECHNICAL_FIELDS = [
    "text_source",
    "native_text_chars",
    "ocr_text_chars",
    "ai_review_status",
    "ai_reviewed_fields",
    "ai_accepted_fields",
    "ai_rejected_fields",
    "ai_review_confidence",
    "ai_review_model",
    "ai_review_duration_seconds",
    "ai_review_reason",
    "human_review_required",
    "deterministic_json",
    "llm_json",
    "raw_json",
]

OPERATIONAL_FIELDS = [
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
]

REGISTER_COLUMNS = (
    OPERATIONAL_FIELDS
    + GENERAL_FIELDS
    + LEASE_FIELDS
    + PROPERTY_FIELDS
    + BANK_FIELDS
    + PAYMENT_FIELDS
    + TECHNICAL_FIELDS
)


CATEGORY_EXTRACTION_FIELDS = {
    "lease_contract": GENERAL_FIELDS + LEASE_FIELDS + PROPERTY_FIELDS,
    "property_document": GENERAL_FIELDS + PROPERTY_FIELDS,
    "bank_details": GENERAL_FIELDS + BANK_FIELDS + PROPERTY_FIELDS,
    "payment_proof": GENERAL_FIELDS + PAYMENT_FIELDS + BANK_FIELDS + PROPERTY_FIELDS,
    "invoice_or_receipt": GENERAL_FIELDS + PAYMENT_FIELDS + PROPERTY_FIELDS,
    "identity_document": GENERAL_FIELDS + ["owner_name", "owner_tax_id", "owner_address"],
    "tax_document": GENERAL_FIELDS + PAYMENT_FIELDS + ["owner_name", "owner_tax_id"],
    "correspondence": GENERAL_FIELDS + PROPERTY_FIELDS,
    "other": GENERAL_FIELDS + PROPERTY_FIELDS + BANK_FIELDS + PAYMENT_FIELDS,
}


REQUIRED_FOR_REVIEW = {
    "lease_contract": ["lessor", "lessee", "property_display_name", "contract_start_date", "monthly_rent"],
    "payment_proof": ["payment_date", "payer", "payee", "payment_amount"],
}
