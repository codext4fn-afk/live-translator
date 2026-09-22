"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
from pathlib import Path

from . import __version__, config as config_mod

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = PROJECT_ROOT / "logs"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="livetranslator",
        description="Translate your computer's audio in real time and suggest replies.",
    )
    parser.add_argument("--config", type=Path, default=None,
                        help="path to config.toml (default: project root)")
    parser.add_argument("--list-devices", action="store_true",
                        help="list loopback capture devices and exit")
    parser.add_argument("--device", default=None,
                        help="override [audio].device (substring of the device name)")
    parser.add_argument("--model", default=None,
                        help="override [asr].model, e.g. medium or distil-large-v3")
    parser.add_argument("--suggest-model", default=None,
                        help="override [suggest].model, e.g. gemma3:4b")
    parser.add_argument("--language", default=None,
                        help="force the incoming language (ISO code) instead of detecting")
    parser.add_argument("--no-suggest", action="store_true",
                        help="translation only; skip the language model entirely")
    parser.add_argument("--demo", action="store_true",
                        help="show the overlay with sample content; no audio or models")
    parser.add_argument("--cpu", action="store_true",
                        help="run Whisper on the CPU (slow; for debugging GPU problems)")
    parser.add_argument("--debug", action="store_true",
                        help="verbose logging, including filtered transcripts")
    parser.add_argument("--version", action="version", version=f"live-translator {__version__}")
    return parser


def setup_logging(debug: bool) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if debug else logging.INFO

    root = logging.getLogger()
    root.setLevel(level)

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "live-translator.log", maxBytes=1_000_000, backupCount=2,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)-28s %(message)s")
    )
    root.addHandler(file_handler)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    root.addHandler(console)

    # chatty at DEBUG, never useful here
    for noisy in ("httpx", "httpcore", "faster_whisper"):
        logging.getLogger(noisy).setLevel(max(level, logging.INFO))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.debug)
    log = logging.getLogger("livetranslator")

    if args.list_devices:
        from .audio import list_devices

        devices = list_devices()
        if not devices:
            print("No loopback devices found.")
            return 1
        print("Loopback capture devices:\n")
        for device in devices:
            print(f"  {device}")
        print(
            "\nSet [audio].device in config.toml to any distinctive part of a name,\n"
            'for example device = "Headphones".'
        )
        return 0

    cfg, warnings = config_mod.load(args.config)
    for warning in warnings:
        log.warning("config: %s", warning)

    # CLI overrides win over the file
    if args.device is not None:
        cfg.audio.device = args.device
    if args.model is not None:
        cfg.asr.model = args.model
    if args.suggest_model is not None:
        cfg.suggest.model = args.suggest_model
    if args.no_suggest:
        cfg.suggest.enabled = False
    if args.cpu:
        cfg.asr.device, cfg.asr.compute_type = "cpu", "int8"
    if args.language is not None:
        cfg.languages.auto_detect = False
        cfg.languages.source = args.language

    if args.demo:
        from .demo import run as run_demo

        log.info("demo mode - no audio capture, no models")
        run_demo(cfg)
        return 0

    log.info(
        "live-translator %s - whisper=%s device=%s suggest=%s",
        __version__,
        cfg.asr.model,
        cfg.asr.device,
        cfg.suggest.model if cfg.suggest.enabled else "off",
    )

    from .pipeline import run

    try:
        run(cfg)
    except KeyboardInterrupt:
        log.info("interrupted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
