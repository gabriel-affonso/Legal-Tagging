from doc_register.models import ExtractionResult
from doc_register.validators.validation_rules import run_all_validations
from doc_register.validators import validate_result


result = ExtractionResult(
    document_category="lease_contract",
    lessor="António Lopes Telo",
    lessee="GESTO ENERGIA, S.A.",
    signed_date="2024-05-31",
    property_article="154",
    property_section="C",
    monthly_rent="500,00 EUR",
    confidence="high",
)

issues = run_all_validations(result)

print("Issues:")
for issue in issues:
    print(f"- {issue}")

validated = validate_result(
    result,
    signals=None,
    document_text="",
)

print()
print("Quality score:", validated.quality_score)
print("Quality band:", validated.quality_band)
print("Validation status:", validated.validation_status)
print("Review priority:", validated.review_priority)
print("Needs review:", validated.needs_review)
print("Validation issues:", validated.validation_issues)
print("Review reason:", validated.review_reason)