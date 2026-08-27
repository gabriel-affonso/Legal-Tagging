from __future__ import annotations

from pathlib import Path
import os
import unittest
from unittest.mock import patch

from doc_register.ai_reviewer.integration import review_if_needed
from doc_register.ai_reviewer.models import AIFieldProposal, AIReviewRequest
from doc_register.ai_reviewer.proposal_validator import validate_proposal
from doc_register.ai_reviewer.reviewer import request_ai_review
from doc_register.contract_types import apply_contract_type_hints
from doc_register.config import AppConfig
from doc_register.models import ExtractionResult
from doc_register.schemas import REGISTER_COLUMNS
from doc_register.validators.validation_rules import requires_monthly_rent
from doc_register.validators.validator import validate_result


def _config() -> AppConfig:
    return AppConfig(
        input_dir=Path("."),
        processing_dir=Path("."),
        archive_dir=Path("."),
        error_dir=Path("."),
        log_dir=Path("."),
        excel_path=Path("register.xlsx"),
        ollama_url="http://localhost:11434",
        ollama_model="test-model",
        ollama_timeout_seconds=1,
        llm_classification_words=500,
        llm_extraction_max_chars=8000,
        poll_interval_seconds=300,
        minimum_file_age_seconds=0,
        ocr_enabled=False,
        ocr_language="por+eng",
        ocr_min_text_chars=600,
        ocr_timeout_seconds=1,
        ocr_dir=Path("."),
        max_pdf_pages=12,
        max_text_chars=24000,
        copy_only_recent_minutes=0,
        ai_review_enabled=True,
        ai_review_timeout_seconds=1,
    )


