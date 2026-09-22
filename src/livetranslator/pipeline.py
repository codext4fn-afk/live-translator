"""Thread orchestration: speakers -> segments -> text -> translation -> overlay."""

from __future__ import annotations

import collections
import logging
import queue
import threading
import time
from pathlib import Path

from . import config as config_mod
from .audio import AudioError, LoopbackCapture, pick_device
from .config import Config
from .overlay import Card, Overlay, timestamp
from .sounds import SoundBoard
from .suggest import _LANGUAGE_NAMES, Outgoing, Suggester
from .vad import Segmenter, Utterance

log = logging.getLogger(__name__)

_MAX_PENDING_SUGGESTIONS = 3


def _language_code(name: str) -> str:
    """Map a display name back to its ISO code, for the skip-translation list."""
    for code, label in _LANGUAGE_NAMES.items():
        if label.casefold() == name.casefold():
            return code
    return ""


class Pipeline:
    def __init__(self, cfg: Config, overlay: Overlay) -> None:
        self.cfg = cfg
        self.overlay = overlay

        self._stop = threading.Event()
        self._ready = threading.Event()
        # Set once the warmup thread has finished, successfully or not
        self._suggester_ready = threading.Event()
        self._threads: list[threading.Thread] = []

        self._utterances: queue.Queue[Utterance] = queue.Queue(maxsize=8)
        self._work: queue.Queue[tuple[str, str, Card]] = queue.Queue()
        self._outgoing: queue.Queue[tuple[str, str]] = queue.Queue()

        self._capture: LoopbackCapture | None = None
        self._transcriber = None  # set by the loader thread
        self._suggester: Suggester | None = None
        self._segmenter: Segmenter | None = None

        # Recent turns given to the model as context, newest last
        self._history: collections.deque[str] = collections.deque(
            maxlen=max(0, cfg.suggest.context_turns)
        )
        self._history_lock = threading.Lock()

        self._accepted = 0
        self._rejected = 0
        self._last_reject = ""

        overlay.set_copy_hook(self._on_reply_copied)
        overlay.set_pause_hook(self.set_paused)
        overlay.set_quit_hook(self.stop)
        overlay.set_compose_hook(self._on_compose)


    def start(self) -> None:
        self._spawn(self._load, "loader")

    def stop(self) -> None:
        self._stop.set()
        if self._capture is not None:
            self._capture.stop()
        for thread in self._threads:
            thread.join(timeout=2.0)
        if self._suggester is not None:
            self._suggester.close()

    def _guarded_warm(self) -> None:
        try:
            self._warm_suggester()
        except Exception:
            log.exception("suggestion model warmup failed")
        finally:
            self._suggester_ready.set()

    def _await_suggester(self, timeout: float = 120.0) -> Suggester | None:
        """Block until the warmup thread settles, then hand back the suggester."""
        self._suggester_ready.wait(timeout)
        return self._suggester

    def _spawn(self, target, name: str) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        self._threads.append(thread)

    def set_paused(self, paused: bool) -> None:
        if self._capture is not None:
            self._capture.set_paused(paused)

    def set_my_language(self, name: str) -> None:
        """Change the language everything is translated into, and remember it."""
        previous = _language_code(self.cfg.languages.my_language)
        self.cfg.languages.my_language = name
        if self._suggester is not None:
            self._suggester.set_my_language(name)

        code = _language_code(name)
        if code and code != previous:
            # Add the new language, never remove one: replacing these lists wholesale
            # once stopped English being ignored while config.toml still said it was
            for attr in ("ignore_languages", "skip_translation_for"):
                current = list(getattr(self.cfg.languages, attr))
                if code not in current:
                    current.append(code)
                setattr(self.cfg.languages, attr, current)
                config_mod.save_value(self.cfg.source_path, "languages", attr, current)

            allowed = set(self.cfg.languages.allowed)
            if allowed:
                # Keep it detectable, or its speech gets forced into another language
                allowed.add(code)
                self.cfg.languages.allowed = sorted(allowed)
                config_mod.save_value(self.cfg.source_path, "languages", "allowed",
                                      self.cfg.languages.allowed)
                if self._transcriber is not None:
                    self._transcriber.allowed_languages = allowed

        config_mod.save_value(self.cfg.source_path, "languages", "my_language", name)
        log.info("translating into %s; ignoring %s", name,
                 ", ".join(self.cfg.languages.ignore_languages) or "nothing")

    def suppress_capture(self, milliseconds: int) -> None:
        """Ignore captured audio briefly - used while an interface sound plays."""
        if self._capture is not None:
            self._capture.suppress_for(milliseconds)


    def _warm_suggester(self) -> None:
        """Connect to Ollama and page the model into VRAM. Runs off-thread."""
        if not self.cfg.suggest.enabled:
            return
        suggester = Suggester(
            model=self.cfg.suggest.model,
            host=self.cfg.suggest.host,
            my_language=self.cfg.languages.my_language,
            count=self.cfg.suggest.count,
            tone=self.cfg.suggest.tone,
            timeout_s=self.cfg.suggest.timeout_s,
            keep_alive=self.cfg.suggest.keep_alive,
            num_ctx=self.cfg.suggest.num_ctx,
            temperature=self.cfg.suggest.temperature,
        )
        ready, message = suggester.health()
        if not ready:
            log.warning("suggestions disabled: %s", message)
            self._banner(message)
            suggester.close()
            return
        suggester.warmup()
        self._suggester = suggester

    def _load(self) -> None:
        """Build the expensive components, then start the streaming stages."""
        try:
            warm = threading.Thread(
                target=self._guarded_warm, name="warm-suggest", daemon=True
            )
            warm.start()

            self._status(f"loading Whisper {self.cfg.asr.model}… (first run downloads it)")
            from .asr import Transcriber

            self._transcriber = Transcriber(
                model=self.cfg.asr.model,
                device=self.cfg.asr.device,
                compute_type=self.cfg.asr.compute_type,
                cpu_threads=self.cfg.asr.cpu_threads,
                beam_size=self.cfg.asr.beam_size,
                max_no_speech_prob=self.cfg.asr.max_no_speech_prob,
                min_avg_logprob=self.cfg.asr.min_avg_logprob,
                min_chars=self.cfg.asr.min_chars,
                min_language_prob=self.cfg.asr.min_language_prob,
                min_audio_s=self.cfg.asr.min_audio_s,
                allowed_languages=self.cfg.languages.allowed,
            )

            self._status("opening audio…")
            device = pick_device(self.cfg.audio.device)
            self._capture = LoopbackCapture(device)
            self._capture.start()
            if self.cfg.ui.start_paused:
                self._capture.set_paused(True)

        except AudioError as exc:
            log.error("audio setup failed: %s", exc)
            self._banner(str(exc))
            self._status("audio unavailable - see the message above")
            return
        except Exception as exc:  # pragma: no cover - surfaced to the user
            log.exception("startup failed")
            self._banner(f"{exc.__class__.__name__}: {exc}")
            self._status("startup failed - see logs\\live-translator.log")
            return

        # Audio is live now; the suggester may still be warming
        self._ready.set()
        self._spawn(self._segment_loop, "segment")
        self._spawn(self._asr_loop, "asr")
        self._spawn(self._suggest_loop, "suggest")
        # Its own thread so typing stays responsive while a suggestion is generating
        self._spawn(self._compose_loop, "compose")
        self._spawn(self._status_loop, "status")


    def _segment_loop(self) -> None:
        assert self._capture is not None
        audio_cfg = self.cfg.audio
        segmenter = Segmenter(
            vad_aggressiveness=audio_cfg.vad_aggressiveness,
            silence_ms=audio_cfg.silence_ms,
            min_speech_ms=audio_cfg.min_speech_ms,
            max_utterance_ms=audio_cfg.max_utterance_ms,
            pre_roll_ms=audio_cfg.pre_roll_ms,
            noise_gate_dbfs=audio_cfg.noise_gate_dbfs,
            noise_margin_db=audio_cfg.noise_margin_db,
            chunk_after_ms=audio_cfg.chunk_after_ms,
            chunk_silence_ms=audio_cfg.chunk_silence_ms,
        )
        log.info("segmenter using %s", segmenter.backend_name)
        self._segmenter = segmenter
        was_active = False

        while not self._stop.is_set():
            try:
                frame = self._capture.frames.get(timeout=0.2)
            except queue.Empty:
                if segmenter.active:
                    continue
                if was_active:
                    was_active = False
                    self.overlay.post(lambda: self.overlay.set_listening(False))
                continue

            utterance = segmenter.push(frame)

            if segmenter.active != was_active:
                was_active = segmenter.active
                state = was_active
                self.overlay.post(lambda s=state: self.overlay.set_listening(s))

            if utterance is not None:
                try:
                    self._utterances.put_nowait(utterance)
                except queue.Full:
                    log.warning("transcription is behind - dropped an utterance")

    def _asr_loop(self) -> None:
        while not self._stop.is_set():
            try:
                utterance = self._utterances.get(timeout=0.2)
            except queue.Empty:
                continue

            language = None if self.cfg.languages.auto_detect else self.cfg.languages.source
            try:
                result = self._transcriber.transcribe(
                    utterance.samples,
                    language=language,
                    trim_silence=utterance.ended_by_timeout,
                )
            except Exception:
                log.exception("transcription failed")
                continue

            if not result.accepted:
                self._rejected += 1
                self._last_reject = result.reject_reason
                log.info("filtered (%s): %r", result.reject_reason, result.text)
                continue

            code = (result.language or "").lower()
            if code in {c.lower() for c in self.cfg.languages.ignore_languages}:
                log.info("ignored %s(%.2f): %r", code, result.language_prob,
                         result.text[:60])
                continue

            log.info(
                "heard %.1fs %s(%.2f) in %.2fs: %r",
                result.audio_s, result.language, result.language_prob,
                result.decode_s, result.text,
            )

            self._accepted += 1
            translate = code not in {
                c.lower() for c in self.cfg.languages.skip_translation_for
            }
            # Whoever spoke last is who the compose box will translate towards
            if code:
                self.overlay.post(lambda c=code: self.overlay.set_target_language(c))
            meta = f"{timestamp()}  ·  {result.decode_s:.1f}s"
            self._queue_suggestion(result.text, code, meta, translate=translate)

    def _queue_suggestion(self, text: str, code: str, meta: str, *, translate: bool) -> None:
        """Create the card immediately, then hand it to the suggestion stage."""
        holder: dict[str, Card] = {}
        created = threading.Event()

        def build() -> None:
            card = self.overlay.add_card(
                original=text, language=code, confidence=0.0, meta=meta
            )
            if not translate:
                card.hide_translation()
            if self.cfg.suggest.enabled:
                card.awaiting_replies()
            else:
                card.set_error("suggestions are turned off")
            holder["card"] = card
            created.set()

        self.overlay.post(build)
        if not created.wait(timeout=5.0):
            log.warning("overlay did not create a card in time")
            return

        card = holder["card"]
        if not self.cfg.suggest.enabled:
            return

        while self._work.qsize() >= _MAX_PENDING_SUGGESTIONS:
            try:
                _, _, stale = self._work.get_nowait()
            except queue.Empty:
                break
            self.overlay.post(lambda c=stale: c.set_error("skipped - model was busy"))

        self._work.put((text, code, card))

    def _suggest_loop(self) -> None:
        while not self._stop.is_set():
            try:
                text, code, card = self._work.get(timeout=0.2)
            except queue.Empty:
                continue

            suggester = self._await_suggester()
            if suggester is None:
                self.overlay.post(
                    lambda c=card: c.set_error("suggestions unavailable - Ollama not connected")
                )
                continue

            translate = code not in {
                c.lower() for c in self.cfg.languages.skip_translation_for
            }
            with self._history_lock:
                history = list(self._history)

            started = time.monotonic()
            try:
                for event in suggester.stream(
                    text, language_code=code, history=history, translate=translate
                ):
                    if self._stop.is_set():
                        break
                    self._apply(card, event)
            except Exception:
                log.exception("suggestion stream failed")
                self.overlay.post(lambda c=card: c.set_error("suggestion failed - see logs"))
                continue

            elapsed = time.monotonic() - started
            log.info("suggested in %.2fs (%d queued)", elapsed, self._work.qsize())
            if elapsed > 8.0:
                # Almost always the model having been evicted from VRAM
                log.warning("that generation was slow; checking model placement")
                suggester.log_placement()

            self._remember(f"them: {text}")

    def _apply(self, card: Card, event) -> None:
        """Translate a stream event into an overlay mutation."""
        if event.kind == "translation":
            self.overlay.post(lambda t=event.text: card.set_translation(t))
        elif event.kind == "reply" and event.reply is not None:
            self.overlay.post(lambda r=event.reply: card.add_reply(r))
        elif event.kind == "error":
            self.overlay.post(lambda t=event.text: card.set_error(t))
        elif event.kind == "done":
            latency = event.latency_s
            self.overlay.post(lambda: card.finish())
            self.overlay.post(lambda ms=latency: card.set_meta(f"{timestamp()}  ·  {ms:.1f}s"))


    def _on_compose(self, text: str, target_code: str) -> None:
        """Called on the GUI thread - enqueue only, never block the interface."""
        self._outgoing.put((text, target_code))

    def _compose_loop(self) -> None:
        while not self._stop.is_set():
            try:
                text, target_code = self._outgoing.get(timeout=0.2)
            except queue.Empty:
                continue

            suggester = self._await_suggester()
            if suggester is None:
                self.overlay.post(
                    lambda: self.overlay.show_outgoing(
                        Outgoing(error="Ollama is not connected - cannot translate.")
                    )
                )
                continue

            with self._history_lock:
                history = list(self._history)

            try:
                result = suggester.translate_outgoing(
                    text, target_code=target_code, history=history
                )
            except Exception:
                log.exception("outgoing translation failed")
                result = Outgoing(error="translation failed - see logs")

            self.overlay.post(lambda r=result: self.overlay.show_outgoing(r))
            if result.ok:
                self._remember(f"you: {text}")


    def _remember(self, line: str) -> None:
        if self._history.maxlen:
            with self._history_lock:
                self._history.append(line)

    def _on_reply_copied(self, text: str) -> None:
        """A copied reply is what the user is about to say - useful context."""
        self._remember(f"you: {text}")

    def _status_loop(self) -> None:
        while not self._stop.is_set():
            parts = []
            if self._capture is not None:
                name = self._capture.device.name.split("(")[0].strip()
                parts.append("paused" if self._capture.paused else f"listening · {name}")
            if self._transcriber is not None:
                parts.append(f"whisper {self.cfg.asr.model} [{self._transcriber.backend_name}]")
            if self._suggester is not None:
                parts.append(self.cfg.suggest.model)
            else:
                parts.append("no suggestions")
            parts.append(f"{self._accepted} kept / {self._rejected} filtered")
            if self._segmenter is not None:
                parts.append(f"gate {self._segmenter.gate_threshold_dbfs:.0f} dBFS")
            if self._capture is not None and self._capture.dropped_frames:
                parts.append(f"{self._capture.dropped_frames} frames dropped")

            text = "   ·   ".join(parts)
            self.overlay.post(lambda t=text: self.overlay.set_status(t))
            self._stop.wait(1.5)

    def _status(self, text: str) -> None:
        self.overlay.post(lambda: self.overlay.set_status(text))

    def _banner(self, message: str) -> None:
        """Surface a startup problem inside the overlay, not just the log."""

        def build() -> None:
            card = self.overlay.add_card(
                original=message, language="", confidence=0.0, meta=timestamp()
            )
            card.hide_translation()

        self.overlay.post(build)


