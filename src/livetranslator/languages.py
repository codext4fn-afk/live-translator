"""The languages Whisper knows, their names, and tolerant code matching."""

from __future__ import annotations

# Every code faster-whisper accepts. Anything outside this raises inside the
# tokenizer, so config values are checked against it before they reach the model
NAMES: dict[str, str] = {
    "af": "Afrikaans", "am": "Amharic", "ar": "Arabic", "as": "Assamese",
    "az": "Azerbaijani", "ba": "Bashkir", "be": "Belarusian", "bg": "Bulgarian",
    "bn": "Bengali", "bo": "Tibetan", "br": "Breton", "bs": "Bosnian",
    "ca": "Catalan", "cs": "Czech", "cy": "Welsh", "da": "Danish",
    "de": "German", "el": "Greek", "en": "English", "es": "Spanish",
    "et": "Estonian", "eu": "Basque", "fa": "Persian", "fi": "Finnish",
    "fo": "Faroese", "fr": "French", "gl": "Galician", "gu": "Gujarati",
    "ha": "Hausa", "haw": "Hawaiian", "he": "Hebrew", "hi": "Hindi",
    "hr": "Croatian", "ht": "Haitian Creole", "hu": "Hungarian",
    "hy": "Armenian", "id": "Indonesian", "is": "Icelandic", "it": "Italian",
    "ja": "Japanese", "jw": "Javanese", "ka": "Georgian", "kk": "Kazakh",
    "km": "Khmer", "kn": "Kannada", "ko": "Korean", "la": "Latin",
    "lb": "Luxembourgish", "ln": "Lingala", "lo": "Lao", "lt": "Lithuanian",
    "lv": "Latvian", "mg": "Malagasy", "mi": "Maori", "mk": "Macedonian",
    "ml": "Malayalam", "mn": "Mongolian", "mr": "Marathi", "ms": "Malay",
    "mt": "Maltese", "my": "Burmese", "ne": "Nepali", "nl": "Dutch",
    "nn": "Norwegian Nynorsk", "no": "Norwegian", "oc": "Occitan",
    "pa": "Punjabi", "pl": "Polish", "ps": "Pashto", "pt": "Portuguese",
    "ro": "Romanian", "ru": "Russian", "sa": "Sanskrit", "sd": "Sindhi",
    "si": "Sinhala", "sk": "Slovak", "sl": "Slovenian", "sn": "Shona",
    "so": "Somali", "sq": "Albanian", "sr": "Serbian", "su": "Sundanese",
    "sv": "Swedish", "sw": "Swahili", "ta": "Tamil", "te": "Telugu",
    "tg": "Tajik", "th": "Thai", "tk": "Turkmen", "tl": "Filipino",
    "tr": "Turkish", "tt": "Tatar", "uk": "Ukrainian", "ur": "Urdu",
    "uz": "Uzbek", "vi": "Vietnamese", "yi": "Yiddish", "yo": "Yoruba",
    "yue": "Cantonese", "zh": "Chinese",
}

CODES = frozenset(NAMES)

# Languages whose everyday writing system is not Latin, so a reply in one of
# these needs a pronunciation line
NON_LATIN = frozenset({
    "am", "ar", "as", "be", "bg", "bn", "bo", "el", "fa", "gu", "he", "hi",
    "hy", "ja", "ka", "km", "kn", "ko", "lo", "mk", "ml", "mn", "mr", "my",
    "ne", "pa", "ps", "ru", "sa", "sd", "si", "sr", "ta", "te", "tg", "th",
    "tt", "uk", "ur", "yi", "yue", "zh",
})

# Codes people reach for that Whisper does not use. Filipino is the common one:
# the country is PH and the language is often written fl, but Whisper calls it tl
ALIASES: dict[str, str] = {
    "fl": "tl", "fil": "tl", "tgl": "tl", "ph": "tl", "phi": "tl",
    "jp": "ja", "jpn": "ja", "kr": "ko", "kor": "ko", "cn": "zh",
    "chi": "zh", "zho": "zh", "mandarin": "zh", "cantonese": "yue",
    "gr": "el", "gre": "el", "ell": "el", "ua": "uk", "ukr": "uk",
    "se": "sv", "swe": "sv", "dk": "da", "dan": "da", "cz": "cs",
    "cze": "cs", "ces": "cs", "nb": "no", "nob": "no", "br-pt": "pt",
    "ind": "id", "may": "ms", "msa": "ms", "vn": "vi", "vie": "vi",
    "ger": "de", "deu": "de", "fre": "fr", "fra": "fr", "spa": "es",
    "por": "pt", "ita": "it", "dut": "nl", "nld": "nl", "rus": "ru",
    "ara": "ar", "tur": "tr", "pol": "pl", "eng": "en",
}

_BY_NAME = {name.casefold(): code for code, name in NAMES.items()}
_BY_NAME.update({
    "tagalog": "tl", "filipino (tagalog)": "tl", "malaysian": "ms",
    "bahasa melayu": "ms", "bahasa indonesia": "id", "mandarin chinese": "zh",
    "brazilian portuguese": "pt", "farsi": "fa", "castilian": "es",
})


def normalise(value: str) -> str:
    """Turn whatever someone typed into a Whisper code, or "" if there is none."""
    text = (value or "").strip().casefold().replace("_", "-")
    if not text:
        return ""
    if text in CODES:
        return text
    if text in ALIASES:
        return ALIASES[text]
    if text in _BY_NAME:
        return _BY_NAME[text]
    base = text.split("-")[0]
    if base in CODES:
        return base
    return ALIASES.get(base, "")


def name(code: str) -> str:
    if not code:
        return "an unknown language"
    return NAMES.get(code.casefold(), code.upper())


def code_for(display_name: str) -> str:
    """Map a display name back to its code, for the settings picker."""
    return _BY_NAME.get((display_name or "").casefold(), "")


def needs_romanisation(code: str) -> bool:
    return (code or "").casefold() in NON_LATIN
