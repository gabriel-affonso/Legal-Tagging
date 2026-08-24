from __future__ import annotations

import unittest

from doc_register.detectors import detect_signals, is_bank_like_value


class DetectorTests(unittest.TestCase):
    def test_detects_portuguese_iban_and_nib(self) -> None:
        text = (
            "Titular: Maria Silva\n"
            "IBAN PT50 0002 0123 1234 5678 9015 4\n"
            "NIB 0002 0123 1234 5678 9015 4\n"
        )

        signals = detect_signals("dados_IBAN.pdf", text)

        self.assertIn("PT50000201231234567890154", signals.ibans)
        self.assertIn("000201231234567890154", signals.nibs)
        self.assertEqual(signals.suggested_category, "bank_details")

    def test_bank_numbers_are_not_money_values(self) -> None:
        self.assertTrue(is_bank_like_value("PT50 0002 0123 1234 5678 9015 4"))
        self.assertTrue(is_bank_like_value("0002 0123 1234 5678 9015 4"))


if __name__ == "__main__":
    unittest.main()

