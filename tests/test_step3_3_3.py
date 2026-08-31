from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from doc_register.ai_reviewer.integration import review_if_needed
from doc_register.ai_reviewer.models import AIFieldProposal
from doc_register.config import AppConfig
from doc_register.models import ExtractionResult
from doc_register.validators.contract_final_resolution import (
    apply_contract_final_resolution,
    prepare_contract_final_resolution,
)
from doc_register.validators.validator import validate_result


def _config() -> AppConfig:
    return AppConfig(
        input_dir=Path("."), processing_dir=Path("."), archive_dir=Path("."),
        error_dir=Path("."), log_dir=Path("."), excel_path=Path("register.xlsx"),
        ollama_url="http://localhost:11434", ollama_model="test-model",
        ollama_timeout_seconds=1, llm_classification_words=500,
        llm_extraction_max_chars=8000, poll_interval_seconds=300,
        minimum_file_age_seconds=0, ocr_enabled=False, ocr_language="por+eng",
        ocr_min_text_chars=600, ocr_timeout_seconds=1, ocr_dir=Path("."),
        pdf_layout_extraction_enabled=True, pdf_layout_min_quality_score=35,
        max_pdf_pages=12, max_text_chars=24000, copy_only_recent_minutes=0,
        ai_review_enabled=True, ai_review_timeout_seconds=1,
    )


def test_ownership_roles_and_conflict_are_preserved_in_final_audit() -> None:
    text = """[Page 1]
    CONTRATO DE ARRENDAMENTO
    Ana Maria Lopes, todos na qualidade de Senhorios e legítimos proprietários.
    GESTO ENERGIA, S.A., NIPC 508567475, de ora em diante designada por Arrendatária.
    [Page 9]
    CADERNETA PREDIAL RÚSTICA
    [Page 10]
    IDENTIFICAÇÃO DO PRÉDIO
    ARTIGO MATRICIAL: 140
    SECÇÃO: J
    NOME/LOCALIZAÇÃO PRÉDIO: Aquaxais
    ELEMENTOS DO PRÉDIO
    ÁREA TOTAL (HA): 4,431200
    [Page 11]
    TITULARES
    Nome: Maria Diferente
    Morada: Rua da Fonte
    Tipo de titular: Propriedade plena
    """
    result = apply_contract_final_resolution(
        ExtractionResult(document_category="lease_contract"),
        prepare_contract_final_resolution(text),
    )

    audit = result.raw_json["step2_7_final_resolution"]
    ownership = audit["ownership_evidence"]
    assert ownership["contract_lessors"] == ["Ana Maria Lopes"]
    assert ownership["declared_owners"] == ["Ana Maria Lopes"]
    assert ownership["cadastral_owners"] == ["Maria Diferente"]
    assert any(item["type"] == "ownership_source_conflict" for item in audit["conflicts"])
    assert result.owner_name == "Maria Diferente"
    assert result.human_review_required == "yes"


def test_annual_rent_has_an_explicit_non_monthly_final_decision() -> None:
    text = """CONTRATO DE ARRENDAMENTO
    GESTO ENERGIA, S.A., NIPC 508567475, de ora em diante designada por Arrendatária.
    Cláusula 5 - A renda é de 1.000,00 EUR por hectare por ano.
    """
    result = apply_contract_final_resolution(
        ExtractionResult(document_category="lease_contract"),
        prepare_contract_final_resolution(text),
    )
    result = validate_result(result, document_text=text, recover=False, entity_resolution=False)

    decision = result.raw_json["step2_7_final_resolution"]["field_decisions"]["monthly_rent"]
    assert decision["decision"] == "not_applicable"
    assert result.monthly_rent == ""
    assert "missing_monthly_rent" not in result.validation_issues


def test_ai_review_keeps_party_result_when_property_group_times_out() -> None:
    text = """CONTRATO DE ARRENDAMENTO
    Entre Ana Maria Lopes, na qualidade de Senhoria, e GESTO ENERGIA, S.A.
    """
    result = ExtractionResult(
        document_category="lease_contract",
        lessor="Senhorios",
        validation_status="NEEDS_REVIEW",
        validation_issues="generic_lessor; missing_property_article",
        review_reason="generic_lessor; missing_property_article",
    )

    def review_side_effect(**kwargs):
        fields = kwargs["request"].fields
        if "property_article" in fields:
            raise TimeoutError("property timeout")
        return [AIFieldProposal(
            field_name="lessor", proposed_value="Ana Maria Lopes",
            evidence="Entre Ana Maria Lopes, na qualidade de Senhoria", confidence=0.95,
        )]

    with patch("doc_register.ai_reviewer.integration.request_ai_review", side_effect=review_side_effect):
        reviewed = review_if_needed(result, config=_config(), file_name="PR098.pdf", document_text=text)

    assert reviewed.lessor == "Ana Maria Lopes"
    assert reviewed.ai_review_status == "PARTIALLY_RESOLVED"
    assert reviewed.raw_json["ai_review"]["group_failures"] == {"property_resolution": "property timeout"}


def load_tests(loader, tests, pattern):
    module = sys.modules[__name__]
    functions = [
        getattr(module, name)
        for name in sorted(dir(module))
        if name.startswith("test_") and callable(getattr(module, name))
    ]
    return unittest.TestSuite(unittest.FunctionTestCase(function) for function in functions)
