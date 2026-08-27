from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from doc_register.ai_reviewer.integration import review_if_needed
from doc_register.ai_reviewer.models import AIFieldProposal
from doc_register.config import AppConfig
from doc_register.detectors import DeterministicSignals
from doc_register.models import ExtractionResult
from doc_register.validators.iban_recovery import recover_iban_fields
from doc_register.validators.ocr_quality import analyze_ocr_quality
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
        pdf_layout_extraction_enabled=True,
        pdf_layout_min_quality_score=35,
        max_pdf_pages=12,
        max_text_chars=24000,
        copy_only_recent_minutes=0,
        ai_review_enabled=True,
        ai_review_timeout_seconds=1,
    )


class Step22Tests(unittest.TestCase):
    def test_corrupted_party_name_is_not_valid(self) -> None:
        result = ExtractionResult(
            document_category="lease_contract",
            document_type="contrato de arrendamento",
            lessor='1”, e 5 g o x natural de g',
            lessee="GESTO ENERGIA, S.A.",
            signed_date="2024-05-20",
            property_article="154",
            property_section="C",
            monthly_rent="700,00 EUR",
            confidence="high",
        )

        validated = validate_result(result, recover=False)

        self.assertIn("invalid_lessor_format", validated.validation_issues)
        self.assertIn("ocr_corrupted_party_name", validated.validation_issues)
        self.assertEqual(validated.validation_status, "NEEDS_REVIEW")

    def test_corrupted_lessor_can_be_recovered_from_filename_candidate(self) -> None:
        text = (
            "CONTRATO DE ARRENDAMENTO\n"
            "Entre Felisbina Maria Lopes Mendes e Augusto Maria Mendes, na qualidade de Senhorios,\n"
            "e GESTO ENERGIA, S.A., na qualidade de Arrendataria.\n"
            "O predio esta inscrito no artigo 440, seccao J.\n"
            "A renda mensal e de 500,00 EUR. Assinado em 31/05/2024."
        )
        result = ExtractionResult(
            document_category="lease_contract",
            document_type="contrato de arrendamento",
            lessor='1”, e 5 g o x natural de g',
            lessee="GESTO ENERGIA, S.A.",
            confidence="high",
        )

        validated = validate_result(
            result,
            document_text=text,
            file_name="PR098_CA_Felisbina Maria Lopes Mendes e Augusto Maria Mendes_SP18492.pdf",
        )

        self.assertEqual(validated.lessor, "Felisbina Maria Lopes Mendes; Augusto Maria Mendes")
        self.assertNotIn("invalid_lessor_format", validated.validation_issues)

    def test_iban_recovery_repairs_ocr_characters_with_checksum(self) -> None:
        result = ExtractionResult(
            document_category="bank_details",
            iban="2 1500055047 700007",
        )
        text = "Titular: Jose Maria Leite\nIBAN PT5O OOO2 O123 1234 5678 9O15 4"

        recovered, report = recover_iban_fields(result, document_text=text)

        self.assertEqual(recovered.iban, "PT50000201231234567890154")
        self.assertEqual(recovered.nib, "000201231234567890154")
        self.assertTrue(report.changes)

    def test_validate_result_runs_iban_recovery_before_validation(self) -> None:
        result = ExtractionResult(
            document_category="bank_details",
            iban="PT5O OOO2 O123 1234 5678 9O15 4",
            bank_account_holder="Jose Maria Leite",
            confidence="high",
        )

        validated = validate_result(
            result,
            document_text="Titular: Jose Maria Leite\nIBAN PT5O OOO2 O123 1234 5678 9O15 4",
        )

        self.assertEqual(validated.iban, "PT50000201231234567890154")
        self.assertNotIn("invalid_iban_format", validated.validation_issues)

    def test_iban_recovery_rejects_incomplete_candidate(self) -> None:
        result = ExtractionResult(
            document_category="bank_details",
            iban="2 1500055047 700007",
        )
        text = "IBAN 2 1500055047 700007"

        recovered, report = recover_iban_fields(result, document_text=text)

        self.assertEqual(recovered.iban, "2 1500055047 700007")
        self.assertFalse(report.changes)
        self.assertEqual(report.reason, "no_unique_valid_candidate")

    def test_owner_name_is_eligible_for_ai_review_and_can_be_accepted(self) -> None:
        text = (
            "CADERNETA PREDIAL\n"
            "Titular: Antonio Augusto Mendes\n"
            "Predio rustico inscrito no artigo 140, seccao J."
        )
        result = ExtractionResult(
            document_category="property_document",
            property_article="140",
            property_section="J",
            owner_name="Senhorios",
            confidence="high",
        )
        result = validate_result(result, recover=False)

        with patch(
            "doc_register.ai_reviewer.integration.request_ai_review",
            return_value=[
                AIFieldProposal(
                    field_name="owner_name",
                    proposed_value="Antonio Augusto Mendes",
                    evidence="Titular: Antonio Augusto Mendes",
                    confidence=0.95,
                )
            ],
        ):
            reviewed = review_if_needed(
                result,
                config=_config(),
                file_name="caderneta.pdf",
                document_text=text,
            )

        self.assertEqual(reviewed.owner_name, "Antonio Augusto Mendes")
        self.assertEqual(reviewed.ai_accepted_fields, "owner_name")

    def test_owner_name_is_recovered_from_label_before_ai_review(self) -> None:
        result = ExtractionResult(
            document_category="property_document",
            property_article="140",
            property_section="J",
            owner_name="Senhorios",
            confidence="high",
        )
        text = "CADERNETA PREDIAL\nSujeito Passivo: Antonio Augusto Mendes\nArtigo 140, seccao J"

        validated = validate_result(result, document_text=text)

        self.assertEqual(validated.owner_name, "Antonio Augusto Mendes")
        self.assertNotIn("generic_owner_name", validated.validation_issues)

    def test_bank_account_holder_is_recovered_from_label(self) -> None:
        result = ExtractionResult(
            document_category="bank_details",
            iban="PT50000201231234567890154",
            bank_account_holder="Titular",
            confidence="high",
        )
        text = "Comprovativo de IBAN\nTitular da conta: Jose Maria Leite\nIBAN PT50 0002 0123 1234 5678 9015 4"

        validated = validate_result(result, document_text=text)

        self.assertEqual(validated.bank_account_holder, "Jose Maria Leite")
        self.assertNotIn("generic_bank_account_holder", validated.validation_issues)

    def test_signal_conflicts_are_reported(self) -> None:
        signals = DeterministicSignals(property_article="140", property_section="J")
        result = ExtractionResult(
            document_category="property_document",
            property_article="1945",
            property_section="K",
            owner_name="Antonio Augusto Mendes",
            confidence="high",
        )

        validated = validate_result(result, signals=signals, recover=False)

        self.assertIn("property_article_conflicts_with_deterministic", validated.validation_issues)
        self.assertIn("property_section_conflicts_with_deterministic", validated.validation_issues)

    def test_ocr_quality_flags_degraded_text(self) -> None:
        text = "1 # @ x y z\n" * 40

        report = analyze_ocr_quality(text)
        validated = validate_result(
            ExtractionResult(document_category="other", confidence="high"),
            document_text=text,
            recover=False,
        )

        self.assertIn("ocr_quality_low", report.flags)
        self.assertIn("ocr_quality_low", validated.validation_issues)


if __name__ == "__main__":
    unittest.main()
