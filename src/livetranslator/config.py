"""Typed configuration loaded from config.toml, with defaults for every field."""

from __future__ import annotations

import json
import logging
import os
import re
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, get_type_hints
from urllib.parse import urlsplit

from . import languages

log = logging.getLogger(__name__)

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass
class LanguageConfig:
    my_language: str = "English"
    auto_detect: bool = True
    source: str = ""
    skip_translation_for: list[str] = field(default_factory=lambda: ["en"])
    ignore_languages: list[str] = field(default_factory=lambda: ["en"])
    allowed: list[str] = field(
        default_factory=lambda: ["en", "fr", "es", "pt", "de", "it", "nl", "ar",
                                 "ru", "tr", "pl", "id", "ja"]
    )


@dataclass
class AsrConfig:
    model: str = "auto"
    device: str = "auto"
    compute_type: str = "auto"
    cpu_threads: int = 0
    beam_size: int = 3
    max_no_speech_prob: float = 0.60
    min_avg_logprob: float = -1.00
    min_chars: int = 2
    min_language_prob: float = 0.55
    min_audio_s: float = 0.55


@dataclass
class AudioConfig:
    device: str = ""
    vad_aggressiveness: int = 2
    silence_ms: int = 700
    min_speech_ms: int = 500
    max_utterance_ms: int = 9000
    pre_roll_ms: int = 300
    chunk_after_ms: int = 2500
    chunk_silence_ms: int = 150
    noise_gate_dbfs: float = -55.0
    noise_margin_db: float = 6.0


@dataclass
class SuggestConfig:
    enabled: bool = True
    # auto picks the strongest chat model you have in Ollama that fits your GPU
    model: str = "auto"
    host: str = "http://127.0.0.1:11434"
    count: int = 3
    tone: str = "casual and friendly, like a teenager in a game voice chat"
    context_turns: int = 6
    timeout_s: int = 30
    keep_alive: str = "1h"
    num_ctx: int = 2048
    temperature: float = 0.6


@dataclass
class UiConfig:
    opacity: float = 0.96
    width: int = 620
    height: int = 620
    font_size: int = 13
    max_history: int = 40
    always_on_top: bool = True
    start_paused: bool = False
    font_family: str = ""
    sounds: bool = True
    sound_volume: float = 0.6
    animations: bool = True
    acrylic: bool = False
    gradient: str = "graphite"


@dataclass
class Config:
    languages: LanguageConfig = field(default_factory=LanguageConfig)
    asr: AsrConfig = field(default_factory=AsrConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    suggest: SuggestConfig = field(default_factory=SuggestConfig)
    ui: UiConfig = field(default_factory=UiConfig)

    # Populated by load(); not read from the file
    source_path: Path | None = None


def _coerce(value: Any, target_type: Any, where: str) -> Any:
    """Convert a TOML value to the dataclass field type, rejecting nonsense."""
    if target_type is bool:
        if isinstance(value, bool):
            return value
        raise ValueError(f"{where}: expected true/false, got {value!r}")
    if target_type is int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{where}: expected a number, got {value!r}")
        return int(value)
    if target_type is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{where}: expected a number, got {value!r}")
        return float(value)
    if target_type is str:
        if not isinstance(value, str):
            raise ValueError(f"{where}: expected a string, got {value!r}")
        return value
    # list[str]
    if getattr(target_type, "__origin__", None) is list:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"{where}: expected a list of strings, got {value!r}")
        return list(value)
    return value


@lru_cache(maxsize=None)
def _hints(cls: type) -> dict[str, Any]:
    """Resolve real types for a dataclass."""
    return get_type_hints(cls)


def _fill(section_obj: Any, data: dict[str, Any], section_name: str) -> list[str]:
    """Overlay `data` onto a dataclass instance. Returns warnings for unknown keys."""
    resolved = _hints(type(section_obj))
    known = {f.name: resolved[f.name] for f in fields(section_obj)}
    warnings: list[str] = []
    for key, value in data.items():
        if key not in known:
            warnings.append(f"unknown key [{section_name}].{key} - ignored")
            continue
        setattr(section_obj, key, _coerce(value, known[key], f"[{section_name}].{key}"))
    return warnings


