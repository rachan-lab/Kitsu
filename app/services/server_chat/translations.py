"""Reviewed translations of approved server rules, bound to the original text."""

from dataclasses import dataclass
import hashlib
import re

from .context import normal
from .knowledge import load_entries, permitted, render_entry
from .languages import LANGUAGES as CHAT_LANGUAGES


LANGUAGES = {
    "hi": ("Hindi", "hindi", "हिंदी", "हिन्दी"),
    "es": ("Spanish", "spanish", "español"),
    "fr": ("French", "french", "français"),
    "de": ("German", "german", "deutsch"),
    "en": ("English", "english"),
    "ja": ("Japanese", "japanese"),
    "ko": ("Korean", "korean"),
    "zh": ("Chinese", "chinese", "mandarin"),
    "ar": ("Arabic", "arabic"),
    "pt": ("Portuguese", "portuguese"),
    "ru": ("Russian", "russian"),
    "bn": ("Bengali", "bengali", "bangla"),
    "ta": ("Tamil", "tamil"),
    "te": ("Telugu", "telugu"),
}
LANGUAGES.update(CHAT_LANGUAGES)
ALIASES = {normal(alias): (code, values[0]) for code, values in LANGUAGES.items() for alias in (code, *values)}
RULES_QUESTIONS = {normal(question) for question in (
    "What are this server's rules?", "What are the server rules?", "What are our server rules?",
    "What are the rules?", "What are our rules?", "Server rules", "Rules",
)}
RULES_SUBJECTS = {
    "rules", "server rules", "our rules", "our server rules", "the rules", "the server rules", "this server s rules",
    "the rules of this server", "the rules of the server", "rules of this server", "rules of the server",
}


@dataclass(frozen=True)
class Translation:
    language: str = ""
    source: str = ""
    cached: str | None = None
    error: str | None = None


def source_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def request(question, guild_id, allowed_ids):
    translating = bool(re.search(r"\btranslat(?:e|ion)\b", question, re.I))
    target = re.search(r"\b(?:into|to|in)\s+([^\n,?.!:'\"`]{1,35})[?.!]*$", question, re.I)
    before = question[:target.start()].strip() if target else question.strip()
    before = re.sub(r"^(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?", "", before, flags=re.I)
    before = re.sub(r"^(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?translate\s*[:\-]?\s*", "", before, flags=re.I)
    before = re.sub(r"^(?:show|give|tell)\s+me\s+", "", before, flags=re.I).strip()
    subject = normal(re.sub(r"^what are\s+", "", before, flags=re.I))
    if subject not in RULES_SUBJECTS:
        return None
    if target is None:
        return Translation(error="Which language should I translate the server rules into?") if translating else None
    language_key = normal(re.sub(r"\s+(?:please|for me)$", "", target[1], flags=re.I))
    known = ALIASES.get(language_key)
    if not translating and not (known and re.search(r"\brules\b", question, re.I)):
        return None
    if not language_key or len(language_key.split()) > 3:
        return Translation(error="Which language should I translate into?")
    code, language = known or (language_key.replace(" ", "-"), language_key.title())
    quoted = len(before) > 1 and (before[0], before[-1]) in {('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"), ("`", "`")}
    if not quoted:
        entries = [entry for entry in load_entries(guild_id)
                   if normal(entry["question"]) in RULES_QUESTIONS and permitted(render_entry(entry), allowed_ids)]
        entry = max(entries, key=lambda entry: str(entry.get("approved_at") or ""), default=None)
        if entry is None:
            return Translation(error="I don't have approved server rules to translate yet. Ask the server owner or an administrator to save them first.")
        source = entry["answer"]
        translations = entry.get("translations", {})
        saved = translations.get(code, {}) if isinstance(translations, dict) else {}
        cached = saved.get("text") if isinstance(saved, dict) and saved.get("source_sha256") == source_hash(source) else None
        if not isinstance(cached, str) or not cached.strip() or len(cached) > 1800 or not permitted(cached, allowed_ids):
            cached = None
        if cached is None:
            return Translation(language, source, error=f"I don't have a reviewed {language} version of these rules yet. Here are the approved original rules:\n\n{source}")
        return Translation(language, source, cached)
    return None
