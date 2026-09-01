from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from doc_register.__main__ import _build_parser
from doc_register.config import AppConfig
from doc_register.models import ExtractionResult
from doc_register.step37 import (
    apply_step37_resolution,
    prepare_step37,
    score_cadastral_page,
)
from doc_register.vision_recovery.page_selector import build_vision_plan


def _config(**overrides):
    values = {
        "ocr_dir": "/tmp/does-not-exist",
        "step_3_7_maximum_contract_pages": 3,
        "step_3_7_maximum_cadastral_pages": 1,
        "step_3_7_maximum_total_pages": 4,
        "step_3_7_maximum_chars_per_page": 12_000,
        "step_3_7_maximum_total_context_chars": 40_000,
        "step_3_7_cadastral_page_threshold": 7,
        "step_3_7_cadastral_min_indicator_diversity": 2,
        "step_3_7_cadastral_ocr_quality_threshold": 0.60,
        "step_3_7_maximum_cadastral_visual_candidates": 3,
        "step_3_7_cadastral_indicator_scores": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_cli_accepts_versioned_and_focused_last_forms() -> None:
    args = _build_parser().parse_args(["--step", "3.7", "--last", "10"])
    assert args.step == "3.7"
    assert args.last == 10
    alias = _build_parser().parse_args(["--focused-core-extraction-last", "4"])
    assert alias.focused_core_extraction_last == 4


def test_nested_step37_config_is_loaded() -> None:
    with TemporaryDirectory() as directory:
        base = Path(directory)
        config_path = base / "config.json"
        config_path.write_text(
            json.dumps({
                "input_dir": ".",
                "processing_dir": "processing",
                "archive_dir": "archive",
                "error_dir": "error",
                "excel_path": "register.xlsx",
                "ocr_dir": "ocr",
                "step_3_7": {
                    "enabled": True,
                    "cadastral_page_threshold": 9,
                    "maximum_total_context_chars": 32000,
                    "cadastral_indicator_scores": {"caderneta_predial": 6},
                },
            }),
            encoding="utf-8",
        )
        config = AppConfig.from_json(config_path)
    assert config.step_3_7_enabled
    assert config.step_3_7_cadastral_page_threshold == 9
    assert config.step_3_7_maximum_total_context_chars == 32000
    assert config.step_3_7_cadastral_indicator_scores["caderneta_predial"] == 6


def test_cadastral_detector_rejects_separator_and_accepts_structured_page() -> None:
    separator = score_cadastral_page(4, "CADERNETA PREDIAL")
    assert separator.is_separator
    assert not separator.is_valid

    page = score_cadastral_page(
        5,
        """AUTORIDADE TRIBUTÁRIA E ADUANEIRA
CADERNETA PREDIAL RÚSTICA
IDENTIFICAÇÃO DO PRÉDIO
Artigo Matricial: 623
Secção: A
Freguesia: Sendim
Concelho: Miranda do Douro
""",
    )
    assert page.is_valid
    assert page.score >= 7
    assert {"A", "B", "D"}.issubset(page.content_groups)


def test_selects_only_first_three_contract_pages_and_first_valid_caderneta() -> None:
    pages = [
        "CONTRATO DE ARRENDAMENTO\nEntre Ana Lopes, na qualidade de Senhoria.",
        "O prédio encontra-se inscrito no artigo matricial 628, secção A.",
        "Solar Lda., na qualidade de Arrendatária.",
        "CADERNETA PREDIAL",  # separator, deliberately not selected
        """AUTORIDADE TRIBUTÁRIA E ADUANEIRA
CADERNETA PREDIAL RÚSTICA
IDENTIFICAÇÃO DO PRÉDIO
Artigo Matricial: 623
Secção: A
Freguesia: Sendim
Concelho: Miranda do Douro
IDENTIFICAÇÃO DOS TITULARES
Nome: Maria Cordeiro
Tipo de titular: Propriedade plena
""",
        "This intermediate annex page must never reach the model.",
    ]
    with patch("doc_register.step37.read_pdf_pages", return_value=pages):
        report = prepare_step37(
            Path("contract.pdf"),
            file_name="VA628_contract.pdf",
            config=_config(),
        )

    assert report.selected_page_numbers == [1, 2, 3, 5]
    assert "PDF_PAGE=5" in report.context
    assert "PDF_PAGE=4" not in report.context
    assert "intermediate annex" not in report.context
    assert len(report.context) <= 40_000

    result = apply_step37_resolution(
        ExtractionResult(
            document_category="lease_contract",
            property_article="628",  # lower-authority/model or filename signal
        ),
        report,
    )
    assert result.property_article == "623"
    assert result.property_section == "A"
    assert result.owner_name == "Maria Cordeiro"
    audit = result.raw_json["step3_7"]
    assert audit["contract_property_articles"] == ["628"]
    assert audit["field_results"]["property_article"]["source_page"] == 5
    assert audit["field_results"]["property_article"]["decision_reason"] == "direct_labeled_cadastral_evidence"


def test_context_truncation_is_explicit_and_preserves_late_core_label() -> None:
    dense = "\n".join(["texto acessório " * 20 for _ in range(40)])
    pages = [
        dense + "\nArrendatário: Solar Energia, S.A.",
        "página dois",
        "página três",
    ]
    with patch("doc_register.step37.read_pdf_pages", return_value=pages):
        report = prepare_step37(
            Path("contract.pdf"),
            file_name="contract.pdf",
            config=_config(
                step_3_7_maximum_chars_per_page=500,
                step_3_7_maximum_total_context_chars=2_000,
            ),
        )
    assert report.context_truncated
    assert report.pages[0].page_context_truncated
    assert "Arrendatário" in report.pages[0].context_text
    assert len(report.context) <= 2_000


def test_visual_confirmation_is_limited_to_focused_candidate_pages() -> None:
    result = ExtractionResult(
        document_category="lease_contract",
        confidence="high",
        lessor="Ana Lopes",
        lessee="Solar, S.A.",
        signed_date="2025-01-01",
        property_article="623",
        property_section="A",
    )
    report = SimpleNamespace(
        decisions={
            name: {"confidence": 0.99}
            for name in ("lessor", "lessee", "signed_date", "property_article", "property_section")
        },
        focused_visual_pages=(18, 19, 20),
        focused_visual_reasons=("candidate_score_tie",),
    )
    plan = build_vision_plan(
        result,
        signals=SimpleNamespace(suggested_category="lease_contract", contract_type="contrato_de_arrendamento"),
        final_resolution_report=report,
        config=SimpleNamespace(
            vision_enabled=True,
            step_3_7_enabled=True,
            vision_critical_candidate_threshold=0.78,
        ),
    )
    assert plan.eligible
    assert plan.reason == "step3_7_cadastral_confirmation"
    assert [page.page_number for page in plan.pages] == [18, 19, 20]
