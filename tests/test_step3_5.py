from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from doc_register.models import ExtractionResult
from doc_register.validators.contract_final_resolution import (
    apply_contract_final_resolution,
    prepare_contract_final_resolution,
)
from doc_register.vision_recovery.integration import recover_contract_with_vision
from doc_register.vision_recovery.models import RenderedPage, VisionFieldCandidate
from doc_register.vision_recovery.ollama_vision_client import request_visual_candidates
from doc_register.vision_recovery.page_selector import build_vision_plan
from doc_register.vision_recovery.pdf_renderer import render_pdf_page


LOW_OCR_CONTRACT = """[Page 1]
CONTRATO DE ARRENDAMENTO
@@@ qwr zzz 1 2
[Page 2]
Entre ??? e GESTO ENERGIA, S.A.
"""


def _config(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "vision_enabled": True,
        "vision_model": "qwen3-vl:4b-instruct",
        "vision_timeout_seconds": 1,
        "vision_first_page_count": 2,
        "vision_max_pages_per_document": 2,
        "vision_render_dpi": 100,
        "vision_max_image_pixels": 100_000,
        "vision_ocr_quality_threshold": 0.60,
        "vision_critical_candidate_threshold": 0.78,
        "vision_apply_proposals": False,
        "vision_auto_accept_enabled": False,
        "vision_auto_accept_confidence": 0.95,
        "vision_keep_alive": "0",
        "ollama_url": "http://localhost:11434",
        "ollama_model": "qwen3:8b",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _signals() -> SimpleNamespace:
    return SimpleNamespace(suggested_category="lease_contract", contract_type="contrato_de_arrendamento")


class Step35Tests(unittest.TestCase):
    def test_renderer_creates_an_in_memory_image_for_the_requested_page(self) -> None:
        try:
            import pymupdf as fitz
        except ImportError:
            try:
                import fitz
            except ImportError:
                self.skipTest("PyMuPDF is not installed in this test environment")
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "one-page.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((72, 72), "CONTRATO DE ARRENDAMENTO")
            document.save(str(pdf_path))
            document.close()
            rendered = render_pdf_page(pdf_path, 1, dpi=100, max_pixels=500_000)

        self.assertEqual(rendered.page_number, 1)
        self.assertGreater(rendered.width, 0)
        self.assertGreater(rendered.height, 0)
        self.assertEqual(len(rendered.image_sha256), 64)
        self.assertTrue(rendered.image_base64)

    def test_vision_client_sends_one_base64_image_and_structured_output_request(self) -> None:
        rendered = RenderedPage(1, "image-base64", "image-digest", 100, 200)
        response = MagicMock()
        response.read.return_value = json.dumps({
            "done_reason": "stop",
            "message": {"content": json.dumps({
                "page_legibility": "high",
                "handwriting_present": False,
                "field_candidates": [],
            })},
        }).encode("utf-8")
        opener = MagicMock()
        opener.__enter__.return_value = response
        with patch("doc_register.vision_recovery.ollama_vision_client.request.urlopen", return_value=opener) as urlopen:
            candidates, audit = request_visual_candidates(
                base_url="http://localhost:11434",
                model="qwen3-vl:4b-instruct",
                file_name="contract.pdf",
                rendered_page=rendered,
                fields=("lessor",),
                timeout_seconds=1,
                keep_alive="0",
            )

        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(candidates, [])
        self.assertEqual(payload["keep_alive"], "0")
        self.assertEqual(payload["messages"][0]["images"], ["image-base64"])
        self.assertIsInstance(payload["format"], dict)
        self.assertEqual(audit["image_sha256"], "image-digest")

    def test_plan_selects_only_low_quality_first_pages_with_uncertain_critical_fields(self) -> None:
        report = prepare_contract_final_resolution(LOW_OCR_CONTRACT)
        plan = build_vision_plan(
            ExtractionResult(document_category="lease_contract", confidence="low"),
            signals=_signals(),
            final_resolution_report=report,
            config=_config(),
        )

        self.assertTrue(plan.eligible)
        self.assertEqual([item.page_number for item in plan.pages], [2])
        self.assertIn("lessor", plan.fields)

    def test_plan_does_not_run_when_vision_is_disabled(self) -> None:
        report = prepare_contract_final_resolution(LOW_OCR_CONTRACT)
        plan = build_vision_plan(
            ExtractionResult(document_category="lease_contract", confidence="low"),
            signals=_signals(),
            final_resolution_report=report,
            config=_config(vision_enabled=False),
        )

        self.assertFalse(plan.eligible)
        self.assertEqual(plan.reason, "vision_disabled")

    def test_shadow_mode_records_candidates_without_changing_contract_graph(self) -> None:
        report = prepare_contract_final_resolution(LOW_OCR_CONTRACT)
        result = ExtractionResult(document_category="lease_contract", confidence="low")
        candidate = VisionFieldCandidate(
            field_name="lessor",
            proposed_value="Ana Maria Lopes",
            evidence="Ana Maria Lopes, na qualidade de Senhoria",
            page_number=1,
            confidence=0.99,
        )
        rendered = RenderedPage(1, "not-used", "image-digest", 100, 200)
        with patch("doc_register.vision_recovery.integration.unload_model"), patch(
            "doc_register.vision_recovery.integration.render_pdf_page", return_value=rendered
        ), patch(
            "doc_register.vision_recovery.integration.request_visual_candidates",
            return_value=([candidate], {"page": 1, "image_sha256": "image-digest"}),
        ):
            recovered, applied = recover_contract_with_vision(
                result,
                config=_config(),
                file_name="contract.pdf",
                pdf_path=Path("contract.pdf"),
                signals=_signals(),
                final_resolution_report=report,
            )

        self.assertFalse(applied)
        self.assertEqual(recovered.vision_status, "SHADOW_COMPLETED")
        self.assertEqual(recovered.vision_accepted_fields, "")
        self.assertEqual(len(report.candidates), 0)
        self.assertEqual(recovered.raw_json["vision_recovery"]["candidates"][0]["proposed_value"], "Ana Maria Lopes")

    def test_handwritten_candidate_never_applies_even_when_auto_accept_is_enabled(self) -> None:
        report = prepare_contract_final_resolution(LOW_OCR_CONTRACT)
        result = ExtractionResult(document_category="lease_contract", confidence="low")
        candidate = VisionFieldCandidate(
            field_name="lessor",
            proposed_value="Ana Maria Lopes",
            evidence="Ana Maria Lopes, na qualidade de Senhoria",
            page_number=1,
            confidence=0.99,
            content_type="handwritten",
        )
        rendered = RenderedPage(1, "not-used", "image-digest", 100, 200)
        with patch("doc_register.vision_recovery.integration.unload_model"), patch(
            "doc_register.vision_recovery.integration.render_pdf_page", return_value=rendered
        ), patch(
            "doc_register.vision_recovery.integration.request_visual_candidates",
            return_value=([candidate], {"page": 1, "image_sha256": "image-digest"}),
        ):
            recovered, applied = recover_contract_with_vision(
                result,
                config=_config(vision_apply_proposals=True, vision_auto_accept_enabled=True),
                file_name="contract.pdf",
                pdf_path=Path("contract.pdf"),
                signals=_signals(),
                final_resolution_report=report,
            )

        self.assertFalse(applied)
        self.assertEqual(recovered.vision_status, "HUMAN_REVIEW_REQUIRED")
        self.assertEqual(recovered.vision_human_required_fields, "lessor")
        self.assertEqual(len(report.candidates), 0)

    def test_printed_high_confidence_candidate_enters_final_resolver_only_after_explicit_enablement(self) -> None:
        report = prepare_contract_final_resolution(LOW_OCR_CONTRACT)
        result = ExtractionResult(document_category="lease_contract", confidence="low")
        candidate = VisionFieldCandidate(
            field_name="lessor",
            proposed_value="Ana Maria Lopes",
            evidence="Ana Maria Lopes, na qualidade de Senhoria",
            page_number=1,
            confidence=0.99,
        )
        rendered = RenderedPage(1, "not-used", "image-digest", 100, 200)
        with patch("doc_register.vision_recovery.integration.unload_model"), patch(
            "doc_register.vision_recovery.integration.render_pdf_page", return_value=rendered
        ), patch(
            "doc_register.vision_recovery.integration.request_visual_candidates",
            return_value=([candidate], {"page": 1, "image_sha256": "image-digest"}),
        ):
            recovered, applied = recover_contract_with_vision(
                result,
                config=_config(vision_apply_proposals=True, vision_auto_accept_enabled=True),
                file_name="contract.pdf",
                pdf_path=Path("contract.pdf"),
                signals=_signals(),
                final_resolution_report=report,
            )

        self.assertTrue(applied)
        resolved = apply_contract_final_resolution(recovered, report)
        self.assertEqual(resolved.lessor, "Ana Maria Lopes")
        self.assertEqual(resolved.vision_accepted_fields, "lessor")
        visual = [item for item in report.candidates if item.source_type == "vision_page"]
        self.assertEqual(len(visual), 1)


if __name__ == "__main__":
    unittest.main()
