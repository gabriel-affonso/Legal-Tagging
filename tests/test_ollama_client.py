from __future__ import annotations

import unittest
from unittest.mock import patch

from doc_register.detectors import detect_signals
from doc_register.ollama_client import (
    OllamaTimeoutError,
    extract_property_with_ollama,
    extract_with_ollama,
)


class OllamaClientTests(unittest.TestCase):
    def test_property_prompt_formats_literal_json_schema(self) -> None:
        with patch(
            "doc_register.ollama_client._chat_json",
            return_value={
                "property_name": "Quinta da Ribeira",
                "matrix_article": "123",
                "matrix_section": "K",
                "area_m2": "45230",
            },
        ) as chat:
            result = extract_property_with_ollama(
                "http://localhost:11434",
                "model",
                "a) Prédio denominado por Quinta da Ribeira.",
            )

        prompt = chat.call_args.args[2]
        self.assertIn('"property_name": ""', prompt)
        self.assertIn("Quinta da Ribeira", prompt)
        self.assertEqual(result["matrix_article"], "123")

    def test_classification_timeout_returns_deterministic_fallback(self) -> None:
        signals = detect_signals("IBAN.pdf", "IBAN PT50 0002 0123 1234 5678 9015 4")

        with patch(
            "doc_register.ollama_client.classify_with_ollama",
            side_effect=OllamaTimeoutError("timeout after 1s"),
        ):
            result = extract_with_ollama(
                "http://localhost:11434",
                "model",
                file_name="IBAN.pdf",
                classification_text="Texto suficiente para disparar a chamada local ao Ollama. " * 8,
                highlighted_text="IBAN PT50 0002 0123 1234 5678 9015 4 " * 8,
                signals=signals,
                timeout_seconds=1,
            )

        self.assertEqual(result.document_category, "bank_details")
        self.assertEqual(result.confidence, "low")
        self.assertIn("Timeout", result.extraction_notes)
        self.assertEqual(result.llm_json["error"]["step"], "classification")

    def test_extraction_timeout_keeps_classification(self) -> None:
        signals = detect_signals("Contrato.pdf", "Contrato de arrendamento com renda de 500 EUR")
        classification = {
            "document_category": "lease_contract",
            "document_type": "contrato de arrendamento",
            "confidence": "medium",
        }

        with (
            patch("doc_register.ollama_client.classify_with_ollama", return_value=classification),
            patch(
                "doc_register.ollama_client.extract_metadata_with_ollama",
                side_effect=OllamaTimeoutError("timeout after 1s"),
            ),
        ):
            result = extract_with_ollama(
                "http://localhost:11434",
                "model",
                file_name="Contrato.pdf",
                classification_text="Contrato de arrendamento com renda mensal. " * 8,
                highlighted_text="[[KEYWORD:Contrato]] de arrendamento com [[MONEY:500 EUR]]. " * 8,
                signals=signals,
                timeout_seconds=1,
            )

        self.assertEqual(result.document_category, "lease_contract")
        self.assertEqual(result.document_type, "contrato de arrendamento")
        self.assertEqual(result.confidence, "low")
        self.assertIn("segunda avaliacao", result.extraction_notes)


if __name__ == "__main__":
    unittest.main()