def run(cfg: Config) -> None:
    """Build the overlay and pipeline, then block on the Tk event loop."""
    board = SoundBoard(
        Path(__file__).resolve().parents[2] / "sounds",
        enabled=cfg.ui.sounds,
        volume=cfg.ui.sound_volume,
    )

    overlay = Overlay(
        width=cfg.ui.width,
        height=cfg.ui.height,
        opacity=cfg.ui.opacity,
        font_size=cfg.ui.font_size,
        max_history=cfg.ui.max_history,
        always_on_top=cfg.ui.always_on_top,
        my_language=cfg.languages.my_language,
        font_family=cfg.ui.font_family,
        sounds=board,
        acrylic=cfg.ui.acrylic,
        gradient=cfg.ui.gradient,
    )
    overlay.set_gradient_hook(
        lambda name: config_mod.save_ui_value(cfg.source_path, "gradient", name)
    )

    pipeline = Pipeline(cfg, overlay)
    # Hooks that call into the pipeline have to be attached after it exists
    overlay.set_language_hook(pipeline.set_my_language)
    # Interface sounds leave the same speakers we record, so mute capture while they play
    board.set_playback_listener(pipeline.suppress_capture)

    if cfg.ui.start_paused:
        overlay.post(overlay.toggle_pause)

    pipeline.start()
    try:
        overlay.run()
    finally:
        pipeline.stop()
        overlay.destroy()
        # Give daemon threads a moment to notice the stop flag before interpreter exit
        time.sleep(0.1)
