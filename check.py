"""Preflight check: verifies every moving part before you rely on the overlay."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

OK = "  ok  "
BAD = " FAIL "
WARN = " warn "


def line(status: str, label: str, detail: str = "") -> None:
    print(f"[{status}] {label}" + (f" - {detail}" if detail else ""))


def main() -> int:
    failures = 0
    print("live-translator preflight\n" + "=" * 60)

    try:
        from livetranslator import config as config_mod

        cfg, warnings = config_mod.load()
        line(OK, "config", str(cfg.source_path or "built-in defaults"))
        for w in warnings:
            line(WARN, "config", w)
    except Exception as exc:
        line(BAD, "config", repr(exc))
        return 1

    for module, label in [
        ("numpy", "numpy"),
        ("soxr", "soxr"),
        ("httpx", "httpx"),
        ("pyaudiowpatch", "PyAudioWPatch"),
        ("webrtcvad", "webrtcvad"),
        ("faster_whisper", "faster-whisper"),
        ("PySide6", "PySide6"),
    ]:
        try:
            mod = __import__(module)
            line(OK, label, getattr(mod, "__version__", ""))
        except Exception as exc:
            line(BAD, label, repr(exc))
            failures += 1

    from livetranslator import bootstrap

    dll_dirs = bootstrap.prepare_cuda()
    line(OK if dll_dirs else WARN, "CUDA DLL path", f"{len(dll_dirs)} dir(s) registered")
    cuda_ok, cuda_msg = bootstrap.cuda_available()
    if cuda_ok:
        line(OK, "CUDA device", cuda_msg)
    else:
        line(WARN, "CUDA device", f"{cuda_msg} - Whisper will fall back to CPU")

    try:
        from livetranslator import audio

        devices = audio.list_devices()
        if not devices:
            line(BAD, "loopback devices", "none found")
            failures += 1
        else:
            chosen = audio.pick_device(cfg.audio.device)
            line(OK, "loopback devices", f"{len(devices)} found")
            line(OK, "capture device", str(chosen))
    except Exception as exc:
        line(BAD, "audio", repr(exc))
        failures += 1

    if cfg.suggest.enabled:
        try:
            from livetranslator.suggest import Suggester

            suggester = Suggester(
                model=cfg.suggest.model,
                host=cfg.suggest.host,
                my_language=cfg.languages.my_language,
                count=cfg.suggest.count,
            )
            ready, msg = suggester.health()
            line(OK if ready else BAD, "ollama", msg)
            if not ready:
                failures += 1
            suggester.close()
        except Exception as exc:
            line(BAD, "ollama", repr(exc))
            failures += 1
    else:
        line(WARN, "ollama", "suggest.enabled = false - translation only")

    try:
        from PySide6.QtCore import qVersion
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        screen = app.primaryScreen()
        detail = f"Qt {qVersion()}"
        if screen is not None:
            size = screen.availableGeometry()
            detail += f", screen {size.width()}x{size.height()}"
        line(OK, "Qt GUI", detail)
    except Exception as exc:
        line(BAD, "Qt GUI", repr(exc))
        failures += 1

    print("=" * 60)
    if failures:
        print(f"{failures} check(s) failed. The overlay will not start correctly.")
    else:
        print("All checks passed. Run .\\run.ps1 to start.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