class AIReviewerTests(unittest.TestCase):
    def test_monthly_rent_not_required_for_purchase_option(self) -> None:
        result = ExtractionResult(
            document_category="lease_contract",
            document_subtype="opcao de compra",
        )

        self.assertFalse(requires_monthly_rent(result))
        self.assertNotIn("missing_monthly_rent", validate_result(result).validation_issues)

    def test_monthly_rent_not_required_for_cpcv_or_assignment(self) -> None:
        cpcv = ExtractionResult(
            document_category="lease_contract",
            document_subtype="contrato_promessa_compra_venda",
        )
        assignment = ExtractionResult(
            document_category="lease_contract",
            contract_type="acordo_cedencia_posicao_contratual",
        )

        self.assertFalse(requires_monthly_rent(cpcv))
        self.assertFalse(requires_monthly_rent(assignment))
        self.assertNotIn("missing_monthly_rent", validate_result(cpcv).validation_issues)
        self.assertNotIn("missing_monthly_rent", validate_result(assignment).validation_issues)

    def test_contract_type_hints_canonicalize_cpcv_and_assignment(self) -> None:
        cpcv = ExtractionResult(document_category="other", document_type="Contrato")
        assignment = ExtractionResult(document_category="", document_type="Acordo")

        apply_contract_type_hints(
            cpcv,
            "Contrato-Promessa de Compra e Venda entre promitente vendedor e comprador",
        )
        apply_contract_type_hints(
            assignment,
            "Acordo de Cedencia de Posicao Contratual entre cedente e cessionario",
        )

        self.assertEqual(cpcv.document_category, "lease_contract")
        self.assertEqual(cpcv.document_type, "Contrato Promessa de Compra e Venda")
        self.assertEqual(cpcv.document_subtype, "contrato_promessa_compra_venda")
        self.assertEqual(assignment.document_category, "lease_contract")
        self.assertEqual(assignment.document_type, "Acordo de Cedencia de Posicao Contratual")
        self.assertEqual(assignment.contract_type, "acordo_cedencia_posicao_contratual")

    def test_contract_price_fields_are_in_register_columns(self) -> None:
        self.assertIn("option_price", REGISTER_COLUMNS)
        self.assertIn("purchase_price", REGISTER_COLUMNS)
        self.assertIn("assignment_price", REGISTER_COLUMNS)

    def test_party_proposal_requires_document_support(self) -> None:
        text = "Entre Ana Maria Lopes, na qualidade de Senhoria, e GESTO ENERGIA, S.A."
        accepted = validate_proposal(
            AIFieldProposal(
                field_name="lessor",
                proposed_value="Ana Maria Lopes",
                evidence="Entre Ana Maria Lopes, na qualidade de Senhoria",
                confidence=0.94,
            ),
            ExtractionResult(document_category="lease_contract", lessor="Senhorios"),
            document_text=text,
            evidence_text=text,
        )
        rejected = validate_proposal(
            AIFieldProposal(
                field_name="lessor",
                proposed_value="Maria Inventada",
                evidence="Entre Ana Maria Lopes, na qualidade de Senhoria",
                confidence=0.99,
            ),
            ExtractionResult(document_category="lease_contract", lessor="Senhorios"),
            document_text=text,
            evidence_text=text,
        )

        self.assertEqual(accepted.decision, "AUTO_ACCEPTED")
        self.assertEqual(rejected.decision, "REJECTED")

    def test_review_if_needed_applies_only_auto_accepted_fields(self) -> None:
        text = (
            "CONTRATO DE ARRENDAMENTO\n"
            "Entre Ana Maria Lopes, na qualidade de Senhoria, e GESTO ENERGIA, S.A.\n"
            "O predio esta inscrito no artigo 154, seccao C.\n"
            "A renda mensal e de 700,00 EUR. Assinado em 20/05/2024."
        )
        result = ExtractionResult(
            document_category="lease_contract",
            document_type="contrato de arrendamento",
            lessor="Senhorios",
            lessee="GESTO ENERGIA, S.A.",
            signed_date="2024-05-20",
            property_article="154",
            property_section="C",
            monthly_rent="700,00 EUR",
            confidence="high",
        )
        result = validate_result(result)
        result.lessor = "Senhorios"
        result.validation_status = "NEEDS_REVIEW"
        result.validation_issues = "generic_lessor"
        result.review_reason = "generic_lessor"

        with patch(
            "doc_register.ai_reviewer.integration.request_ai_review",
            return_value=[
                AIFieldProposal(
                    field_name="lessor",
                    proposed_value="Ana Maria Lopes",
                    evidence="Entre Ana Maria Lopes, na qualidade de Senhoria",
                    confidence=0.95,
                )
            ],
        ):
            reviewed = review_if_needed(
                result,
                config=_config(),
                file_name="contrato.pdf",
                document_text=text,
            )

        self.assertEqual(reviewed.lessor, "Ana Maria Lopes")
        self.assertEqual(reviewed.ai_review_status, "RESOLVED")
        self.assertEqual(reviewed.ai_accepted_fields, "lessor")
        self.assertEqual(reviewed.raw_json["ai_review"]["accepted_fields"], ["lessor"])

    def test_final_validation_clears_resolved_machine_issues(self) -> None:
        result = ExtractionResult(
            document_category="lease_contract",
            document_type="contrato de arrendamento",
            lessor="Ana Maria Lopes",
            lessee="GESTO ENERGIA, S.A.",
            signed_date="2024-05-20",
            property_article="154",
            property_section="C",
            monthly_rent="700,00 EUR",
            confidence="high",
            needs_review="yes",
            review_reason="generic_lessor",
            human_review_required="no",
        )

        validated = validate_result(result, recover=False)

        self.assertEqual(validated.validation_status, "AUTO_APPROVED")
        self.assertEqual(validated.needs_review, "no")
        self.assertEqual(validated.validation_issues, "")
        self.assertEqual(validated.review_reason, "")

    def test_recover_false_does_not_mutate_fields(self) -> None:
        text = (
            "CONTRATO DE ARRENDAMENTO\n"
            "Senhorios: Ana Maria Lopes\n"
            "GESTO ENERGIA, S.A.\n"
            "artigo 154, seccao C. renda mensal 700,00 EUR. assinado em 20/05/2024."
        )
        result = ExtractionResult(
            document_category="lease_contract",
            document_type="contrato de arrendamento",
            lessor="Senhorios",
            lessee="GESTO ENERGIA, S.A.",
            signed_date="2024-05-20",
            property_article="154",
            property_section="C",
            monthly_rent="700,00 EUR",
            confidence="high",
        )

        validated = validate_result(result, document_text=text, recover=False)

        self.assertEqual(validated.lessor, "Senhorios")
        self.assertIn("generic_lessor", validated.validation_issues)

    def test_human_confidence_proposal_is_recorded_but_not_applied(self) -> None:
        text = (
            "CONTRATO DE ARRENDAMENTO\n"
            "Entre Ana Maria Lopes, na qualidade de Senhoria, e GESTO ENERGIA, S.A.\n"
            "O predio esta inscrito no artigo 154, seccao C.\n"
            "A renda mensal e de 700,00 EUR. Assinado em 20/05/2024."
        )
        result = ExtractionResult(
            document_category="lease_contract",
            document_type="contrato de arrendamento",
            lessor="Senhorios",
            lessee="GESTO ENERGIA, S.A.",
            signed_date="2024-05-20",
            property_article="154",
            property_section="C",
            monthly_rent="700,00 EUR",
            confidence="high",
            validation_status="NEEDS_REVIEW",
            validation_issues="generic_lessor",
            review_reason="generic_lessor",
        )

        with patch(
            "doc_register.ai_reviewer.integration.request_ai_review",
            return_value=[
                AIFieldProposal(
                    field_name="lessor",
                    proposed_value="Ana Maria Lopes",
                    evidence="Entre Ana Maria Lopes, na qualidade de Senhoria",
                    confidence=0.82,
                )
            ],
        ):
            reviewed = review_if_needed(
                result,
                config=_config(),
                file_name="contrato.pdf",
                document_text=text,
            )

        self.assertEqual(reviewed.lessor, "Senhorios")
        self.assertEqual(reviewed.human_review_required, "yes")
        self.assertIn("lessor", reviewed.ai_rejected_fields)
        self.assertEqual(
            reviewed.raw_json["ai_review"]["field_decisions"][0]["decision"],
            "AI_PROPOSED_HUMAN_REQUIRED",
        )
        self.assertEqual(
            reviewed.raw_json["ai_review"]["field_decisions"][0]["proposed_value"],
            "Ana Maria Lopes",
        )

    def test_technical_statuses_are_explicit(self) -> None:
        needs_ocr = validate_result(
            ExtractionResult(
                document_category="lease_contract",
                text_source="native_pdf_text",
                native_text_chars="0",
                ocr_text_chars="0",
            ),
            recover=False,
        )
        failed = validate_result(
            ExtractionResult(
                document_category="lease_contract",
                text_source="native_pdf_text",
                native_text_chars="10",
                ocr_text_chars="0",
                extraction_notes="OCR indisponivel; processamento continuou com o texto nativo.",
            ),
            recover=False,
        )
        error = validate_result(
            ExtractionResult(processing_status="error"),
            recover=False,
        )

        self.assertEqual(needs_ocr.validation_status, "NEEDS_OCR")
        self.assertEqual(failed.validation_status, "OCR_FAILED")
        self.assertEqual(error.validation_status, "TECHNICAL_ERROR")

    def test_step2_sequence_can_auto_approve_after_final_validation(self) -> None:
        text = (
            "CONTRATO DE ARRENDAMENTO\n"
            "Entre Ana Maria Lopes, na qualidade de Senhoria, e GESTO ENERGIA, S.A.\n"
            "O predio esta inscrito no artigo 154, seccao C.\n"
            "A renda mensal e de 700,00 EUR. Assinado em 20/05/2024."
        )
        result = ExtractionResult(
            document_category="lease_contract",
            document_type="contrato de arrendamento",
            lessor="Senhorios",
            lessee="GESTO ENERGIA, S.A.",
            signed_date="2024-05-20",
            property_article="154",
            property_section="C",
            monthly_rent="700,00 EUR",
            confidence="high",
        )
        initially_validated = validate_result(result, document_text=text, recover=False)
        self.assertEqual(initially_validated.validation_status, "NEEDS_REVIEW")

        with patch(
            "doc_register.ai_reviewer.integration.request_ai_review",
            return_value=[
                AIFieldProposal(
                    field_name="lessor",
                    proposed_value="Ana Maria Lopes",
                    evidence="Entre Ana Maria Lopes, na qualidade de Senhoria",
                    confidence=0.95,
                )
            ],
        ):
            reviewed = review_if_needed(
                initially_validated,
                config=_config(),
                file_name="contrato.pdf",
                document_text=text,
            )

        finally_validated = validate_result(reviewed, document_text=text, recover=False)

        self.assertEqual(finally_validated.validation_status, "AUTO_APPROVED")
        self.assertEqual(finally_validated.review_reason, "")
        self.assertEqual(finally_validated.ai_review_status, "RESOLVED")

    @unittest.skipUnless(
        os.environ.get("DOC_REGISTER_RUN_OLLAMA_SMOKE") == "1",
        "Set DOC_REGISTER_RUN_OLLAMA_SMOKE=1 to run the local Ollama smoke test.",
    )
    def test_optional_local_ollama_smoke_review(self) -> None:
        text = (
            "CONTRATO DE ARRENDAMENTO\n"
            "Entre Ana Maria Lopes, na qualidade de Senhoria, e GESTO ENERGIA, S.A.\n"
            "Assinado em 20/05/2024."
        )
        request = AIReviewRequest(
            result=ExtractionResult(document_category="lease_contract", lessor="Senhorios"),
            file_name="contrato.pdf",
            fields=["lessor"],
            issues=["generic_lessor"],
            evidence_text=text,
        )

        proposals = request_ai_review(
            base_url=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
            model=os.environ.get("OLLAMA_MODEL", "qwen3:8b"),
            request=request,
            timeout_seconds=30,
        )

        self.assertTrue(proposals)
        self.assertEqual(proposals[0].field_name, "lessor")


if __name__ == "__main__":
    unittest.main()
