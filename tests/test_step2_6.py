from __future__ import annotations

import unittest

from doc_register.models import ExtractionResult
from doc_register.validators import validate_result
from doc_register.validators.entity_resolution import (
    extract_property_codes,
    owner_candidates_from_filename,
    recover_property_name_from_text,
)


class Step26Tests(unittest.TestCase):
    def test_filename_property_code_becomes_operational_property_number(self) -> None:
        text = (
            "CONTRATO DE ARRENDAMENTO\n"
            "Primeiro Outorgante: Maria Isabel Leite Rodrigues Marcos, NIF 220957592.\n"
            "Segundo Outorgante: GESTO ENERGIA, S.A., NIPC 508567475.\n"
            "O predio rustico denominado Aguaxais, sito na freguesia de Penas Roias, "
            "esta inscrito na matriz predial sob o artigo 1945, seccao J.\n"
            "Assinado em 31/05/2024."
        )
        result = ExtractionResult(
            document_category="lease_contract",
            lessor="Maria Isabel Leite Rodrigues Marcos",
            lessee="GESTO ENERGIA, S.A.",
            owner_name="GESTO ENERGIA, S.A.",
            property_name="Predio rustico",
            property_number="508567475",
            property_article="1945",
            property_section="J",
            signed_date="2024-05-31",
            monthly_rent="500,00 EUR",
            confidence="high",
        )

        validated = validate_result(
            result,
            document_text=text,
            file_name="VA140_CA_Maria Isabel Leite Rodrigues Marcos_SP123.pdf",
            recover=False,
            entity_resolution=True,
        )

        self.assertEqual(validated.owner_name, "Maria Isabel Leite Rodrigues Marcos")
        self.assertEqual(validated.property_name, "Aguaxais")
        self.assertEqual(validated.property_number, "140")
        self.assertEqual(validated.property_article, "1945")
        self.assertNotIn("owner_matches_lessee", validated.validation_issues)
        self.assertNotIn("property_number_matches_tax_id", validated.validation_issues)

        report = validated.raw_json["step2_6_entity_resolution"]
        self.assertEqual(report["property_codes"], ["VA140"])
        self.assertEqual(report["property_numbers"], ["140"])
        self.assertIn("property_number", {change["field"] for change in report["changes"]})

    def test_multiple_filename_property_codes_are_kept_as_auditable_list(self) -> None:
        result = ExtractionResult(
            document_category="lease_contract",
            lessor="António Lopes Telo",
            lessee="GESTO ENERGIA, S.A.",
            signed_date="2024-05-31",
            monthly_rent="500,00 EUR",
            property_article="65",
            property_section="A",
            confidence="high",
        )

        validated = validate_result(
            result,
            document_text="Contrato com predio inscrito na matriz sob o artigo 65, seccao A.",
            file_name="VA088_089_397_399_401_CA_Antonio Lopes Telo.pdf",
            recover=False,
            entity_resolution=True,
        )

        self.assertEqual(validated.property_number, "88; 89; 397; 399; 401")
        self.assertEqual(
            validated.raw_json["step2_6_entity_resolution"]["property_codes"],
            ["VA088", "VA089", "VA397", "VA399", "VA401"],
        )

    def test_generic_party_and_property_values_are_cleared_when_not_resolved(self) -> None:
        result = ExtractionResult(
            document_category="lease_contract",
            lessor="Senhorios",
            lessee="Arrendataria",
            owner_name="Senhorio",
            property_name="Terreno",
            property_number="Anexo II",
            signed_date="2024-05-31",
            monthly_rent="500,00 EUR",
            confidence="high",
        )

        validated = validate_result(
            result,
            document_text="Contrato sem nomes legiveis. Assinado em 31/05/2024.",
            file_name="contrato_sem_codigo.pdf",
            recover=False,
            entity_resolution=True,
        )

        self.assertEqual(validated.lessor, "")
        self.assertEqual(validated.lessee, "")
        self.assertEqual(validated.owner_name, "")
        self.assertEqual(validated.property_name, "")
        self.assertEqual(validated.property_number, "")
        blocked = validated.raw_json["step2_6_entity_resolution"]["blocked_values"]
        self.assertEqual(
            {item["reason"] for item in blocked},
            {"generic_party_label", "generic_property_name", "invalid_property_number"},
        )

    def test_step26_helpers_extract_filename_and_property_name_candidates(self) -> None:
        self.assertEqual(
            extract_property_codes("PR098_CA_Felisbina Maria Lopes Mendes.pdf"),
            ["PR098"],
        )
        self.assertEqual(
            owner_candidates_from_filename("PR098_CA_Felisbina Maria Lopes Mendes e Augusto Maria Mendes_SP999.pdf"),
            ["Felisbina Maria Lopes Mendes", "Augusto Maria Mendes"],
        )
        self.assertEqual(
            recover_property_name_from_text(
                "O predio rustico denominado Aguaxais, freguesia de Penas Roias, "
                "inscrito na matriz sob o artigo 1945."
            ),
            "Aguaxais",
        )


if __name__ == "__main__":
    unittest.main()
