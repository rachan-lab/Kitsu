"""Reviewed Discord documentation and current project behavior, with provenance."""

from functools import lru_cache
import json
from pathlib import Path

from .context import normal
from .runtime import ROOT


REFERENCE_PATH = ROOT / "llm" / "server_chat" / "reference.json"
STOP_WORDS = set("what is are the a an do does how can i we you me my to for in on of this that please tell about".split())


@lru_cache(maxsize=2)
def _read(path, modified, size):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        entries = data.get("entries", [])
        return [dict(entry, checked_at=data.get("checked_at", "unknown")) for entry in entries
                if isinstance(entry, dict) and all(isinstance(entry.get(key), str) for key in ("question", "answer", "source"))]
    except (OSError, ValueError, AttributeError, TypeError):
        return []


def entries():
    try:
        stat = REFERENCE_PATH.stat()
        return _read(str(REFERENCE_PATH), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return []


def match(question):
    key = normal(question)
    return next((entry for entry in entries() if key in {normal(text) for text in [entry["question"], *entry.get("aliases", [])]}), None)


def answer(question):
    entry = match(question)
    if entry is None:
        return None
    text = entry["answer"]
    if entry["source"].startswith("https://"):
        text += f"\n[Discord source]({entry['source']}) · Checked {entry['checked_at']}"
    return text


def context(question):
    words = set(normal(question).split()) - STOP_WORDS
    ranked = sorted(entries(), key=lambda entry: -len(words.intersection(normal(
        entry["question"] + " " + " ".join(entry.get("tags", []))).split())))
    related = [entry for entry in ranked if words.intersection(normal(entry["question"] + " " + " ".join(entry.get("tags", []))).split())]
    return "\n\n".join(f"Question: {entry['question']}\nAnswer: {entry['answer']}\nSource: {entry['source'] if entry['source'].startswith('https://') else 'Kitsu implemented command definitions'}\nChecked: {entry['checked_at']}"
                       for entry in related[:2])[:1600]
