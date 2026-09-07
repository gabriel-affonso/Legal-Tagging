import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf as fitz

from doc_register.step55.canonical import Duration, EvidenceSpan, LiteralDate
from doc_register.step55.document_map import Observation, classify
from doc_register.step55.pipeline import Pipeline, PipelineConfig


def make_pdf(path: Path, pages: list[str]):
    with fitz.open() as document:
        for content in pages:
            page = document.new_page()
            page.insert_textbox(page.rect + (36, 36, -36, -36), content, fontsize=10)
        document.save(path)


class Step55Tests(unittest.TestCase):
    def test_document_types_do_not_inherit_between_regions(self):
        self.assertEqual(classify("CONTRATO DE ARRENDAMENTO")[0], "lease_contract")
        self.assertEqual(classify("Política de Privacidade e RGPD")[0], "privacy_notice")
        self.assertEqual(classify("IBAN NIB e dados pessoais")[0], "unknown")
        self.assertEqual(classify("306")[2], "low_text")

    def test_temporal_types_are_distinct(self):
        duration = Duration(years=29, months=11)
        literal = LiteralDate(value=__import__("datetime").date(2020, 12, 22))
        self.assertEqual(duration.kind, "duration")
        self.assertEqual(literal.kind, "literal_date")
        with self.assertRaises(ValueError):
            Duration()

    def test_evidence_rejects_invalid_offset(self):
        with self.assertRaises(ValueError):
            EvidenceSpan(id="e", page=1, region_id="r", text="abc", start=1, end=5,
                         reading_method="native", dependency_id="d")

    def test_receipt_is_structured_without_llm_and_date_is_document_date(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "receipt.pdf"
            make_pdf(source, ["""Envio de recibo de renda
18 de Abril de 2024
Valor bruto de 3.700,00 EUR
Retido na fonte de 925,00 EUR
Valor líquido de 2.775,00 EUR
artigos 68 e 530"""])
            result = json.loads(Pipeline(root / "runs").process(source).read_text())
            self.assertEqual(result["logical_documents"][0]["source_type"], "payment_correspondence")
            values = {item["predicate"]: item["typed_value"] for item in result["assertions"]}
            self.assertEqual(values["payment_gross_amount"], {"amount": "3700", "currency": "EUR"})
            self.assertEqual(values["withholding_tax_amount"], {"amount": "925", "currency": "EUR"})
            self.assertEqual(values["payment_net_amount"], {"amount": "2775", "currency": "EUR"})
            self.assertEqual(values["document_date"]["kind"], "literal_date")
            self.assertNotIn("bank_execution_date", values)
            articles = [item for item in result["assertions"] if item["predicate"] == "related_property_article"]
            self.assertEqual({item["typed_value"] for item in articles}, {"68", "530"})
            self.assertTrue(all("cross_document_property_resolution_required" in item["validation_issues"] for item in articles))
            self.assertEqual(result["metrics"]["model_calls"], 0)
            projection = json.loads((Path(Pipeline(root / "runs").process(source)).parent / "staging-projection.json").read_text())
            self.assertTrue(projection["export_blocked"])

    def test_privacy_cannot_create_property_or_iban(self):
        observation = Observation("r", 1, "Política de Privacidade RGPD. Avenida das Túlipas. IBAN/NIB.", None, "native")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "privacy.pdf"
            make_pdf(source, [observation.text])
            result = json.loads(Pipeline(root / "runs").process(source).read_text())
            self.assertEqual(result["logical_documents"][0]["source_type"], "privacy_notice")
            self.assertEqual(result["entities"], [])
            self.assertEqual(result["assertions"], [])

    def test_ocr_is_a_second_observation_and_failure_is_not_blank(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "scan.pdf"
            with fitz.open() as document:
                document.new_page()
                document.save(source)
            with patch("doc_register.step55.pipeline.ocr", return_value="Recibo de renda\nValor líquido de 2.775,00 EUR"):
                result = json.loads(Pipeline(root / "runs").process(source).read_text())
            self.assertEqual(result["logical_documents"][0]["source_type"], "payment_receipt")
            self.assertEqual(result["evidence"][0]["reading_method"], "ocr")

    def test_contract_keeps_durations_rates_percentages_and_areas_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "contract.pdf"
            make_pdf(source, ["""CONTRATO DE ARRENDAMENTO
Assinado em 22 de Dezembro de 2020
O contrato terá a duração de 29 anos e 11 meses.
O início do arrendamento depende da Condição Suspensiva.
Renda anual de 1.000,00 EUR por hectare, anualmente.
25% da renda anual será pago após licença.
Prédio denominado Fonte da Carvalha, artigo matricial: 68, descrição predial: 529,
freguesia de Castedo, concelho de Torre de Moncorvo, área total de 25.650 m2.
Prédio denominado Cascalheira, artigo matricial: 306, descrição predial: 530,
freguesia de Castedo, concelho de Torre de Moncorvo, área total de 11.502 m2.
Área útil aproximada de 3,7 ha."""])
            result = json.loads(Pipeline(root / "runs").process(source).read_text())
            values = {item["predicate"]: [] for item in result["assertions"]}
            for item in result["assertions"]:
                values[item["predicate"]].append(item["typed_value"])
            self.assertEqual(values["term_duration"], [{"kind": "duration", "years": 29, "months": 11, "days": 0}])
            self.assertEqual(values["rent_term"][0]["amount"], "1000")
            self.assertEqual(values["reservation_obligation"][0]["fraction"], "0.25")
            self.assertEqual({item["typed_value"] for item in result["assertions"] if item["predicate"] == "cadastral_article"}, {"68", "306"})
            areas = values["area_measurement"]
            self.assertTrue(any(item["concept"] == "leased_usable" and item["unit"] == "ha" for item in areas))
            self.assertTrue(any(item["concept"] == "cadastral_total" and item["amount"] == "25650" for item in areas))
            self.assertNotIn("contract_end_date", values)


if __name__ == "__main__":
    unittest.main()
