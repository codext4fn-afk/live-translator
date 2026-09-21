"""Whisper transcription via faster-whisper (CTranslate2)."""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass

import numpy as np

from . import bootstrap
from .audio import TARGET_RATE

log = logging.getLogger(__name__)


@dataclass
class Transcript:
    text: str
    language: str
    language_prob: float
    avg_logprob: float
    no_speech_prob: float
    compression_ratio: float
    audio_s: float
    decode_s: float
    accepted: bool = True
    reject_reason: str = ""


# Phrases Whisper emits when the audio contains no speech.
_HALLUCINATION_SUBSTRINGS = (
    "thanks for watching",
    "thank you for watching",
    "subscribe to my channel",
    "like and subscribe",
    "amara.org",
    "subtitles by",
    "subtitled by",
    "captions by",
    "transcription by",
    "subtitles created by",
    "sous-titres réalisés par",
    "sous-titrage société radio-canada",
    "subtítulos realizados por",
    "subtítulos por",
    "legendas pela comunidade",
    "untertitel im auftrag des zdf",
    "untertitelung aufgrund der audiodeskription",
    "ご視聴ありがとうございました",
    "ご視聴ありがとうございます",
    "시청해주셔서 감사합니다",
    "구독과 좋아요",
    "字幕由amara.org社区提供",
    "请不吝点赞",
    "明镜与点点栏目",
    "спасибо за просмотр",
    "продолжение следует",
)

_HALLUCINATION_EXACT = (
    "thank you",
    "thank you.",
    "you",
    "bye",
    "bye bye",
    "okay",
    "music",
    "applause",
    "laughter",
    "silence",
    "outro",
    "intro",
    "foreign",
    "beep",
)

# Bracketed non-speech annotations: [MUSIC], (applause), ♪...♪
_ANNOTATION_RE = re.compile(r"[\[\(\<♪*].{0,40}?[\]\)\>♪*]")
_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _ANNOTATION_RE.sub(" ", text)
    text = _PUNCT_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip().casefold()


# The blocklists are written above in readable form, but matching happens against
# normalised text.
_BLOCK_SUBSTRINGS: tuple[str, ...] = tuple(
    sorted({n for n in map(_normalise, _HALLUCINATION_SUBSTRINGS) if n})
)
_BLOCK_EXACT: frozenset[str] = frozenset(
    n for n in map(_normalise, _HALLUCINATION_EXACT) if n
)


def _is_repetitive(text: str, *, max_ratio: float = 0.45) -> bool:
    """Detect the degenerate "word word word word" decode."""
    tokens = text.split()
    if len(tokens) < 6:
        return False
    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    return max(counts.values()) / len(tokens) > max_ratio


# Markers that distinguish "this machine cannot use the GPU" from any other startup
# failure. Matched case-insensitively against the exception text.
_CUDA_FAILURE_MARKERS = (
    "cuda",
    "cudnn",
    "cublas",
    "cudart",
    "dll load failed",
    "no kernel image",
    "out of memory",
    "device ordinal",
    "unsupported device",
)


def _is_cuda_failure(exc: BaseException) -> bool:
    text = f"{exc.__class__.__name__}: {exc}".casefold()
    return any(marker in text for marker in _CUDA_FAILURE_MARKERS)


