"""Opt-in real local model smoke. Generated text is NOT an accuracy benchmark."""
from dataclasses import asdict
import json
from pathlib import Path
import time

import pymupdf as fitz
from doc_register.step50.models import LocalModels
from doc_register.step50.pipeline import PipelineConfig
from doc_register.step50.recognition import render_crop
from doc_register.step50.storage import atomic_json


def main():
    output = Path('/private/tmp/step50-model-smoke')
    output.mkdir(exist_ok=True)
    models = LocalModels(PipelineConfig(model_timeout=60, max_model_calls=2))
    report = {'dataset': 'synthetic integration only; no accuracy claim', 'tests': []}
    for mode in ['text', 'visual']:
        started = time.monotonic()
        try:
            if mode == 'text':
                result = models.extract({'source_type': 'contract', 'regions': [{'region_id': 'r1', 'text': 'CONTRATO DE ARRENDAMENTO\nSenhorio: Ana Maria Lopes\nAssinado em 20/05/2024.'}]})
                success = isinstance(result.get('candidates'), list)
            else:
                with fitz.open() as document:
                    page = document.new_page(width=400, height=150)
                    page.insert_text((20, 50), 'Assinado em 20/05/2024', fontsize=20)
                    crop, _ = render_crop(page, list(page.rect), dpi=200, max_pixels=2_000_000)
                    result = models.transcribe(crop)
                    success = bool(result['text'])
            atomic_json(output / f'{mode}.json', result)
            report['tests'].append({'mode': mode, 'status': 'passed' if success else 'failed', 'seconds': time.monotonic()-started})
        except Exception as exc:
            report['tests'].append({'mode': mode, 'status': 'failed', 'error': str(exc), 'seconds': time.monotonic()-started})
    atomic_json(output/'report.json', report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
