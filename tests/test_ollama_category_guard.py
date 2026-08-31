from __future__ import annotations

import sys
import unittest

from doc_register.detectors import DeterministicSignals
from doc_register.ollama_client import _enforce_category_metadata, _select_category


def test_filename_iban_category_beats_bad_ocr_classification() -> None:
    signals = DeterministicSignals(file_category="bank_details", suggested_category="bank_details")
    assert _select_category({"document_category": "property_document"}, signals) == "bank_details"


def test_bank_category_removes_cadastral_owner_metadata() -> None:
    payload = {
        "document_type": "cadastral_book",
        "owner_name": "Francisco Antonio Marcos",
        "owner_tax_id": "123456789",
        "owner_address": "Rua A",
    }
    _enforce_category_metadata(payload, "bank_details")
    assert payload["document_type"] == "bank_account_details"
    assert payload["owner_name"] == ""
    assert payload["owner_tax_id"] == ""


def load_tests(loader, tests, pattern):
    module = sys.modules[__name__]
    functions = [
        getattr(module, name)
        for name in sorted(dir(module))
        if name.startswith("test_") and callable(getattr(module, name))
    ]
    return unittest.TestSuite(unittest.FunctionTestCase(function) for function in functions)
