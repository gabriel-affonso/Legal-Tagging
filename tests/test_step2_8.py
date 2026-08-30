from __future__ import annotations

import unittest

from doc_register.models import ExtractionResult
from doc_register.validators.contract_final_resolution import (
    apply_contract_final_resolution,
    prepare_contract_final_resolution,
)
from doc_register.validators.ocr_quality import analyze_page_quality
from doc_register.validators.validator import validate_result


PR098_FIXTURE = """[Page 1]
CONTRATO DE ARRENDAMENTO
Felisbina Maria Lopes Mendes, NIF 220957592, Nuno Miguel Telo Preto,
Augusto Maria Mendes e Dirce da Assunção Manso Mendes, todos na qualidade de Senhorios
e legítimos proprietários.
GESTO ENERGIA, S.A., NIPC 508567475, de ora em diante designada por Arrendatária.
Cláusula da renda: a renda é de 1000,00 EUR por hectare por ano.
[Page 7]
CADERNETA PREDIAL
IDENTIFICAÇÃO DO PRÉDIO
Localização: Aquaxais
Artigo matricial: 140
Secção: J
Freguesia de Pewas Roias, Concelho de Mogadouro, Distrito de Bragança
Área Total: 4,431200 hectares
Titular: Maria Diferente
[Page 8]
Parcela arrendada com área de 1,1312 hectares.
"""


class Step28Tests(unittest.TestCase):
    def test_pr098_contract_container_resolution_regression(self) -> None:
        report = prepare_contract_final_resolution(PR098_FIXTURE)
        result = apply_contract_final_resolution(
            ExtractionResult(
                document_category="lease_contract",
                lessor_2="GESTO ENERGIA, S.A.",
                property_name="Senhorio 1",
                confidence="high",
            ),
            report,
        )
        result = validate_result(result, document_text=PR098_FIXTURE, recover=False, entity_resolution=False)

        self.assertEqual(result.lessee, "GESTO ENERGIA, S.A.")
        self.assertEqual(result.lessee_tax_id, "508567475")
        self.assertEqual(
            result.lessor,
            "Felisbina Maria Lopes Mendes; Nuno Miguel Telo Preto; Augusto Maria Mendes; Dirce da Assunção Manso Mendes",
        )
        self.assertNotEqual(result.lessor_2, "GESTO ENERGIA, S.A.")
        self.assertEqual(result.property_name, "Aquaxais")
        self.assertEqual(result.property_article, "140")
        self.assertEqual(result.property_section, "J")
        self.assertEqual(result.property_parish, "Penas Roias")
        self.assertEqual(result.property_municipality, "Mogadouro")
        self.assertEqual(result.property_district, "Bragança")
        self.assertEqual(result.property_total_area, "4.431200 hectares")
        self.assertEqual(result.leased_parcel_area, "1.1312 hectares")
        self.assertEqual(result.rent_amount, "1000.00")
        self.assertEqual(result.rent_frequency, "annual")
        self.assertEqual(result.rent_unit, "per_hectare")
        self.assertEqual(result.monthly_rent, "")
        self.assertNotIn("missing_monthly_rent", result.validation_issues)
        self.assertIn("ownership_source_conflict", result.review_reason)
        audit = result.raw_json["step2_7_final_resolution"]
        self.assertTrue(audit["document_zones"])
        self.assertTrue(audit["field_candidates"])
        self.assertTrue(audit["conflicts"])

    def test_generic_property_label_is_not_accepted(self) -> None:
        report = prepare_contract_final_resolution(
            "CONTRATO DE ARRENDAMENTO\nSenhorio 1: Ana Maria Lopes.\n"
        )
        result = apply_contract_final_resolution(
            ExtractionResult(document_category="lease_contract", property_name="Senhorio 1"), report
        )
        self.assertEqual(result.property_name, "")

    def test_page_quality_does_not_score_semantic_noise_as_excellent(self) -> None:
        reports = analyze_page_quality(
            "[Page 1] A9X ## qwr zzz 1 2 3 !!!\n[Page 2] CADERNETA PREDIAL\nArtigo matricial: 140\nSecção: J\n"
        )
        self.assertEqual(len(reports), 2)
        self.assertLess(reports[0].overall_page_quality, reports[1].overall_page_quality)
        self.assertLess(reports[0].numeric_recoverability, reports[1].numeric_recoverability)

    def test_internal_model_a_caderneta_is_authoritative_for_property_and_owner(self) -> None:
        document = """[Page 1] CONTRATO DE ARRENDAMENTO
        Senhorio e Arrendatário acordam a renda anual.
        [Page 21] CADERNETA PREDIAL RÚSTICA
        Modelo A
        [Page 22] IDENTIFICAÇÃO DO PRÉDIO
        SECÇÃO: M
        ARTIGO MATRICIAL Nº: 456
        NOME/LOCALIZAÇÃO PRÉDIO: Herdade da Fonte
        ELEMENTOS DO PRÉDIO
        ÁREA TOTAL (HA): 2,5
        [Page 23] TITULARES
        Nome: Ana Maria da Silva
        Morada: Caminho da Fonte
        Tipo de titular: Propriedade plena
        """
        report = prepare_contract_final_resolution(document)
        result = apply_contract_final_resolution(
            ExtractionResult(document_category="lease_contract"), report
        )

        self.assertEqual(result.property_name, "Herdade da Fonte")
        self.assertEqual(result.property_article, "456")
        self.assertEqual(result.property_section, "M")
        self.assertEqual(result.property_total_area, "2.5 hectares")
        self.assertEqual(result.owner_name, "Ana Maria da Silva")


if __name__ == "__main__":
    unittest.main()
