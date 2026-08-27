from __future__ import annotations
from typing import Any
from ..config import AppConfig
from ..models import ExtractionResult
from .validator import validate_result

def recover_validate_result(result: ExtractionResult, *, config: AppConfig, file_name: str, signals: Any, document_text: str) -> ExtractionResult:
    return validate_result(
        result, signals, document_text, file_name=file_name,
        ollama_url=config.ollama_url, ollama_model=config.ollama_model,
        recovery_ai_enabled=bool(getattr(config, "critical_recovery_ai_enabled", False)),
        recovery_timeout_seconds=int(getattr(config, "critical_recovery_timeout_seconds", 180)),
    )
