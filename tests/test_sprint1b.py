from doc_register.models import ExtractionResult
from doc_register.validators import recover_critical_fields, validate_result

def test_deterministic_recovery():
    text="""CONTRATO DE ARRENDAMENTO
Entre Felisbina Maria Lopes Mendes e Augusto Maria Mendes, na qualidade de Senhorios,
e GESTO ENERGIA, S.A., na qualidade de Arrendataria.
O predio esta inscrito no artigo 440, seccao J.
A renda mensal e de 500,00 EUR. Assinado em 31/05/2024."""
    result=ExtractionResult(document_category="lease_contract", lessor="Senhorios", lessee="Arrendataria", confidence="medium")
    result, report=recover_critical_fields(result, file_name="PR098_CA_Felisbina Maria Lopes Mendes e Augusto Maria Mendes_SP18492.pdf", document_text=text)
    assert result.lessor == "Felisbina Maria Lopes Mendes; Augusto Maria Mendes"
    assert result.lessee == "GESTO ENERGIA, S.A."
    assert result.property_article == "440"
    assert result.property_section == "J"
    assert result.monthly_rent == "500,00 EUR"
    assert result.signed_date == "2024-05-31"
    assert report.changes

def test_validation_after_recovery():
    text="""CONTRATO DE ARRENDAMENTO
Senhorios: Ana Maria Lopes
GESTO ENERGIA, S.A.
artigo 154, seccao C. renda mensal 700,00 EUR. assinado em 20/05/2024."""
    result=ExtractionResult(document_category="lease_contract", lessor="Senhorio", lessee="Arrendataria", confidence="high")
    validated=validate_result(result, document_text=text, file_name="VA154_CA_Ana Maria Lopes_SP00001.pdf")
    assert validated.validation_status == "AUTO_APPROVED"
    assert validated.quality_score == "100"
