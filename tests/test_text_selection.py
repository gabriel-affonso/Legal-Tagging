from __future__ import annotations

import unittest

from doc_register.text_selection import highlight_important_text, select_classification_text


class TextSelectionTests(unittest.TestCase):
    def test_classification_text_uses_first_two_text_pages(self) -> None:
        text = (
            "[Page 1] Contrato de arrendamento com renda mensal.\n\n"
            "[Page 2] Proprietario e propriedade identificados.\n\n"
            "[Page 3] Esta pagina ja nao deve entrar."
        )

        selected = select_classification_text(text)

        self.assertIn("[Page 1]", selected)
        self.assertIn("[Page 2]", selected)
        self.assertNotIn("[Page 3]", selected)

    def test_classification_text_falls_back_to_500_words(self) -> None:
        text = " ".join(f"word{i}" for i in range(700))

        selected = select_classification_text(text)

        self.assertEqual(len(selected.split()), 500)

    def test_highlight_marks_dates_money_months_and_keywords(self) -> None:
        text = "Pagamento de 1.234,56 EUR em 12/03/2024 referente a propriedade em março."

        highlighted = highlight_important_text(text)

        self.assertIn("[[KEYWORD:Pagamento]]", highlighted)
        self.assertIn("[[MONEY:1.234,56 EUR]]", highlighted)
        self.assertIn("[[DATE:12/03/2024]]", highlighted)
        self.assertIn("[[MONTH:março]]", highlighted)


if __name__ == "__main__":
    unittest.main()

