"""Translation and reply suggestions via a local Ollama model."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import httpx

log = logging.getLogger(__name__)

_THINK_RE = re.compile(r"<think>.*?</think>", flags=re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think>.*\Z", flags=re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", flags=re.DOTALL)
# Requires the closing quote, so a half-streamed value never matches.
_TRANSLATION_RE = re.compile(
    r'"translation"\s*:\s*"((?:[^"\\]|\\.)*)"', flags=re.DOTALL
)
# Anything that is not a word character, for comparing two sentences by their words.
_WORD_RE = re.compile(r"[^\w]+", flags=re.UNICODE)

# Whisper reports ISO 639-1.
_LANGUAGE_NAMES = {
    "ar": "Arabic", "bg": "Bulgarian", "bn": "Bengali", "cs": "Czech",
    "da": "Danish", "de": "German", "el": "Greek", "en": "English",
    "es": "Spanish", "et": "Estonian", "fa": "Persian", "fi": "Finnish",
    "fr": "French", "he": "Hebrew", "hi": "Hindi", "hr": "Croatian",
    "hu": "Hungarian", "id": "Indonesian", "it": "Italian", "ja": "Japanese",
    "ko": "Korean", "lt": "Lithuanian", "lv": "Latvian", "ms": "Malay",
    "nl": "Dutch", "no": "Norwegian", "pl": "Polish", "pt": "Portuguese",
    "ro": "Romanian", "ru": "Russian", "sk": "Slovak", "sl": "Slovenian",
    "sr": "Serbian", "sv": "Swedish", "ta": "Tamil", "th": "Thai",
    "tl": "Tagalog", "tr": "Turkish", "uk": "Ukrainian", "ur": "Urdu",
    "vi": "Vietnamese", "zh": "Chinese",
}


def language_name(code: str) -> str:
    if not code:
        return "an unknown language"
    return _LANGUAGE_NAMES.get(code.lower(), code.upper())


# Languages whose everyday writing system is not Latin.
NON_LATIN_SCRIPTS = frozenset({
    "am", "ar", "as", "bg", "bn", "dv", "el", "fa", "gu", "he", "hi", "hy", "ja",
    "ka", "km", "kn", "ko", "lo", "ml", "mn", "mr", "my", "ne", "or", "pa", "ps",
    "ru", "sd", "si", "sr", "ta", "te", "th", "ti", "uk", "ur", "yi", "zh",
})


def needs_romanisation(code: str) -> bool:
    return (code or "").lower() in NON_LATIN_SCRIPTS


def _wrong_script(text: str, target_code: str) -> bool:
    """True when a translation into a non-Latin script came back in Latin letters."""
    if not needs_romanisation(target_code) or not text:
        return False
    return all(ord(ch) < 0x0250 for ch in text)


def _echoes_source(text: str, source: str) -> bool:
    """True when the 'translation' is really just the input handed back."""
    if not text or not source:
        return False
    normalise = lambda s: _WORD_RE.sub(" ", s).strip().casefold()  # noqa: E731
    a, b = normalise(text), normalise(source)
    if not a or not b or len(b) < 12:
        return False
    if a == b:
        return True
    return SequenceMatcher(None, a, b).ratio() > 0.90


# Worked examples, because a bare instruction to "write it in English letters" produces.
_ROMANISATION_EXAMPLES: dict[str, str] = {
    "ja": "こんにちは → kon-ni-chi-wa · 待ってて → mat-te-te · 2分 → ni-fun",
    "zh": "你好 → ni-hao · 谢谢 → xie-xie · 等一下 → deng-yi-xia",
    "ko": "안녕하세요 → an-nyeong-ha-se-yo · 기다려 → gi-da-ryeo",
    "ar": "شكرا → shuk-ran · مرحبا → mar-ha-ban · انتظر → in-ta-zir",
    "ru": "спасибо → spa-si-ba · привет → pri-vyet · подожди → pa-dazh-di",
    "uk": "дякую → dya-ku-yu · привіт → pry-vit",
    "hi": "नमस्ते → na-mas-te · रुको → ru-ko",
    "th": "สวัสดี → sa-wat-dee · รอก่อน → ror-gorn",
    "el": "ευχαριστώ → ef-ha-ri-sto · περίμενε → pe-ri-me-ne",
    "he": "תודה → to-da · רגע → re-ga",
    "fa": "سلام → sa-laam · صبر کن → sabr-kon",
    "bn": "ধন্যবাদ → dhon-no-bad · অপেক্ষা → o-pek-kha",
    "ta": "நன்றி → nan-dri · காத்திரு → kaath-thi-ru",
    "sr": "хвала → hva-la · чекај → che-kai",
    "bg": "благодаря → bla-go-da-rya · чакай → cha-kai",
}


def romanisation_guidance(code: str, language: str) -> str:
    """The shared rule for writing a phrase in English letters."""
    example = _ROMANISATION_EXAMPLES.get((code or "").lower())
    hint = f"  {language} examples: {example}\n" if example else ""
    return (
        f"  Read the {language} text character by character and transcribe how it "
        "SOUNDS. Do not invent a spelling from the meaning. Write numerals the way "
        "they are spoken in that language. Separate syllables with hyphens, plain "
        "English letters only - no phonetic symbols, no tone marks.\n"
        f"{hint}"
    )


@dataclass
class Reply:
    """One suggested thing to say back."""

    reply: str  # in the speaker's language - this is what gets copied
    gloss: str  # what it means, in the user's language
    say: str = ""  # how to pronounce it, spelled with English letters


@dataclass
class SuggestEvent:
    """Incremental output. ``kind`` is translation | reply | done | error."""

    kind: str
    text: str = ""
    reply: Reply | None = None
    latency_s: float = 0.0


@dataclass
class Outgoing:
    """A line the user typed, rendered into the other person's language."""

    natural: str = ""  # the user's sentence, faithfully rendered in the target language
    say: str = ""      # how to pronounce it, in English letters
    note: str = ""     # only when something genuinely needs flagging
    latency_s: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.natural)


