from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from doc_register.pdf_text import (
    TextExtractionCandidate,
    _candidate_is_better,
    _normalize_page_text,
    extract_text_with_optional_ocr,
)


class Step25Tests(unittest.TestCase):
    def test_preserve_layout_keeps_meaningful_line_breaks(self) -> None:
        text = "Primeiro Outorgante: Ana Lopes\n\n   Artigo   440   Secção J\nRenda 700 EUR"

        flattened = _normalize_page_text(text, preserve_layout=False)
        layout = _normalize_page_text(text, preserve_layout=True)

        self.assertEqual(flattened, "Primeiro Outorgante: Ana Lopes Artigo 440 Secção J Renda 700 EUR")
        self.assertIn("Primeiro Outorgante: Ana Lopes\nArtigo 440 Secção J\nRenda 700 EUR", layout)

    def test_ocr_candidate_can_win_by_quality_not_only_character_count(self) -> None:
        native = TextExtractionCandidate(
            text="x" * 1000,
            source="native_pdf_text",
            native_text_chars=1000,
            quality_score=20,
        )
        ocr = TextExtractionCandidate(
            text="Contrato\nClausula Primeira\nRenda mensal 700 EUR",
            source="cached_ocr_pdf_layout_text",
            ocr_text_chars=220,
            quality_score=42,
        )

        self.assertTrue(_candidate_is_better(ocr, native))

    def test_good_layout_native_text_skips_ocr(self) -> None:
        native = TextExtractionCandidate(
            text="[Page 1]\nContrato de arrendamento\nClausula Primeira\nRenda mensal 700 EUR",
            source="native_pdf_layout_text",
            native_text_chars=900,
            quality_score=72,
            notes="Step 2.5 PyMuPDF layout-aware extraction selected.",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("doc_register.pdf_text._best_native_candidate", return_value=native), patch(
                "doc_register.pdf_text.run_ocrmypdf"
            ) as run_ocr:
                extracted = extract_text_with_optional_ocr(
                    Path(tmpdir) / "contract.pdf",
                    max_pages=12,
                    max_chars=24000,
                    ocr_enabled=True,
                    ocr_min_text_chars=600,
                    ocr_dir=Path(tmpdir),
                    ocr_language="por+eng",
                    ocr_timeout_seconds=1,
                    layout_extraction_enabled=True,
                    layout_min_quality_score=35,
                )

        self.assertEqual(extracted.source, "native_pdf_layout_text")
        self.assertEqual(extracted.native_text_chars, 900)
        run_ocr.assert_not_called()

    def test_low_quality_native_text_can_fall_back_to_generated_ocr(self) -> None:
        native = TextExtractionCandidate(
            text="[Page 1] @@@ @@@",
            source="native_pdf_text",
            native_text_chars=80,
            quality_score=5,
        )
        ocr = TextExtractionCandidate(
            text="[Page 1]\nContrato de arrendamento\nClausula Terceira\nRenda mensal 700 EUR",
            source="cached_ocr_pdf_layout_text",
            ocr_text_chars=760,
            quality_score=76,
            notes="Step 2.5 PyMuPDF layout-aware extraction selected.",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            ocr_dir = Path(tmpdir)

            def fake_ocr(_input: Path, output: Path, **_kwargs: object) -> None:
                output.write_bytes(b"%PDF-1.4 fake")

            with patch(
                "doc_register.pdf_text._best_native_candidate",
                side_effect=[native, ocr],
            ), patch("doc_register.pdf_text.run_ocrmypdf", side_effect=fake_ocr):
                extracted = extract_text_with_optional_ocr(
                    Path(tmpdir) / "contract.pdf",
                    max_pages=12,
                    max_chars=24000,
                    ocr_enabled=True,
                    ocr_min_text_chars=600,
                    ocr_dir=ocr_dir,
                    ocr_language="por+eng",
                    ocr_timeout_seconds=1,
                    layout_extraction_enabled=True,
                    layout_min_quality_score=35,
                )

        self.assertEqual(extracted.source, "cached_ocr_pdf_layout_text")
        self.assertEqual(extracted.native_text_chars, 80)
        self.assertEqual(extracted.ocr_text_chars, 760)
        self.assertIn("OCR executado", extracted.notes)


if __name__ == "__main__":
    unittest.main()
