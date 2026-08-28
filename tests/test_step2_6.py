from __future__ import annotations

import unittest

from doc_register.models import ExtractionResult
from doc_register.contract_types import detect_contract_type
from doc_register.detectors import detect_signals
from doc_register.validators import validate_result
from doc_register.validators.entity_resolution import (
    extract_property_codes,
    owner_candidates_from_filename,
    recover_property_name_from_text,
)
from doc_register.validators.property_recovery import recover_property_group


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
        blocked_reasons = {item["reason"] for item in blocked}
        self.assertIn("generic_party_label", blocked_reasons)
        self.assertIn("generic_property_name", blocked_reasons)
        self.assertIn("invalid_property_number", blocked_reasons)

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

    def test_filename_owner_candidates_fill_lessor_but_not_owner_when_ocr_roles_exist(self) -> None:
        text = (
            "CONTRATO DE ARRENDAMENTO\n"
            "de ora em diante designado por Senhorio 1\n"
            "de ora em diante designado por Senhorio 2\n"
            "designados, conjuntamente, por Senhorios\n"
            "Segundo Outorgante: GESTO ENERGIA, S.A."
        )
        result = ExtractionResult(
            document_category="lease_contract",
            lessor="",
            lessee="GESTO ENERGIA, S.A.",
            owner_name="",
            signed_date="2024-05-31",
            property_article="140",
            property_section="J",
            monthly_rent="500,00 EUR",
            confidence="high",
        )

        validated = validate_result(
            result,
            document_text=text,
            file_name="PR098_CA_Felisbina Maria Lopes Mendes e Augusto Maria Mendes_SP999.pdf",
            recover=False,
            entity_resolution=True,
        )

        self.assertEqual(validated.lessor, "Felisbina Maria Lopes Mendes; Augusto Maria Mendes")
        self.assertEqual(validated.owner_name, "")
        methods = {
            change["method"]
            for change in validated.raw_json["step2_6_entity_resolution"]["changes"]
        }
        self.assertIn("filename_owner_candidates_confirmed_by_lessor_roles", methods)

    def test_property_name_consumes_por_and_repairs_known_ocr_place(self) -> None:
        self.assertEqual(
            recover_property_name_from_text(
                "O predio rustico denominado\npor\nA Gyax ais, freguesia de Penas Roias."
            ),
            "Aguaxais",
        )

    def test_property_document_location_can_supply_business_property_name(self) -> None:
        result = ExtractionResult(
            document_category="property_document",
            property_location="Aguaxais",
            property_article="140",
            property_section="J",
            owner_name="ANTONIO AUGUSTO MENDES",
            confidence="high",
        )

        validated = validate_result(
            result,
            document_text=(
                "CADERNETA PREDIAL\n"
                "Artigo matricial No: 140\n"
                "Secção: J\n"
                "Localização: Aguaxais\n"
                "Titular: ANTONIO AUGUSTO MENDES"
            ),
            recover=False,
            entity_resolution=True,
        )

        self.assertEqual(validated.property_name, "Aguaxais")
        self.assertEqual(validated.property_location, "Aguaxais")

    def test_evidence_gate_clears_hallucinated_contract_values(self) -> None:
        text = (
            "CONTRATO DE ARRENDAMENTO\n"
            "Primeiro Outorgante: Felisbina Maria Lopes Mendes.\n"
            "Segundo Outorgante: GESTO ENERGIA, S.A.\n"
            "O predio rustico denominado Aguaxais esta inscrito na matriz sob o artigo 140, seccao J.\n"
            "Assinado em 31/05/2024."
        )
        result = ExtractionResult(
            document_category="lease_contract",
            lessor="Felisbina Maria Lopes Mendes",
            lessee="GESTO ENERGIA, S.A.",
            owner_name="João Silva",
            owner_tax_id="123456789",
            property_name="Passport Renewal Service",
            property_article="Art. 1234",
            property_section="Seção 1",
            property_parish="Paróquia Solar",
            property_municipality="Município Solar",
            property_district="Distrito Solar",
            property_address="Rua Solar, 123, Município Solar",
            monthly_rent="50000.00",
            currency="USD",
            option_price="1000000.00",
            purchase_price="5000000.00",
            assignment_price="2000000.00",
            signed_date="2024-05-31",
            confidence="high",
        )

        validated = validate_result(
            result,
            document_text=text,
            file_name="PR098_CA_Felisbina Maria Lopes Mendes.pdf",
            recover=False,
            entity_resolution=True,
        )

        self.assertEqual(validated.owner_name, "")
        self.assertEqual(validated.owner_tax_id, "")
        self.assertEqual(validated.property_name, "")
        self.assertEqual(validated.property_article, "")
        self.assertEqual(validated.property_section, "")
        self.assertEqual(validated.property_parish, "")
        self.assertEqual(validated.property_municipality, "")
        self.assertEqual(validated.property_district, "")
        self.assertEqual(validated.property_address, "")
        self.assertEqual(validated.monthly_rent, "")
        self.assertEqual(validated.currency, "")
        self.assertEqual(validated.option_price, "")
        self.assertEqual(validated.purchase_price, "")
        self.assertEqual(validated.assignment_price, "")

    def test_matrix_inscription_year_is_not_property_article(self) -> None:
        text = (
            "CADERNETA PREDIAL\n"
            "Ano de inscrição na matriz: 1945\n"
            "Artigo matricial No: 140\n"
            "Secção: J\n"
        )

        signals = detect_signals("PR098_CadernetaPredial.pdf", text)
        group = recover_property_group(text)

        self.assertEqual(signals.property_article, "140")
        self.assertEqual(signals.property_section, "J")
        self.assertEqual(group.article, "140")

    def test_contract_end_date_in_future_is_allowed_but_bad_order_is_flagged(self) -> None:
        long_contract = validate_result(
            ExtractionResult(
                document_category="lease_contract",
                lessor="Ana Maria Lopes",
                lessee="GESTO ENERGIA, S.A.",
                signed_date="2024-05-31",
                contract_start_date="2025-04-05",
                contract_end_date="2055-04-05",
                property_article="140",
                property_section="J",
                monthly_rent="500,00 EUR",
                confidence="high",
            ),
            recover=False,
        )
        bad_order = validate_result(
            ExtractionResult(
                document_category="lease_contract",
                lessor="Ana Maria Lopes",
                lessee="GESTO ENERGIA, S.A.",
                signed_date="2024-05-31",
                contract_start_date="2025-04-05",
                contract_end_date="2025-04-05",
                property_article="140",
                property_section="J",
                monthly_rent="500,00 EUR",
                confidence="high",
            ),
            recover=False,
        )

        self.assertNotIn("future_date_contract_end_date", long_contract.validation_issues)
        self.assertIn("contract_end_not_after_start", bad_order.validation_issues)

    def test_formal_lease_title_beats_isolated_purchase_promise_terms(self) -> None:
        contract_type = detect_contract_type(
            "CONTRATO DE ARRENDAMENTO PARA FINS FOTOVOLTAICOS\n"
            "As partes podem celebrar futura promessa de compra e venda."
        )

        self.assertIsNotNone(contract_type)
        self.assertEqual(contract_type.code, "contrato_de_arrendamento")


if __name__ == "__main__":
    unittest.main()
