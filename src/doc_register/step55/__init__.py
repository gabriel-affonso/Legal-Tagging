"""Step 5.5: local, evidence-first semantic extraction pilot.

Step 5.5 deliberately writes a separate canonical result.  It does not change
the Step 5.0 format and has no writer for operational workbooks.
"""

from .pipeline import Pipeline, PipelineConfig

__all__ = ["Pipeline", "PipelineConfig"]
