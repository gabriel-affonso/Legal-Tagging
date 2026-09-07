from __future__ import annotations

import argparse
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
    args = parser.parse_args(argv)
    if args.command == "scan":
        config = PipelineConfig.read(args.config)
        if args.no_rules:
            from dataclasses import replace
            config = replace(config, rules_enabled=False)
        if not args.input.exists():
            parser.error("input path does not exist")
        paths = [args.input] if args.input.is_file() else sorted(args.input.rglob("*.pdf"))
        if not paths:
            parser.error("no PDF files found")
        for path in paths:
            print(Pipeline(args.output, config).process(path, force=args.force))


if __name__ == "__main__":
    main()
