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
        choices=["scan", "watch", "property-scan", "property-watch"],
        help="Run the main register or the independent property extraction pipeline.",
    )
    parser.add_argument("--config", default="config.json", help="Path to configuration JSON.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
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
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.property_table and args.command in {"property-scan", "property-watch"}:
        parser.error("--property-table is available only with scan or watch")
    if args.vision_recall_last is not None and args.vision_recall_last <= 0:
        parser.error("--vision-recall-last must be greater than zero")

    config = AppConfig.from_json(Path(args.config).expanduser().resolve())
    _configure_logging(config.log_dir, args.log_level)
    config.ensure_directories()
    if args.vision_recall or args.vision_recall_last is not None:
        config = replace(config, vision_enabled=True, vision_recall_mode=True)
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
