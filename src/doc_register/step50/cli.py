from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from .evaluation import annotation_template, evaluate
from .pipeline import Pipeline, PipelineConfig
from .storage import atomic_json, runtime


def main(argv=None):
    parser = argparse.ArgumentParser(description='Step 5.0 — local evidence-first contract extraction')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor', help='Show loaded package, code fingerprint and runtime')
    scan = sub.add_parser('scan')
    scan.add_argument('input', type=Path)
    scan.add_argument('--output', type=Path, required=True)
    scan.add_argument('--config', type=Path)
    scan.add_argument('--force', action='store_true', help='New immutable run; preserve prior outputs and reviews')
    scan.add_argument('--rules-only', action='store_true', help='Disable optional models, keep OCR and explicit degradation')
    evaluate_parser = sub.add_parser('evaluate')
    evaluate_parser.add_argument('manifest', type=Path)
    evaluate_parser.add_argument('predictions', type=Path, help='JSON object: annotation document id -> result.json path')
    evaluate_parser.add_argument('--output', type=Path, required=True)
    evaluate_parser.add_argument('--split', choices=['development', 'calibration', 'test'], default='test')
    evaluate_parser.add_argument('--after-human-review', action='store_true')
    annotate = sub.add_parser('annotate')
    annotate.add_argument('result', type=Path)
    annotate.add_argument('--output', required=True, type=Path)
    review = sub.add_parser('review')
    review.add_argument('result', type=Path)
    review.add_argument('decisions', type=Path)
    review.add_argument('--reviewer', required=True)
    export = sub.add_parser('export')
    export.add_argument('results', type=Path, nargs='+')
    export.add_argument('--output', required=True, type=Path)
    export.add_argument('--output-format', choices=['legacy', 'normalized', 'both'], default='both')
    export.add_argument('--reference-mg', default='')
    args = parser.parse_args(argv)
    if args.command == 'doctor':
        print(json.dumps(runtime(), indent=2))
    elif args.command == 'scan':
        from dataclasses import replace
        active = runtime()
        print(f"Step 5.0: package={active['package_path']} version={active['version']} code={active['code_sha256']}", file=sys.stderr)
        config = PipelineConfig.read(args.config)
        if args.rules_only:
            config = replace(config, text_enabled=False, vision_enabled=False)
        pipeline = Pipeline(args.output, config)
        if not args.input.exists():
            parser.error('input path does not exist')
        paths = [args.input] if args.input.is_file() else sorted(p for p in args.input.resolve().rglob('*') if p.suffix.lower() == '.pdf' and not p.is_relative_to(args.output.resolve()))
        if not paths:
            parser.error('no PDF files found')
        failures = []
        for path in paths:
            try:
                result = pipeline.process(path, force=args.force)
                print(result, flush=True)
            except Exception as exc:
                failures.append({'file': str(path), 'error': str(exc)})
        if failures:
            atomic_json(args.output / 'batch-errors.json', failures)
            raise SystemExit(1)
    elif args.command == 'evaluate':
        mapping = json.loads(args.predictions.read_text())
        predictions = {key: json.loads((args.predictions.parent / path).read_text()) for key, path in mapping.items()}
        report = evaluate(json.loads(args.manifest.read_text()), predictions, split=args.split, human=args.after_human_review)
        atomic_json(args.output, report)
        print(json.dumps(report['micro'], indent=2))
    elif args.command == 'annotate':
        if args.output.exists():
            parser.error('annotation destination already exists')
        atomic_json(args.output, annotation_template(json.loads(args.result.read_text())))
    elif args.command == 'review':
        from .review import apply_decisions
        print(apply_decisions(args.result, args.decisions, args.reviewer))
    elif args.command == 'export':
        from .export import export_results
        print(export_results(args.results, args.output, args.output_format, args.reference_mg))


if __name__ == '__main__':
    main()
