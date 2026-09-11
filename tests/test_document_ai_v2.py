from doc_register.document_ai_v2.pipeline import Block, _group_lines


def test_reading_order_uses_geometry_not_legacy_token_sequence() -> None:
    # Mimics a bad OCR layer which emitted the right-column word first.
    blocks = [
        Block("p1_b1", 1, "paragraph", "mundo", (100, 10, 150, 20), .9, "test", 1),
        Block("p1_b2", 1, "paragraph", "Olá", (10, 10, 40, 20), .9, "test", 2),
        Block("p1_b3", 1, "paragraph", "seguinte", (10, 35, 80, 45), .9, "test", 3),
    ]
    lines = _group_lines(blocks)
    assert [line.text for line in lines] == ["Olá mundo", "seguinte"]
