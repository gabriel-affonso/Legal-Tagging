from __future__ import annotations

import argparse
from dataclasses import replace
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import time

from .config import AppConfig
from .processor import DocumentProcessor
from .property_processor import PropertyExtractionProcessor
from .rent_processor import LeaseRentExtractionProcessor
from .document_ai_v2 import DocumentAIV2Pipeline, V2Config


def _configure_logging(log_dir: Path, log_level: str) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level))
    root.handlers.clear()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        log_dir / "processing.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local PDF document register powered by Ollama.")
    parser.add_argument(
        "command",
        nargs="?",
        default="scan",
        choices=["scan", "watch", "property-scan", "property-watch", "rent-scan", "rent-inspect", "normalize"],
        help="Run the main register or the independent property extraction pipeline.",
    )
    parser.add_argument("--config", default="config.json", help="Path to configuration JSON.")
    parser.add_argument("--pdf", type=Path, help="With rent-inspect, PDF to inspect without running OCR models.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument(
        "--step",
        choices=["3.6", "3.7"],
        help="Select the compatible pipeline step. Step 3.7 enables focused core extraction.",
    )
    parser.add_argument(
        "--focused-core-extraction",
        action="store_true",
        help="Alias for --step 3.7: use contract pages 1-3 plus the first valid caderneta page.",
    )
    parser.add_argument(
        "--last",
        type=int,
        metavar="N",
        help="With Step 3.7, reprocess the last N registered PDFs in place.",
    )
    parser.add_argument(
        "--focused-core-extraction-last",
        type=int,
        metavar="N",
        help="Reprocess the last N registered PDFs with focused core extraction.",
    )
    parser.add_argument(
        "--reprocess-cadernetas",
        action="store_true",
        help="Reprocess existing main-register PDFs and replace their rows to apply internal caderneta extraction.",
    )
    parser.add_argument(
        "--vision-recall",
        action="store_true",
        help="Force the Step 3.6 targeted Vision Recall pass for this scan.",
    )
    parser.add_argument(
        "--vision-recall-last",
        type=int,
        metavar="N",
        help="Revalidate the last N registered PDFs using targeted Vision Recall.",
    )
    parser.add_argument(
        "--property-table",
        action="store_true",
        help=(
            "Write the main pipeline to the 'Property Table' worksheet with one row per "
            "resolved property instead of one row per contract."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="With rent-scan, re-extract contracts already present in 'Rent Extraction'.",
    )
    parser.add_argument(
        "--pipeline",
        choices=["legacy", "v2"],
        default="legacy",
        help="With rent-scan, choose the historical text path or Document AI v2 visual reconstruction.",
    )
    parser.add_argument(
        "--output-format",
        choices=["legacy", "normalized", "both"],
        help="Step 4.0 output: historical register, normalized workbook, or both.",
    )
    return parser


def main() -> None:
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "step5":
        from .step50.cli import main as step5_main
        step5_main(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "step55":
        from .step55.cli import main as step55_main
        step55_main(sys.argv[2:])
        return
    parser = _build_parser()
    args = parser.parse_args()

    if args.property_table and args.command in {"property-scan", "property-watch"}:
        parser.error("--property-table is available only with scan or watch")
    if args.force and args.command != "rent-scan":
        parser.error("--force is available only with rent-scan")
    if args.pipeline != "legacy" and args.command != "rent-scan":
        parser.error("--pipeline is available only with rent-scan")
    if args.command == "rent-inspect" and args.pdf is None:
        parser.error("rent-inspect requires --pdf /path/to/contract.pdf")
    if args.vision_recall_last is not None and args.vision_recall_last <= 0:
        parser.error("--vision-recall-last must be greater than zero")
    if args.last is not None and args.last <= 0:
        parser.error("--last must be greater than zero")
    if args.focused_core_extraction_last is not None and args.focused_core_extraction_last <= 0:
        parser.error("--focused-core-extraction-last must be greater than zero")
    focused = bool(
        args.step == "3.7"
        or args.focused_core_extraction
        or args.focused_core_extraction_last is not None
    )
    if args.step == "3.6" and focused:
        parser.error("--step 3.6 cannot be combined with Step 3.7 flags")
    if args.last is not None and not focused:
        parser.error("--last is available only with --step 3.7 or --focused-core-extraction")
    if args.last is not None and args.focused_core_extraction_last is not None:
        parser.error("Use either --last or --focused-core-extraction-last, not both")
    if focused and args.command in {"property-scan", "property-watch"}:
        parser.error("Step 3.7 is available only with scan or watch")

    config = AppConfig.from_json(Path(args.config).expanduser().resolve())
    if args.output_format:
        config = replace(config, output_format=args.output_format)
    if config.output_format not in {"legacy", "normalized", "both"}:
        parser.error("config.json: output_format must be legacy, normalized, or both")
    _configure_logging(config.log_dir, args.log_level)
    config.ensure_directories()
    if args.command == "rent-inspect":
        inspector = DocumentAIV2Pipeline(config.processing_dir / "document-ai-v2-cache", V2Config())
        for feature in inspector.inspect(args.pdf.expanduser().resolve()):
            logging.info("page=%s route=%s text_quality=%.2f chars=%s images=%s", feature.page, inspector.router.classify(feature), feature.text_layer_quality, feature.text_chars, feature.image_count)
        return
    if args.command == "normalize":
        counts = DocumentProcessor(config).normalized_register.process_pending()
        logging.info("Step 4.0 normalized pending entries: %s", counts)
        return
    if args.command == "rent-scan":
        processed = LeaseRentExtractionProcessor(config, pipeline=args.pipeline).scan_once(force=args.force)
        logging.info("Rent clause scan complete. Processed %s eligible contract(s).", processed)
        return
    if args.vision_recall or args.vision_recall_last is not None:
        config = replace(config, vision_enabled=True, vision_recall_mode=True)
    if focused:
        config = replace(config, step_3_7_enabled=True)
    elif args.step == "3.6":
        config = replace(config, step_3_7_enabled=False)
    if args.vision_recall_last is not None:
        processor = DocumentProcessor(config)
        processed = processor.vision_recall_last(args.vision_recall_last)
        logging.info("Vision Recall complete. Revalidated %s registered PDF(s).", processed)
        return
    if args.command in {"property-scan", "property-watch"}:
        processor = PropertyExtractionProcessor(config)
        if args.command == "property-scan":
            processed = processor.scan_once()
            logging.info("Property scan complete. Processed %s new PDF(s).", processed)
            return

        logging.info(
            "Watching %s for independent property extraction every %s seconds.",
            config.input_dir,
            config.poll_interval_seconds,
        )
        while True:
            processed = processor.scan_once()
            logging.info("Property watch cycle complete. Processed %s new PDF(s).", processed)
            time.sleep(config.poll_interval_seconds)

    processor = DocumentProcessor(config)

    focused_last = args.focused_core_extraction_last or args.last
    if focused_last is not None:
        processed = processor.focused_extraction_last(focused_last)
        logging.info("Step 3.7 complete. Reprocessed %s registered PDF(s).", processed)
        return

    if args.command == "scan":
        processed = processor.scan_once(
            reprocess_cadernetas=args.reprocess_cadernetas,
            property_table=args.property_table,
        )
        logging.info("Scan complete. Processed %s new PDF(s).", processed)
        return

    logging.info("Watching %s every %s seconds.", config.input_dir, config.poll_interval_seconds)
    while True:
        processed = processor.scan_once(property_table=args.property_table)
        logging.info("Watch cycle complete. Processed %s new PDF(s).", processed)
        time.sleep(config.poll_interval_seconds)


if __name__ == "__main__":
    main()