class Transcriber:
    """Loads Whisper once and transcribes utterances on the calling thread."""

    def __init__(
        self,
        *,
        model: str = "large-v3-turbo",
        device: str = "cuda",
        compute_type: str = "float16",
        beam_size: int = 1,
        max_no_speech_prob: float = 0.60,
        min_avg_logprob: float = -1.00,
        min_chars: int = 2,
        min_language_prob: float = 0.55,
        min_audio_s: float = 0.55,
        allowed_languages: list[str] | None = None,
    ) -> None:
        self.allowed_languages = {c.lower() for c in (allowed_languages or []) if c}
        self.beam_size = beam_size
        self.max_no_speech_prob = max_no_speech_prob
        self.min_avg_logprob = min_avg_logprob
        self.min_chars = min_chars
        self.min_language_prob = min_language_prob
        self.min_audio_s = min_audio_s

        bootstrap.prepare_hf_cache()
        registered = bootstrap.prepare_cuda()
        if registered:
            log.debug("CUDA DLL dirs: %s", registered)

        from faster_whisper import WhisperModel  # imported after the DLL path fix

        self.device = device
        self.compute_type = compute_type

        try:
            log.info("loading Whisper %s on %s (%s)", model, device, compute_type)
            started = time.monotonic()
            self._model = WhisperModel(model, device=device, compute_type=compute_type)
            log.info("model ready in %.1fs", time.monotonic() - started)
        except Exception as exc:
            # Only a CUDA problem justifies dropping to the CPU.
            if device != "cuda" or not _is_cuda_failure(exc):
                raise
            log.warning(
                "GPU unavailable (%s) - retrying on CPU with int8. Expect much higher "
                "latency.",
                exc,
            )
            self.device, self.compute_type = "cpu", "int8"
            self._model = WhisperModel(model, device="cpu", compute_type="int8")

    @property
    def backend_name(self) -> str:
        return f"{self.device}/{self.compute_type}"

    def transcribe(
        self,
        samples: np.ndarray,
        *,
        language: str | None = None,
        trim_silence: bool = False,
    ) -> Transcript:
        """Transcribe 16 kHz mono float32 audio and score it for plausibility."""
        started = time.monotonic()
        audio_s = len(samples) / TARGET_RATE

        # Restrict detection to languages the user actually encounters.
        detected_prob = 0.0
        if language is None and self.allowed_languages:
            language, detected_prob = self._detect_allowed(samples)
            if language is None:
                return Transcript(
                    text="", language="", language_prob=detected_prob,
                    avg_logprob=0.0, no_speech_prob=0.0, compression_ratio=0.0,
                    audio_s=audio_s, decode_s=time.monotonic() - started,
                    accepted=False,
                    reject_reason=f"no expected language matched ({detected_prob:.2f})",
                )

        segments, info = self._model.transcribe(
            samples,
            language=language,
            beam_size=self.beam_size,
            vad_filter=trim_silence,
            vad_parameters={"min_silence_duration_ms": 300} if trim_silence else None,
            condition_on_previous_text=False,
            # A single temperature rather than a fallback ladder: a retry at 0.2/0.4
            # only fires on clips we would reject anyway, so it buys nothing but latency.
            temperature=0.0,
            # Segment timestamps are never displayed, and not emitting them is ~19% faster.
            without_timestamps=True,
            no_speech_threshold=self.max_no_speech_prob,
            log_prob_threshold=self.min_avg_logprob,
            compression_ratio_threshold=2.4,
        )

        parts: list[str] = []
        logprob_weighted = 0.0
        weight_total = 0.0
        worst_no_speech = 0.0
        worst_compression = 0.0

        for seg in segments:  # generator - this is where decoding actually happens
            parts.append(seg.text)
            span = max(1e-3, seg.end - seg.start)
            logprob_weighted += seg.avg_logprob * span
            weight_total += span
            worst_no_speech = max(worst_no_speech, seg.no_speech_prob)
            worst_compression = max(worst_compression, seg.compression_ratio)

        text = " ".join(p.strip() for p in parts if p.strip()).strip()
        avg_logprob = logprob_weighted / weight_total if weight_total else -10.0

        result = Transcript(
            text=text,
            language=info.language or "",
            # When the language was chosen from the shortlist, transcribe() reports a
            # probability of 1.0 because it was told what to use - keep the real one.
            language_prob=detected_prob or float(info.language_probability or 0.0),
            avg_logprob=avg_logprob,
            no_speech_prob=worst_no_speech,
            compression_ratio=worst_compression,
            audio_s=audio_s,
            decode_s=time.monotonic() - started,
        )

        reason = self._reject_reason(result)
        if reason:
            result.accepted = False
            result.reject_reason = reason
        return result

    def _detect_allowed(self, samples: np.ndarray) -> tuple[str | None, float]:
        """Pick the most likely language from the configured shortlist."""
        try:
            _code, _prob, all_probs = self._model.detect_language(audio=samples)
        except Exception:
            log.debug("language detection failed; falling back to automatic",
                      exc_info=True)
            return None, 0.0

        best_code, best_prob = None, 0.0
        for code, prob in all_probs:
            if code.lower() in self.allowed_languages and prob > best_prob:
                best_code, best_prob = code, float(prob)

        if best_code is None or best_prob < self.min_language_prob:
            return None, best_prob
        return best_code, best_prob

    def _reject_reason(self, r: Transcript) -> str:
        """Return a human-readable reason to discard this transcript, or ""."""
        normalised = _normalise(r.text)

        # Whisper guesses on very short clips - both the words and the language - and
        # a confident-looking guess from half a second of audio is how wrong
        # translations get through.
        if r.audio_s < self.min_audio_s:
            return f"fragment ({r.audio_s:.2f}s of audio)"
        if not normalised:
            return "no speech content"
        if len(normalised) < self.min_chars:
            return f"too short ({len(normalised)} chars)"
        if r.no_speech_prob > self.max_no_speech_prob:
            return f"no_speech_prob {r.no_speech_prob:.2f}"
        if r.avg_logprob < self.min_avg_logprob:
            return f"low confidence (logprob {r.avg_logprob:.2f})"
        if r.language_prob < self.min_language_prob:
            return f"uncertain language ({r.language} {r.language_prob:.2f})"
        if r.compression_ratio > 2.6:
            return f"repetitive (compression {r.compression_ratio:.2f})"
        if _is_repetitive(normalised):
            return "repeated token loop"
        if normalised in _BLOCK_EXACT:
            return "known filler phrase"
        for needle in _BLOCK_SUBSTRINGS:
            if needle in normalised:
                return "known hallucination phrase"
        return ""
