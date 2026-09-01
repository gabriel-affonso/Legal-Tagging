from __future__ import annotations

from types import SimpleNamespace
import unittest

from doc_register.__main__ import _build_parser
from doc_register.models import ExtractionResult
from doc_register.validators.contract_final_resolution import (
    apply_contract_final_resolution,
    prepare_contract_final_resolution,
)
from doc_register.vision_recovery.page_selector import build_vision_plan


EVIDENCE_TEXT = """[Page 1]
CONTRATO DE ARRENDAMENTO
Entre Ana Maria Lopes, na qualidade de Senhoria, e GESTO ENERGIA, S.A., de ora em diante designada por Arrendatária.
O imóvel corresponde ao artigo matricial 440, secção J.
[Page 8]
CADERNETA PREDIAL RÚSTICA
IDENTIFICAÇÃO DO PRÉDIO
ARTIGO MATRICIAL: 140
SECÇÃO: J
NOME/LOCALIZAÇÃO PRÉDIO: Aquaxais
ELEMENTOS DO PRÉDIO
ÁREA TOTAL (HA): 4,431200
[Page 9]
TITULARES
Nome: Maria Diferente
Tipo de titular: Propriedade plena
"""


class Step36Tests(unittest.TestCase):
    def test_evidence_graph_preserves_conflicting_values_and_llm_cannot_replace_contract(self) -> None:
        report = prepare_contract_final_resolution(EVIDENCE_TEXT)
        result = apply_contract_final_resolution(
            ExtractionResult(document_category="lease_contract", property_article="999"), report
        )

        evidence = result.raw_json["step3_6_evidence"]
        article_evidence = evidence["graph"]["property_article"]
        values = {(item["value"], item["source"]) for item in article_evidence}
        self.assertIn(("440", "CONTRACT_EXPLICIT"), values)
        self.assertIn(("140", "CADERNETA"), values)
        self.assertIn(("999", "LLM_EXTRACTION"), values)
        # No lower-authority LLM value can win; all alternatives remain in the
        # graph even when the contract candidate is published.
        self.assertEqual(result.property_article, "440")
        self.assertTrue(any(item["field"] == "property_article" for item in evidence["conflicts"]))

    def test_ownership_roles_are_distinct_and_multi_owner_safe(self) -> None:
        report = prepare_contract_final_resolution(EVIDENCE_TEXT)
        result = apply_contract_final_resolution(ExtractionResult(document_category="lease_contract"), report)

        ownership = result.raw_json["step3_6_evidence"]["ownership"]
        self.assertEqual(ownership["contract_lessor"][0]["name"], "Ana Maria Lopes")
        self.assertEqual(ownership["cadastral_owners"][0]["name"], "Maria Diferente")
        self.assertEqual(result.owner_name, "Maria Diferente")

    def test_recall_plan_uses_first_last_signature_and_property_pages(self) -> None:
        report = prepare_contract_final_resolution(EVIDENCE_TEXT)
        config = SimpleNamespace(
            vision_enabled=True, vision_recall_mode=True, vision_recall_first_pages=5,
            vision_recall_last_pages=3, vision_max_pages_per_document=8,
            vision_critical_candidate_threshold=0.78,
        )
        plan = build_vision_plan(
            ExtractionResult(document_category="lease_contract", confidence="low"),
            signals=SimpleNamespace(suggested_category="lease_contract", contract_type="contrato_de_arrendamento"),
            final_resolution_report=report, config=config,
        )

        self.assertTrue(plan.eligible)
        self.assertEqual(plan.reason, "targeted_vision_recall")
        self.assertIn(1, [item.page_number for item in plan.pages])
        self.assertIn(9, [item.page_number for item in plan.pages])

    def test_cli_accepts_standalone_batch_recall(self) -> None:
        args = _build_parser().parse_args(["--vision-recall-last", "10"])
        self.assertEqual(args.command, "scan")
        self.assertEqual(args.vision_recall_last, 10)


if __name__ == "__main__":
    unittest.main()