def load(path: str | Path | None = None) -> tuple[Config, list[str]]:
    """Load configuration. """
    cfg = Config()
    warnings: list[str] = []

    if path is None:
        path = Path(__file__).resolve().parents[2] / "config.toml"
    path = Path(path)

    if not path.is_file():
        warnings.append(f"no config file at {path} - using built-in defaults")
        return cfg, warnings

    # A broken file used to crash the app before any window opened, and with
    # pythonw there is no console, so it just looked like nothing happened.
    # Fall back to defaults instead and leave source_path empty so the app
    # never writes over a file the user might want to fix by hand
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        warnings.append(f"could not read {path.name} ({exc}) - using built-in defaults")
        return cfg, warnings

    cfg.source_path = path
    for f in fields(cfg):
        if f.name == "source_path":
            continue
        section = raw.get(f.name)
        if section is None:
            continue
        if not isinstance(section, dict):
            warnings.append(f"[{f.name}] is not a table - ignored")
            continue
        warnings.extend(_fill(getattr(cfg, f.name), section, f.name))

    for key in raw:
        if key not in {f.name for f in fields(cfg)} and isinstance(raw[key], dict):
            warnings.append(f"unknown section [{key}] - ignored")

    warnings.extend(_validate(cfg))
    return cfg, warnings


def _toml_string(value: str) -> str:
    # JSON string escaping is also valid TOML, so quotes, backslashes and
    # newlines in a value can no longer break the file
    return json.dumps(str(value), ensure_ascii=False)


def _value_end(text: str, start: int) -> int:
    """Find where a value that starts at `start` finishes.

    Most values end at the end of their line, but an array like `allowed`
    is often spread over several lines. The old code only replaced the first
    line and left the rest behind, which broke the whole file. This walks
    forward counting brackets and skipping anything inside quotes, and stops
    before a trailing comment so that comment survives
    """
    depth = 0
    quote = ""
    i = start
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        elif ch == "#" and depth <= 0:
            break
        elif ch == "\n" and depth <= 0:
            break
        i += 1
    # leave the spaces in front of a trailing comment where they were
    if i < len(text) and text[i] == "#":
        while i > start and text[i - 1] in " \t":
            i -= 1
    return i


def save_value(path: Path | None, section: str, key: str,
               value: str | list[str]) -> bool:
    """Persist one setting, a string or a list, preserving comments and layout."""
    if path is None or not Path(path).is_file():
        return False
    path = Path(path)

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        log.warning("could not read %s to save %s", path, key)
        return False

    if isinstance(value, (list, tuple)):
        rendered = "[" + ", ".join(_toml_string(v) for v in value) + "]"
    else:
        rendered = _toml_string(value)

    key_re = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=[ \t]*", re.MULTILINE)
    section_re = re.compile(rf"^[ \t]*\[{re.escape(section)}\][ \t]*$", re.MULTILINE)

    found = section_re.search(text)
    if found is None:
        text = f"{text.rstrip()}\n\n[{section}]\n{key} = {rendered}\n"
    else:
        # Only look inside this section, the same key name can exist in another one
        start = found.end()
        next_section = re.search(r"^[ \t]*\[[A-Za-z]", text[start:], re.MULTILINE)
        end = start + next_section.start() if next_section else len(text)

        match = key_re.search(text, start, end)
        if match:
            value_end = _value_end(text, match.end())
            text = text[:match.end()] + rendered + text[value_end:]
        else:
            block = text[start:end].rstrip("\n") + f"\n{key} = {rendered}\n\n"
            text = text[:start] + block + text[end:]

    # Make sure what we are about to write still parses, so a bug in here can
    # never leave the user with a config the app refuses to load
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        log.warning("not saving %s, the result would not parse: %s", key, exc)
        return False

    # Write to a temp file first and swap it in, so a crash halfway through
    # cannot leave a half written config behind
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        log.warning("could not write %s", path)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def save_ui_value(path: Path | None, key: str, value: str) -> bool:
    """Convenience wrapper for the ``[ui]`` section."""
    return save_value(path, "ui", key, value)


def _clean_codes(values: list[str], where: str, notes: list[str]) -> list[str]:
    """Drop language codes Whisper cannot use, fixing the ones with a known spelling."""
    cleaned: list[str] = []
    for raw in values:
        code = languages.normalise(raw)
        if not code:
            notes.append(f"{where}: {raw!r} is not a language Whisper knows - ignored")
            continue
        if code != raw.strip().lower():
            notes.append(f"{where}: {raw!r} read as {code!r} ({languages.name(code)})")
        if code not in cleaned:
            cleaned.append(code)
    return cleaned