@dataclass
class Suggestion:
    """Collected result, for callers that do not care about streaming."""

    translation: str = ""
    replies: list[Reply] = field(default_factory=list)
    latency_s: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def _response_schema(count: int, *, romanise: bool, translate: bool = True) -> dict:
    # Property order is preserved and honoured by Ollama's grammar.
    if romanise:
        item_properties: dict[str, dict] = {
            "text": {"type": "string"},
            "say": {"type": "string"},
            "meaning": {"type": "string"},
        }
        required = ["text", "say", "meaning"]
    else:
        item_properties = {
            "text": {"type": "string"},
            "meaning": {"type": "string"},
        }
        required = ["text", "meaning"]

    replies = {
        "type": "array",
        "minItems": 1,
        "maxItems": count,
        "items": {
            "type": "object",
            "properties": item_properties,
            "required": required,
        },
    }

    # When the speaker's language is one the user already reads.
    if not translate:
        return {
            "type": "object",
            "properties": {"replies": replies},
            "required": ["replies"],
        }

    return {
        "type": "object",
        "properties": {"translation": {"type": "string"}, "replies": replies},
        "required": ["translation", "replies"],
    }


def _outgoing_schema(*, romanise: bool) -> dict:
    """One translation, nothing else."""
    properties: dict[str, dict] = {"translation": {"type": "string"}}
    required = ["translation"]
    if romanise:
        properties["say"] = {"type": "string"}
        required.append("say")
    properties["note"] = {"type": "string"}
    return {"type": "object", "properties": properties, "required": required}


def _decode_json_string(raw: str) -> str:
    """Turn a captured JSON string body into text, resolving escapes."""
    try:
        return json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        return raw


def _find_object_end(text: str, start: int) -> int:
    """Index just past the object opening at ``start``, or -1 if incomplete."""
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


