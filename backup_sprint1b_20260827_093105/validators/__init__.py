from __future__ import annotations

"""
Document Register - Validation Package

Sprint 1A V2

Public API exposed by the validation layer.

This module centralizes access to:

- Field validation
- Quality scoring
- Quality bands
- Future validation utilities

Used by:

    from .validators import validate_result

inside processor.py
"""

from .validator import validate_result
from .quality_score import (
    calculate_score,
    determine_quality_band,
    score_summary,
    apply_penalty,
    get_penalty,
)

__all__ = [
    # Main entry point
    "validate_result",

    # Quality score helpers
    "calculate_score",
    "determine_quality_band",
    "score_summary",
    "apply_penalty",
    "get_penalty",
]

__version__ = "1.0.0"