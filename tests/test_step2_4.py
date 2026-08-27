from __future__ import annotations

import unittest

from doc_register.models import ExtractionResult
from doc_register.validators.contract_clause_integration import (
    apply_contract_clause_segmentation,
)
from doc_register.validators.contract_clause_segmentation import segment_contract_clauses


class Step24Tests(unittest.TestCase):
    def test_segments_lease_contract_clauses_by_semantic_type(self) -> None:
        text = (
            "[Page 1] CONTRATO DE ARRENDAMENTO "
            "Primeiro Outorgante: Ana Maria Lopes, NIF 123456789. "
            "Segundo Outorgante: GESTO ENERGIA, S.A., NIPC 509999999. "
            "Clausula Primeira - Objeto O predio rustico esta inscrito na matriz sob o artigo 440, seccao J. "
            "Clausula Segunda - Prazo O contrato tem inicio em 01/06/2024 e termina em 31/05/2025. "
            "Clausula Terceira - Renda A renda mensal e de 700,00 EUR, paga ate ao dia 8 de cada mes. "
            "[Page 2] Assinado em 31/05/2024."
        )

        report = segment_contract_clauses(text)
        clause_types = {clause.clause_type for clause in report.clauses}

        self.assertEqual(report.status, "SEGMENTED")
        self.assertIn("parties", clause_types)
        self.assertIn("property", clause_types)
        self.assertIn("term", clause_types)
        self.assertIn("rent", clause_types)
        self.assertIn("signatures", clause_types)

    def test_clause_segmentation_recovers_contract_fields_and_audit_report(self) -> None:
        text = (
            "[Page 1] CONTRATO DE ARRENDAMENTO "
            "Primeiro Outorgante: Ana Maria Lopes, NIF 123456789. "
            "Segundo Outorgante: GESTO ENERGIA, S.A., NIPC 509999999. "
            "Clausula Primeira - Objeto O predio rustico esta inscrito na matriz sob o artigo 440, seccao J. "
            "Clausula Segunda - Prazo O contrato tem inicio em 01/06/2024 e termina em 31/05/2025. "
            "Clausula Terceira - Renda A renda mensal e de 700,00 EUR, paga ate ao dia 8 de cada mes. "
            "[Page 2] Assinado em 31/05/2024."
        )
        result = ExtractionResult(
            document_category="lease_contract",
            document_subtype="contrato_de_arrendamento",
            contract_type="contrato_de_arrendamento",
            lessor="Senhorios",
            lessee="Arrendatario",
            confidence="high",
        )

        updated = apply_contract_clause_segmentation(result, document_text=text)

        self.assertEqual(updated.lessor, "Ana Maria Lopes")
        self.assertEqual(updated.lessee, "GESTO ENERGIA, S.A.")
        self.assertEqual(updated.property_article, "440")
        self.assertEqual(updated.property_section, "J")
        self.assertEqual(updated.contract_start_date, "2024-06-01")
        self.assertEqual(updated.contract_end_date, "2025-05-31")
        self.assertEqual(updated.monthly_rent, "700,00 EUR")
        self.assertEqual(updated.rent_payment_day, "8")
        self.assertEqual(updated.signed_date, "2024-05-31")
        self.assertIn("contract_clause_segmentation", updated.raw_json)
        self.assertEqual(
            updated.raw_json["contract_clause_segmentation"]["integration_status"],
            "APPLIED",
        )

    def test_clause_segmentation_skips_non_contract_documents(self) -> None:
        result = ExtractionResult(document_category="bank_details", iban="PT500001")

        updated = apply_contract_clause_segmentation(
            result,
            document_text="Declaracao bancaria com IBAN PT500001.",
        )

        self.assertEqual(updated.iban, "PT500001")
        self.assertEqual(
            updated.raw_json["contract_clause_segmentation"]["integration_status"],
            "NOT_APPLICABLE",
        )


if __name__ == "__main__":
    unittest.main()
