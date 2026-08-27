from __future__ import annotations

import unittest

from doc_register.ai_reviewer.evidence_selector import select_evidence
from doc_register.ai_reviewer.models import AIFieldProposal
from doc_register.ai_reviewer.proposal_validator import validate_proposal
from doc_register.models import ExtractionResult
from doc_register.validators import validate_result
from doc_register.validators.property_recovery import recover_property_group


class Step23Tests(unittest.TestCase):
    def test_contract_structure_recovers_lessor_and_lessee_from_role_zones(self) -> None:
        text = (
            "CONTRATO DE ARRENDAMENTO\n"
            "Primeiro Outorgante: Ana Maria Lopes, NIF 123456789, residente em Braganca.\n"
            "Segundo Outorgante: GESTO ENERGIA, S.A., NIPC 509999999, com sede em Lisboa.\n"
            "O predio rustico esta inscrito na matriz sob o artigo 440, seccao J.\n"
            "Assinado em 31/05/2024."
        )
        result = ExtractionResult(
            document_category="lease_contract",
            lessor="Senhorios",
            lessee="Arrendatario",
            confidence="high",
        )

        validated = validate_result(result, document_text=text)

        self.assertEqual(validated.lessor, "Ana Maria Lopes")
        self.assertEqual(validated.lessee, "GESTO ENERGIA, S.A.")
        self.assertEqual(validated.property_article, "440")
        self.assertEqual(validated.property_section, "J")
        self.assertEqual(validated.signed_date, "2024-05-31")

    def test_filename_lessor_candidate_requires_lessor_zone_when_zones_exist(self) -> None:
        text = (
            "CONTRATO DE ARRENDAMENTO\n"
            "Primeiro Outorgante: Ana Maria Lopes, NIF 123456789.\n"
            "Segundo Outorgante: GESTO ENERGIA, S.A.\n"
            "O predio esta inscrito no artigo 154, seccao C.\n"
            "Assinado em 20/05/2024."
        )
        result = ExtractionResult(
            document_category="lease_contract",
            lessor="Senhorios",
            lessee="GESTO ENERGIA, S.A.",
            confidence="high",
        )

        validated = validate_result(
            result,
            document_text=text,
            file_name="PR001_CA_Maria Inventada_SP999.pdf",
        )

        self.assertEqual(validated.lessor, "Ana Maria Lopes")
        methods = [
            change["method"]
            for change in validated.raw_json["critical_recovery"]["changes"]
            if change["field"] == "lessor"
        ]
        self.assertNotIn("filename_candidate_confirmed_in_text", methods)

    def test_signature_date_prefers_signature_context_over_other_dates(self) -> None:
        text = (
            "Licenca emitida em 01/02/2023. A caderneta foi consultada em 12/03/2023.\n"
            "O predio rustico esta inscrito na matriz sob o artigo 440, seccao J.\n"
            "O presente contrato e celebrado e assinado em 31/05/2024."
        )
        result = ExtractionResult(
            document_category="lease_contract",
            lessor="Ana Maria Lopes",
            lessee="GESTO ENERGIA, S.A.",
            confidence="high",
        )

        validated = validate_result(result, document_text=text)

        self.assertEqual(validated.signed_date, "2024-05-31")

    def test_property_group_ignores_clause_article_without_property_context(self) -> None:
        text = (
            "Nos termos do artigo 5 do contrato, as partes acordam.\n"
            "A assinatura ocorreu em 31/05/2024."
        )

        group = recover_property_group(text)
        validated = validate_result(
            ExtractionResult(
                document_category="lease_contract",
                lessor="Ana Maria Lopes",
                lessee="GESTO ENERGIA, S.A.",
                signed_date="2024-05-31",
                monthly_rent="500,00 EUR",
                confidence="high",
            ),
            document_text=text,
        )

        self.assertEqual(group.article, "")
        self.assertEqual(validated.property_article, "")

    def test_ai_reviewer_evidence_uses_focused_300_char_windows(self) -> None:
        text = (
            "inicio irrelevante " * 300
            + "O predio rustico esta inscrito na matriz sob o artigo 440, seccao J. "
            + "fim irrelevante " * 300
        )

        evidence = select_evidence(text, ["property_article", "property_section"], max_chars=1000)

        self.assertIn("FIELD-FOCUSED 300-CHAR WINDOWS", evidence)
        self.assertIn("artigo 440", evidence)
        self.assertLessEqual(len(evidence), 1000)
        self.assertNotIn("DOCUMENT BEGINNING", evidence)

    def test_property_ai_proposal_requires_property_block_context(self) -> None:
        decision = validate_proposal(
            AIFieldProposal(
                field_name="property_article",
                proposed_value="5",
                evidence="Nos termos do artigo 5 do contrato, as partes acordam.",
                confidence=0.96,
            ),
            ExtractionResult(document_category="lease_contract"),
            document_text="Nos termos do artigo 5 do contrato, as partes acordam.",
            evidence_text="Nos termos do artigo 5 do contrato, as partes acordam.",
        )

        self.assertEqual(decision.decision, "REJECTED")
        self.assertEqual(decision.reason, "property_block_context_missing")

    def test_fuzzy_recovers_known_parish_name_with_local_evidence(self) -> None:
        result = ExtractionResult(
            document_category="property_document",
            property_article="440",
            property_section="J",
            property_parish="Pewas Roias",
            owner_name="Ana Maria Lopes",
            confidence="high",
        )
        text = "Caderneta predial do predio rustico em Pewas Roias, concelho de Mogadouro."

        validated = validate_result(result, document_text=text)

        self.assertEqual(validated.property_parish, "Penas Roias")
        self.assertEqual(
            validated.raw_json["fuzzy_recovery"]["changes"][0]["method"],
            "known_place_fuzzy_ocr",
        )


if __name__ == "__main__":
    unittest.main()
