from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import time

from .config import AppConfig
from .processor import DocumentProcessor


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Local PDF document register powered by Ollama.")
    parser.add_argument("command", choices=["scan", "watch"], help="Run once or keep polling the input folder.")
    parser.add_argument("--config", default="config.json", help="Path to configuration JSON.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    config = AppConfig.from_json(Path(args.config).expanduser().resolve())
    _configure_logging(config.log_dir, args.log_level)
    config.ensure_directories()
    processor = DocumentProcessor(config)

    if args.command == "scan":
        processed = processor.scan_once()
        logging.info("Scan complete. Processed %s new PDF(s).", processed)
        return

    logging.info("Watching %s every %s seconds.", config.input_dir, config.poll_interval_seconds)
    while True:
        processed = processor.scan_once()
        logging.info("Watch cycle complete. Processed %s new PDF(s).", processed)
        time.sleep(config.poll_interval_seconds)


if __name__ == "__main__":
    main()
