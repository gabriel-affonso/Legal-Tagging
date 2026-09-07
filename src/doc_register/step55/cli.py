from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import Pipeline, PipelineConfig


def main(argv=None):
    parser = argparse.ArgumentParser(description="Step 5.5 — canonical local semantic extraction pilot")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("input", type=Path)
    scan.add_argument("--output", type=Path, required=True)
    scan.add_argument("--config", type=Path)
    scan.add_argument("--force", action="store_true")
    scan.add_argument("--no-rules", action="store_true")
    scan.add_argument('--text-enabled',action='store_true')
    scan.add_argument('--model')
    sub.add_parser('doctor')
    reset=sub.add_parser('reset-model-circuit');reset.add_argument('--output',type=Path,required=True)
    links=sub.add_parser('link-results');links.add_argument('results',type=Path,nargs='+');links.add_argument('--output',type=Path,required=True)
    mapping=sub.add_parser('field-map');mapping.add_argument('--output',type=Path,required=True)
    mapping.add_argument('--workbook',type=Path)
    replay=sub.add_parser('replay');replay.add_argument('legacy',type=Path);replay.add_argument('--source',type=Path);replay.add_argument('--output',type=Path,required=True);replay.add_argument('--config',type=Path)
    review=sub.add_parser('review');review.add_argument('result',type=Path);review.add_argument('decisions',type=Path);review.add_argument('--reviewer',required=True)
    export=sub.add_parser('export-staging');export.add_argument('results',type=Path,nargs='+');export.add_argument('--output',type=Path,required=True)
    annotate=sub.add_parser('annotate');annotate.add_argument('result',type=Path);annotate.add_argument('--output',type=Path,required=True)
    evaluation=sub.add_parser('evaluate');evaluation.add_argument('manifest',type=Path);evaluation.add_argument('predictions',type=Path);evaluation.add_argument('--output',type=Path,required=True);evaluation.add_argument('--split',default='test',choices=['development','calibration','test'])
    args = parser.parse_args(argv)
    from .storage import atomic_json,runtime
    from .canonical import CanonicalResult
    if args.command=='doctor':print(json.dumps(runtime(),indent=2));return
    if args.command=='reset-model-circuit':
        from .storage import locked
        with locked(args.output/'.ollama.lock',timeout=5):(args.output/'ollama-circuit.json').unlink(missing_ok=True)
        return
    if args.command=='link-results':
        from .dossier import link_results
        print(link_results(args.results,args.output));return
    if args.command=='field-map':
        from .field_map import field_catalog
        atomic_json(args.output,field_catalog(args.workbook));return
    if args.command=='replay':print(Pipeline(args.output,PipelineConfig.read(args.config)).replay(args.legacy,source=args.source));return
    if args.command=='review':
        from .review import apply_decisions
        print(apply_decisions(args.result,args.decisions,args.reviewer));return
    if args.command=='export-staging':
        from .export import export_staging
        print(export_staging(args.results,args.output));return
    if args.command=='annotate':
        from .evaluation import annotation_template
        if args.output.exists():parser.error('annotation_destination_exists')
        atomic_json(args.output,annotation_template(CanonicalResult.model_validate_json(args.result.read_text())));return
    if args.command=='evaluate':
        from .evaluation import evaluate
        predictions={k:CanonicalResult.model_validate_json((args.predictions.parent/v).read_text()) for k,v in json.loads(args.predictions.read_text()).items()}
        atomic_json(args.output,evaluate(json.loads(args.manifest.read_text()),predictions,args.split));return
    if args.command == "scan":
        config = PipelineConfig.read(args.config)
        from dataclasses import replace
        if args.text_enabled:config=replace(config,text_enabled=True)
        if args.model:config=replace(config,text_model=args.model)
        if args.no_rules:
            from dataclasses import replace
            config = replace(config, rules_enabled=False)
        if not args.input.exists():
            parser.error("input path does not exist")
        paths = [args.input] if args.input.is_file() else sorted(p for p in args.input.resolve().rglob('*') if p.suffix.lower()=='.pdf' and not p.is_relative_to(args.output.resolve()))
        if not paths:
            parser.error("no PDF files found")
        failures=[]
        for path in paths:
            try:print(Pipeline(args.output, config).process(path, force=args.force))
            except Exception as exc:failures.append({'path':str(path),'error':str(exc)})
        if failures:
            atomic_json(args.output/'batch-errors.json',failures)
            raise SystemExit(1)


if __name__ == "__main__":
    main()
