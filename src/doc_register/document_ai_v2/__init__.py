"""CPU-first, auditable document reconstruction primitives.

The package is deliberately independent of the historical ``pdf_text`` path:
legacy OCR is retained only as comparison evidence, never as reading order.
"""

from .pipeline import DocumentAIV2Pipeline, V2Config

__all__ = ["DocumentAIV2Pipeline", "V2Config"]
