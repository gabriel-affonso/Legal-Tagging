from __future__ import annotations

import unittest

from doc_register.models import ExtractionResult
from doc_register.validators.party_centric import (
    apply_party_centric_contract_extraction,
    prepare_party_centric_contract,
)


class Step27Tests(unittest.TestCase):
    def test_company_template_is_party_first_and_auditable(self) -> None:
        text = """[Page 1]
CONTRATO DE ARRENDAMENTO
Ana Maria Lopes, NIF 220957592, e Augusto Mendes, NIF 123456789,
de ora em diante designados por Senhorios.
GESTO ENERGIA, S.A., NIPC 508567475, doravante designada por Arrendatária.
O prédio rústico está inscrito na matriz predial sob o artigo 140, secção J,
freguesia de Penas Roias, concelho de Mogadouro, distrito de Bragança.
Cláusula primeira: o prazo inicia-se em 05/04/2025 e termina em 05/04/2055.
Cláusula segunda: a renda mensal é de 500,00 EUR.
"""
        report = prepare_party_centric_contract(text)
        result = apply_party_centric_contract_extraction(
            ExtractionResult(document_category="lease_contract", confidence="high"), report
        )

        self.assertEqual(result.lessor, "Ana Maria Lopes; Augusto Mendes")
        self.assertEqual(result.owner_name, "Ana Maria Lopes; Augusto Mendes")
        self.assertEqual(result.owner_tax_id, "220957592")
        self.assertEqual(result.lessee, "GESTO ENERGIA, S.A.")
        self.assertEqual(result.lessee_tax_id, "508567475")
        self.assertEqual(result.lessor_2, "Augusto Mendes")
        self.assertEqual(result.property_article, "140")
        self.assertEqual(result.property_section, "J")
        self.assertEqual(result.property_parish, "Penas Roias")
        self.assertEqual(result.property_municipality, "Mogadouro")
        self.assertEqual(result.property_district, "Bragança")
        self.assertEqual(result.contract_start_date, "2025-04-05")
        self.assertEqual(result.contract_end_date, "2055-04-05")
        self.assertEqual(result.monthly_rent, "500,00 EUR")
        audit = result.raw_json["step2_7_party_centric"]
        self.assertEqual(audit["field_sources"]["lessor"]["source"], "parties_lessor_template")
        self.assertEqual(audit["field_sources"]["property_article"]["source"], "recital_property")
        self.assertEqual(audit["field_sources"]["monthly_rent"]["source"], "rent_clause")

    def test_page_one_candidate_beats_later_shorter_duplicate(self) -> None:
        text = """[Page 1]
CONTRATO DE ARRENDAMENTO
Felisbina Maria Lopes Mendes, NIF 220957592, de ora em diante designada por Senhorio.
[Page 4]
Felisbina, de ora em diante designada por Senhorio.
"""
        report = prepare_party_centric_contract(text)
        result = apply_party_centric_contract_extraction(
            ExtractionResult(document_category="lease_contract", confidence="high"), report
        )
        self.assertEqual(result.lessor, "Felisbina Maria Lopes Mendes")
        self.assertEqual(result.raw_json["step2_7_party_centric"]["field_sources"]["lessor"]["page"], 1)

    def test_disallowed_global_value_is_cleared_when_no_party_clause_supports_it(self) -> None:
        text = """CONTRATO DE ARRENDAMENTO
Passport Renewal Service, de ora em diante designado por Senhorio.
Cláusula primeira: o prazo inicia-se em 05/04/2025.
"""
        report = prepare_party_centric_contract(text)
        result = apply_party_centric_contract_extraction(
            ExtractionResult(
                document_category="lease_contract",
                lessor="Government of Canada",
                lessee="Immigration Service",
                confidence="low",
            ),
            report,
        )
        self.assertEqual(result.lessor, "")
        self.assertEqual(result.lessee, "")
        self.assertTrue(result.raw_json["step2_7_party_centric"]["blocked_values"])


if __name__ == "__main__":
    unittest.main()