def _validate(cfg: Config) -> list[str]:
    """Clamp out-of-range values rather than crashing, and report what changed."""
    notes: list[str] = []

    lang = cfg.languages
    lang.allowed = _clean_codes(lang.allowed, "languages.allowed", notes)
    lang.ignore_languages = _clean_codes(
        lang.ignore_languages, "languages.ignore_languages", notes
    )
    lang.skip_translation_for = _clean_codes(
        lang.skip_translation_for, "languages.skip_translation_for", notes
    )
    if lang.source:
        lang.source = _clean_codes([lang.source], "languages.source", notes)
        lang.source = lang.source[0] if lang.source else ""

    if not 0 <= cfg.audio.vad_aggressiveness <= 3:
        notes.append(
            f"vad_aggressiveness {cfg.audio.vad_aggressiveness} out of range 0-3 - clamped"
        )
        cfg.audio.vad_aggressiveness = min(3, max(0, cfg.audio.vad_aggressiveness))

    if not 0.3 <= cfg.ui.opacity <= 1.0:
        notes.append(f"ui.opacity {cfg.ui.opacity} out of range 0.3-1.0 - clamped")
        cfg.ui.opacity = min(1.0, max(0.3, cfg.ui.opacity))

    if cfg.audio.silence_ms < 100:
        notes.append("audio.silence_ms below 100 would chop words apart - raised to 100")
        cfg.audio.silence_ms = 100

    if cfg.audio.max_utterance_ms < cfg.audio.min_speech_ms:
        notes.append("audio.max_utterance_ms below min_speech_ms - raised to 5000")
        cfg.audio.max_utterance_ms = 5000

    if cfg.suggest.count < 1:
        notes.append("suggest.count below 1 - raised to 1")
        cfg.suggest.count = 1
    elif cfg.suggest.count > 6:
        notes.append("suggest.count above 6 hurts latency - lowered to 6")
        cfg.suggest.count = 6

    if cfg.asr.device not in {"auto", "cuda", "cpu"}:
        notes.append(f"asr.device {cfg.asr.device!r} unrecognised - using 'auto'")
        cfg.asr.device = "auto"

    if cfg.asr.compute_type not in {"auto", "float16", "float32", "int8",
                                    "int8_float16", "int8_float32", "bfloat16"}:
        notes.append(f"asr.compute_type {cfg.asr.compute_type!r} unrecognised - "
                     "using 'auto'")
        cfg.asr.compute_type = "auto"

    if cfg.asr.cpu_threads < 0:
        notes.append("asr.cpu_threads below 0 - using 0 (automatic)")
        cfg.asr.cpu_threads = 0

    if cfg.asr.beam_size < 1:
        notes.append("asr.beam_size below 1 - raised to 1")
        cfg.asr.beam_size = 1

    # These were never checked before. A max_history of 0 deleted every card
    # the moment it appeared, and a zero timeout made every request fail
    def clamp(section: Any, key: str, low: float, high: float) -> None:
        value = getattr(section, key)
        fixed = min(high, max(low, value))
        if fixed != value:
            name = f"{type(section).__name__.removesuffix('Config').lower()}.{key}"
            notes.append(f"{name} {value} out of range {low}-{high} - using {fixed}")
            setattr(section, key, type(value)(fixed))

    clamp(cfg.ui, "sound_volume", 0.0, 1.0)
    clamp(cfg.ui, "max_history", 1, 500)
    clamp(cfg.ui, "font_size", 8, 32)
    clamp(cfg.ui, "width", 420, 4000)
    clamp(cfg.ui, "height", 340, 4000)
    clamp(cfg.suggest, "timeout_s", 5, 600)
    clamp(cfg.suggest, "num_ctx", 512, 131072)
    clamp(cfg.suggest, "temperature", 0.0, 2.0)
    clamp(cfg.suggest, "context_turns", 0, 50)

    if not cfg.suggest.model.strip():
        cfg.suggest.model = "auto"

    # Everything you hear gets sent to this address. Plain http to another
    # machine means the conversation crosses the network unencrypted
    host = urlsplit(cfg.suggest.host)
    if host.scheme not in ("http", "https") or not host.hostname:
        notes.append(f"suggest.host {cfg.suggest.host!r} is not a valid URL - "
                     "using http://127.0.0.1:11434")
        cfg.suggest.host = "http://127.0.0.1:11434"
    elif host.scheme == "http" and host.hostname not in _LOCAL_HOSTS:
        notes.append(f"suggest.host points at {host.hostname} over plain http, so "
                     "everything you hear is sent across the network unencrypted")

    if not cfg.languages.auto_detect and not cfg.languages.source:
        notes.append(
            "auto_detect is off but languages.source is empty - re-enabling auto_detect"
        )
        cfg.languages.auto_detect = True

    return notes


assert is_dataclass(Config)
