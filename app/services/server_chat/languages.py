"""The selected chat languages and explicit, predictable language requests."""

from dataclasses import dataclass
import re

from .context import normal


LANGUAGES = {
    "en": ("English", "english"),
    "hi": ("Hindi", "hindi", "हिंदी", "हिन्दी"),
    "es": ("Spanish", "spanish", "español"),
    "fr": ("French", "french", "français", "francais"),
    "ar": ("Arabic", "arabic", "العربية", "عربي"),
    "ja": ("Japanese", "japanese", "日本語"),
    "ko": ("Korean", "korean", "한국어"),
    "ru": ("Russian", "russian", "русский"),
    "bn": ("Bengali", "bengali", "bangla", "বাংলা"),
    "be": ("Belarusian", "belarusian", "беларуская"),
    "bs": ("Bosnian", "bosnian", "bosanski"),
    "bg": ("Bulgarian", "bulgarian", "български"),
    "hr": ("Croatian", "croatian", "hrvatski"),
    "cs": ("Czech", "czech", "čeština", "cestina"),
    "mk": ("Macedonian", "macedonian", "македонски"),
    "pl": ("Polish", "polish", "polski"),
    "sr": ("Serbian", "serbian", "srpski", "српски"),
    "sk": ("Slovak", "slovak", "slovenčina", "slovencina"),
    "sl": ("Slovenian", "slovenian", "slovene", "slovenščina", "slovenscina"),
    "uk": ("Ukrainian", "ukrainian", "українська"),
}
ALIASES = {normal(alias): code for code, values in LANGUAGES.items() for alias in (code, *values)}
NAMES = ", ".join(values[0] for values in LANGUAGES.values())
SLAVIC_NOTICE = "Slavic is a language family. Which language do you mean: Polish, Ukrainian, Serbian, or another one? Russian is already supported."


@dataclass(frozen=True)
class LanguageRequest:
    question: str
    language: str | None = None
    literal: str | None = None
    repeat: bool = False
    notice: str | None = None


def parse(question):
    text = question.strip()
    target = re.search(r"\b(?:into|in|to)\s+([^\s,?.!:'\"`]{1,35})(?:\s+please|\s+for me)?[?.!]*$", text, re.I)
    if target is None:
        return LanguageRequest(text)
    name = normal(target[1])
    if name == "slavic":
        return LanguageRequest(text, notice=SLAVIC_NOTICE)
    language = ALIASES.get(name)
    if language is None:
        if re.match(r"^(?:please\s+)?(?:reply|respond|speak|talk|answer)\s+(?:to me\s+)?(?:only\s+)?in\s+", text, re.I):
            return LanguageRequest(text, notice=f"The selected chat languages are: {NAMES}.")
        return LanguageRequest(text)
    before = text[:target.start()].strip().rstrip(",")
    literal = re.fullmatch(r"(?:please\s+)?translate\s*:?\s*([\"'`“‘])(.*)[\"'`”’]", before, re.I | re.S)
    if literal:
        return LanguageRequest("", language, literal=literal[2])
    if re.fullmatch(r"(?:please\s+)?(?:reply|respond|speak|talk|answer)(?:\s+to me)?(?:\s+only)?", before, re.I):
        return LanguageRequest("", language)
    repeat = normal(before) in {"say that", "say it", "translate that", "translate it", "repeat that", "that", "it"}
    cleaned = re.sub(r"[,.]?\s*(?:please\s+)?(?:reply|respond|answer)(?:\s+to me)?$", "", before, flags=re.I).strip()
    return LanguageRequest(cleaned or before, language, repeat=repeat)