def _extract_json(raw: str) -> dict:
    """Pull one JSON object out of a complete model response."""
    text = _THINK_RE.sub("", raw).strip()
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        if start == -1:
            raise ValueError("no JSON object in response") from None
        end = _find_object_end(text, start)
        if end == -1:
            raise ValueError("truncated JSON object in response") from None
        parsed = json.loads(text[start:end])

    if not isinstance(parsed, dict):
        raise ValueError("response was not a JSON object")
    return parsed


class _IncrementalParser:
    """Emits values from a partially-received JSON object as they complete."""

    def __init__(self, max_replies: int) -> None:
        self._buf = ""
        self._max_replies = max_replies
        self._translation_sent = False
        self._replies_sent = 0
        self._cursor: int | None = None  # position inside the replies array
        self._array_closed = False

    @property
    def buffer(self) -> str:
        return self._buf

    @property
    def replies_sent(self) -> int:
        return self._replies_sent

    @property
    def translation_sent(self) -> bool:
        return self._translation_sent

    def feed(self, delta: str) -> list[SuggestEvent]:
        self._buf += delta
        events: list[SuggestEvent] = []

        # A reasoning model may still open a <think> block despite think=false.
        visible = _THINK_RE.sub("", self._buf)
        if _THINK_OPEN_RE.search(visible):
            return events

        if not self._translation_sent:
            match = _TRANSLATION_RE.search(visible)
            if match:
                self._translation_sent = True
                events.append(
                    SuggestEvent(kind="translation", text=_decode_json_string(match.group(1)))
                )

        events.extend(self._drain_replies(visible))
        return events

    def _drain_replies(self, visible: str) -> list[SuggestEvent]:
        if self._array_closed or self._replies_sent >= self._max_replies:
            return []

        if self._cursor is None:
            key = visible.find('"replies"')
            if key == -1:
                return []
            bracket = visible.find("[", key)
            if bracket == -1:
                return []
            self._cursor = bracket + 1

        events: list[SuggestEvent] = []
        pos = self._cursor
        while pos < len(visible) and self._replies_sent < self._max_replies:
            while pos < len(visible) and visible[pos] in " \t\r\n,":
                pos += 1
            if pos >= len(visible):
                break
            if visible[pos] == "]":
                self._array_closed = True
                break
            if visible[pos] != "{":
                break

            end = _find_object_end(visible, pos)
            if end == -1:
                break  # still streaming this object

            try:
                obj = json.loads(visible[pos:end])
            except json.JSONDecodeError:
                break
            reply = _coerce_reply(obj)
            if reply is not None:
                self._replies_sent += 1
                events.append(SuggestEvent(kind="reply", reply=reply))
            pos = end

        self._cursor = pos
        return events

    def finish(self) -> dict:
        """Parse the complete buffer, for fields streaming did not surface."""
        return _extract_json(self._buf)


def _coerce_reply(obj: object) -> Reply | None:
    """Accept the schema's keys, plus the aliases a model sometimes substitutes."""
    if not isinstance(obj, dict):
        return None
    text = obj.get("text") or obj.get("reply") or obj.get("native")
    if not isinstance(text, str) or not text.strip():
        return None
    # A model that mis-slots a field tends to echo the field's own name.
    if text.strip().casefold() in {"text", "reply", "gloss", "meaning", "say"}:
        return None
    meaning = obj.get("meaning") or obj.get("gloss") or obj.get("english") or ""
    say = obj.get("say") or obj.get("pronunciation") or obj.get("romaji") or ""
    return Reply(reply=text.strip(), gloss=str(meaning).strip(), say=str(say).strip())


class Suggester:
    """Ollama-backed translator and reply generator."""

    def __init__(
        self,
        *,
        model: str = "qwen3:4b",
        host: str = "http://127.0.0.1:11434",
        my_language: str = "English",
        count: int = 3,
        tone: str = "casual and friendly",
        timeout_s: int = 30,
        keep_alive: str = "1h",
        num_ctx: int = 2048,
        temperature: float = 0.6,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.my_language = my_language
        self.count = count
        self.tone = tone
        self.keep_alive = keep_alive
        self.num_ctx = num_ctx
        self.temperature = temperature

        # Generous connect budget: the first request after `keep_alive` expires has to wait.
        self._client = httpx.Client(
            base_url=self.host,
            timeout=httpx.Timeout(timeout_s, connect=10.0, read=timeout_s),
        )
        # Latches off if the server rejects the parameter for this model.
        self._supports_think_flag = True

    def close(self) -> None:
        self._client.close()

    def set_my_language(self, name: str) -> None:
        """Change the language translations and glosses are produced in."""
        self.my_language = name


    def health(self) -> tuple[bool, str]:
        """Check the server is up and the configured model is present."""
        try:
            response = self._client.get("/api/tags", timeout=5.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return False, (
                f"Ollama unreachable at {self.host} ({exc.__class__.__name__}). "
                "Start it from the Start menu, or run: ollama serve"
            )

        installed = [m.get("name", "") for m in response.json().get("models", [])]
        if not self._model_present(installed):
            listed = ", ".join(sorted(installed)) or "none"
            return False, (
                f"Model {self.model!r} is not installed. Run: ollama pull {self.model}\n"
                f"Installed models: {listed}"
            )
        return True, f"{self.model} ready"

    def _model_present(self, installed: list[str]) -> bool:
        # Ollama reports "qwen3:4b"; a user may configure "qwen3" meaning the default tag.
        wanted = self.model if ":" in self.model else f"{self.model}:latest"
        return any(name in (wanted, self.model) for name in installed)

    def warmup(self) -> None:
        """Load the model into VRAM so the first real utterance is not slow."""
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"num_predict": 1, "num_ctx": self.num_ctx},
        }
        if self._supports_think_flag:
            body["think"] = False
        try:
            response = self._client.post("/api/chat", json=body, timeout=240.0)
            if response.status_code == 400 and self._supports_think_flag:
                self._supports_think_flag = False
                body.pop("think", None)
                self._client.post("/api/chat", json=body, timeout=240.0)
        except httpx.HTTPError as exc:
            log.warning("warmup failed: %s", exc)
            return
        self.log_placement()

    def log_placement(self) -> None:
        """Report whether the model actually landed on the GPU."""
        try:
            response = self._client.get("/api/ps", timeout=5.0)
            response.raise_for_status()
            models = response.json().get("models", [])
        except (httpx.HTTPError, ValueError):
            return

        for entry in models:
            if entry.get("name") not in (self.model, f"{self.model}:latest"):
                continue
            total = entry.get("size") or 0
            on_gpu = entry.get("size_vram") or 0
            if not total:
                return
            share = on_gpu / total
            if share >= 0.99:
                log.info("%s resident: 100%% GPU", self.model)
            else:
                log.warning(
                    "%s is only %.0f%% on the GPU - generation will be far slower. "
                    "Close something using VRAM, or lower asr.compute_type to int8.",
                    self.model, share * 100,
                )
            return


    def _romanisation_rule(self, code: str, language: str) -> str:
        return (
            "- 'say': the same text as 'text', written in English letters.\n"
            + romanisation_guidance(code, language)
        )

    def _worked_example(self, language: str, *, romanise: bool) -> str:
        """One filled-in object."""
        if romanise:
            body = (
                '{"text": "<in ' + language + '>", '
                '"say": "<same words, English letters>", '
                '"meaning": "<in ' + self.my_language + '>"}'
            )
        else:
            body = (
                '{"text": "<in ' + language + '>", '
                '"meaning": "<in ' + self.my_language + '>"}'
            )
        return f"\nShape of one reply: {body}\n"

    def _system_prompt(self, speaker_language: str, *, romanise: bool = False,
                       code: str = "", translate: bool = True) -> str:
        pronunciation = (
            self._romanisation_rule(code, speaker_language) if romanise else ""
        )

        if translate:
            task = (
                "For each incoming line you do two things:\n"
                f"1. translation - the line rewritten in {self.my_language}.\n"
                f"   - This field is ALWAYS {self.my_language}, never "
                f"{speaker_language}. Copying the original words back is a failure, "
                "even if the line is short, lowercase or full of slang.\n"
                "   - Convey what the person actually means. Replace slang, "
                f"abbreviations and laughter markers with their {self.my_language} "
                "equivalents rather than leaving them as-is.\n"
                "   - No commentary, no notes, no explanations.\n"
                f"2. replies - up to {self.count} things the user could say back, "
                f"written in {speaker_language}, as text they can send immediately.\n\n"
            )
        else:
            # The user already reads this language, so there is nothing to translate.
            task = (
                f"The user reads {speaker_language} perfectly well, so do NOT translate "
                "the line and do not restate it. Produce only replies: up to "
                f"{self.count} things the user could say back, written in "
                f"{speaker_language}, as text they can send immediately.\n\n"
            )

        return (
            "You are a live interpreter built into a gaming voice-chat overlay. You "
            f"help a {self.my_language} speaker talk with someone speaking "
            f"{speaker_language}.\n\n"
            f"{task}"
            "Rules for replies:\n"
            f"- Tone: {self.tone}.\n"
            "- Short: 10 words maximum. This is voice chat, not an essay.\n"
            f"- Correct: check verb conjugations, pronouns and agreement. A reply with a "
            f"grammar mistake is worse than a simpler reply that is right.\n"
            "- Natural: what a native speaker actually says in a game, not a textbook.\n"
            "- Distinct: vary the intent - agree, ask something, joke, decline. Never "
            "give several rewordings of the same sentence.\n"
            "- Never invent facts about the user and never promise anything specific.\n"
            f"\nEach reply object has these fields:\n"
            f"- 'text': the reply itself, written in {speaker_language}.\n"
            f"{pronunciation}"
            f"- 'meaning': what it means in {self.my_language}, 8 words maximum. A "
            "label, not a sentence - no preamble, no quotes.\n"
            f"{self._worked_example(speaker_language, romanise=bool(pronunciation))}"
            "\nReply with JSON only. Every word you write costs the user latency."
        )

    def _user_prompt(self, text: str, speaker_language: str, history: list[str]) -> str:
        parts: list[str] = []
        if history:
            parts.append("Earlier in this conversation:")
            parts.extend(f"- {line}" for line in history)
            parts.append("")
        parts.append(f"They just said (in {speaker_language}):")
        parts.append(f'"{text}"')
        return "\n".join(parts)

    def _build_body(self, text: str, language_code: str, history: list[str],
                    *, translate: bool = True) -> dict:
        speaker_language = language_name(language_code)
        romanise = needs_romanisation(language_code)
        body = {
            "model": self.model,
            "messages": [
                {"role": "system",
                 "content": self._system_prompt(speaker_language, romanise=romanise,
                                                code=language_code,
                                                translate=translate)},
                {"role": "user", "content": self._user_prompt(text, speaker_language, history)},
            ],
            "stream": True,
            "format": _response_schema(self.count, romanise=romanise,
                                       translate=translate),
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
                # Bounds worst-case latency; the schema keeps output compact anyway.
                "num_predict": 512,
            },
        }
        if self._supports_think_flag:
            body["think"] = False
        return body


    def stream(
        self,
        text: str,
        *,
        language_code: str,
        history: list[str] | None = None,
        translate: bool = True,
    ) -> Iterator[SuggestEvent]:
        """Yield translation and replies as the model produces them. Never raises."""
        started = time.monotonic()
        body = self._build_body(text, language_code, history or [], translate=translate)
        parser = _IncrementalParser(self.count)

        try:
            for delta in self._iter_deltas(body):
                for event in parser.feed(delta):
                    if event.kind == "translation" and not translate:
                        continue
                    event.latency_s = time.monotonic() - started
                    yield event
        except httpx.TimeoutException:
            yield SuggestEvent(
                kind="error",
                text="model timed out - try a smaller model or raise suggest.timeout_s",
                latency_s=time.monotonic() - started,
            )
            return
        except httpx.HTTPError as exc:
            yield SuggestEvent(
                kind="error",
                text=f"Ollama error: {exc}",
                latency_s=time.monotonic() - started,
            )
            return

        # Recover anything the incremental pass missed - e.g.
        yield from self._reconcile(parser, translate=translate, started=started)
        yield SuggestEvent(kind="done", latency_s=time.monotonic() - started)

    def _iter_deltas(self, body: dict) -> Iterator[str]:
        """Yield content deltas from Ollama's newline-delimited JSON stream."""
        attempted_without_think = not self._supports_think_flag

        while True:
            with self._client.stream("POST", "/api/chat", json=body) as response:
                if (
                    response.status_code == 400
                    and self._supports_think_flag
                    and not attempted_without_think
                ):
                    response.read()  # release the connection before retrying
                    self._supports_think_flag = False
                    attempted_without_think = True
                    body = dict(body)
                    body.pop("think", None)
                    continue

                response.raise_for_status()
                for raw_line in response.iter_lines():
                    if not raw_line:
                        continue
                    try:
                        chunk = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("error"):
                        raise httpx.HTTPError(str(chunk["error"]))
                    piece = (chunk.get("message") or {}).get("content") or ""
                    if piece:
                        yield piece
                    if chunk.get("done"):
                        return
            return

    def _reconcile(
        self, parser: _IncrementalParser, *, translate: bool, started: float
    ) -> Iterator[SuggestEvent]:
        """Emit anything the streaming pass did not, by parsing the whole buffer."""
        if not parser.buffer.strip():
            yield SuggestEvent(
                kind="error",
                text="model returned nothing",
                latency_s=time.monotonic() - started,
            )
            return

        try:
            data = parser.finish()
        except (ValueError, json.JSONDecodeError) as exc:
            if parser.replies_sent:
                return  # partial output already displayed; nothing useful to add
            log.debug("unparseable model output: %r", parser.buffer[:500])
            yield SuggestEvent(
                kind="error",
                text=f"could not parse model output ({exc})",
                latency_s=time.monotonic() - started,
            )
            return

        if translate and not parser.translation_sent:
            translation = data.get("translation")
            if isinstance(translation, str) and translation.strip():
                yield SuggestEvent(
                    kind="translation",
                    text=translation.strip(),
                    latency_s=time.monotonic() - started,
                )

        remaining = (data.get("replies") or [])[parser.replies_sent : self.count]
        for item in remaining:
            reply = _coerce_reply(item)
            if reply is not None:
                yield SuggestEvent(
                    kind="reply", reply=reply, latency_s=time.monotonic() - started
                )


    def translate_outgoing(
        self,
        text: str,
        *,
        target_code: str,
        history: list[str] | None = None,
    ) -> Outgoing:
        """Translate something the user typed into the other person's language."""
        history = history or []
        result = self._outgoing_once(text, target_code=target_code, history=history)

        # Two ways the model silently fails to translate: it answers in the wrong script.
        if result.ok and _wrong_script(result.natural, target_code):
            reason = "wrong script"
        elif result.ok and _echoes_source(result.natural, text):
            reason = "returned the input unchanged"
        else:
            return result

        log.warning("outgoing translation %s; retrying", reason)
        retry = self._outgoing_once(
            text, target_code=target_code, history=history, romanise=False,
            correction=(
                "Your previous attempt did not translate the message - you "
                f"{reason}. Translate it properly this time."
            ),
        )
        if retry.ok and not _wrong_script(retry.natural, target_code) \
                and not _echoes_source(retry.natural, text):
            # Recover the pronunciation from the first attempt if it was usable.
            if not retry.say and result.say:
                retry.say = result.say
            return retry
        return result

    def _outgoing_once(
        self,
        text: str,
        *,
        target_code: str,
        history: list[str],
        romanise: bool | None = None,
        correction: str = "",
    ) -> Outgoing:
        started = time.monotonic()
        target = language_name(target_code)
        if romanise is None:
            romanise = needs_romanisation(target_code)

        pronunciation = ""
        shape = ""
        if romanise:
            pronunciation = (
                "- say: the exact text you wrote in 'translation', in English letters.\n"
                + romanisation_guidance(target_code, target)
                + f"  'translation' uses {target}'s own script. 'say' is the only field "
                "that may contain English letters. Writing the translation itself in "
                "English letters is a failure.\n"
            )
            # Asking for a Latin-letter field alongside the translation makes a 4B model.
            shape = (
                '\nShape: {"translation": "<in ' + target + ', in its own script>", '
                '"say": "<that same sentence in English letters>", "note": ""}\n'
            )

        system = (
            f"You are a translator. You translate {self.my_language} into {target} "
            "exactly.\n\n"
            "- translation: the user's message in "
            f"{target}. Translate what they actually wrote - every part of it, and "
            "nothing more. Do not add, drop, soften, embellish or reinterpret "
            "anything. Do not change who is being addressed, the tense, or whether it "
            "is a question. If they wrote something blunt, it stays blunt; if they "
            "wrote something plain, it stays plain.\n"
            "  Natural wording is fine where a word-for-word rendering would be wrong "
            f"in {target} - but the meaning must match the input exactly.\n"
            "  Never answer the message, never continue the conversation, never add a "
            "greeting or sign-off that was not there.\n"
            f"{pronunciation}"
            "- note: an empty string, unless something genuinely cannot carry across "
            f"into {target} (an idiom with no equivalent, or wording that would land as "
            "rude). Never use it to explain or justify the translation.\n\n"
            f"'translation' is written in {target}, never in {self.my_language}. "
            "Reply with JSON only."
            f"{shape}"
        )

        parts: list[str] = []
        if history:
            parts.append("Conversation so far:")
            parts.extend(f"- {line}" for line in history)
            parts.append("")
        parts.append(f"Translate this into {target}:")
        parts.append(f'"{text}"')
        if correction:
            parts.append("")
            parts.append(correction)

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": "\n".join(parts)},
            ],
            "stream": False,
            "format": _outgoing_schema(romanise=romanise),
            "keep_alive": self.keep_alive,
            "options": {
                # Near-deterministic.
                "temperature": 0.15,
                "num_ctx": self.num_ctx,
                "num_predict": 220,
            },
        }
        if self._supports_think_flag:
            body["think"] = False

        try:
            response = self._client.post("/api/chat", json=body)
            if response.status_code == 400 and self._supports_think_flag:
                self._supports_think_flag = False
                body.pop("think", None)
                response = self._client.post("/api/chat", json=body)
            response.raise_for_status()
            raw = response.json().get("message", {}).get("content", "") or ""
        except httpx.TimeoutException:
            return Outgoing(
                latency_s=time.monotonic() - started, error="model timed out"
            )
        except httpx.HTTPError as exc:
            return Outgoing(
                latency_s=time.monotonic() - started, error=f"Ollama error: {exc}"
            )

        try:
            data = _extract_json(_THINK_RE.sub("", raw))
        except (ValueError, json.JSONDecodeError) as exc:
            log.debug("unparseable outgoing translation: %r", raw[:400])
            return Outgoing(
                latency_s=time.monotonic() - started,
                error=f"could not parse model output ({exc})",
            )

        translation = str(
            data.get("translation") or data.get("natural") or ""
        ).strip()

        result = Outgoing(
            natural=translation,
            say=str(data.get("say") or "").strip(),
            note=str(data.get("note") or "").strip(),
            latency_s=time.monotonic() - started,
        )
        if not translation:
            result.error = "model returned no translation"
        return result

    def run(
        self,
        text: str,
        *,
        language_code: str,
        history: list[str] | None = None,
        translate: bool = True,
    ) -> Suggestion:
        """Blocking convenience wrapper that collects the whole stream."""
        result = Suggestion()
        for event in self.stream(
            text, language_code=language_code, history=history, translate=translate
        ):
            if event.kind == "translation":
                result.translation = event.text
            elif event.kind == "reply" and event.reply is not None:
                result.replies.append(event.reply)
            elif event.kind == "error":
                result.error = event.text
            result.latency_s = event.latency_s
        if not result.translation and not result.replies and not result.error:
            result.error = "model returned nothing usable"
        return result
