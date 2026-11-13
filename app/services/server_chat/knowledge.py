"""Explicitly approved public FAQs. Ordinary conversations never write here."""

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile

from .context import normal
from .runtime import ROOT
from .reference import context as reference_context


DATA_DIRECTORY = ROOT / "data" / "server-chat"
SEED_PATH = ROOT / "llm" / "server_chat" / "knowledge.json"
MAX_ENTRIES = 500
STOP_WORDS = set("what is are the a an do does how can i we you me my to for in on of this that server please tell about".split())


class KnowledgeError(ValueError):
    pass


def load_entries(guild_id, *, strict=False):
    path = DATA_DIRECTORY / f"{guild_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("guild_id") != guild_id or not isinstance(data.get("entries"), list):
            raise ValueError("Invalid knowledge document")
        entries = data["entries"]
        if len(entries) > MAX_ENTRIES or any(
            not isinstance(entry, dict) or not isinstance(entry.get("question"), str)
            or not isinstance(entry.get("answer"), str) or not normal(entry["question"])
            for entry in entries
        ):
            raise ValueError("Invalid knowledge entries")
        return entries
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as error:
        if strict:
            raise KnowledgeError("I couldn't read the saved FAQs. Nothing was changed; check the knowledge file.") from error
        return []


def exact_answer(question, guild_id, allowed_ids=None):
    key = normal(question)
    return next((entry["answer"] for entry in load_entries(guild_id)
                 if normal(entry["question"]) == key and permitted(render_entry(entry), allowed_ids)), None)


def render_entry(entry):
    return f"Question: {entry['question']}\nApproved answer: {entry['answer']}"


def permitted(text, allowed_ids):
    return allowed_ids is None or all(int(value) in allowed_ids for value in re.findall(r"<#(\d+)>", text))


def knowledge(question, guild_id=None, allowed_ids=None):
    try:
        data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
        notes = data.get("notes", []) if isinstance(data, dict) else []
        notes = [note for note in notes if isinstance(note, str)] if isinstance(notes, list) else []
    except (OSError, ValueError):
        notes = []
    words = set(normal(question).split()) - STOP_WORDS
    entries = load_entries(guild_id) if guild_id is not None else []
    entries = [entry for entry in entries if permitted(render_entry(entry), allowed_ids)]
    notes = [note for note in notes if permitted(note, allowed_ids)]
    def score(entry):
        return 2 * len(words.intersection(normal(entry["question"]).split())) + len(words.intersection(normal(entry["answer"]).split()))

    related = [entry for entry in sorted(entries, key=lambda entry: -score(entry)) if score(entry)]
    notes.sort(key=lambda note: -len(words.intersection(normal(note).split())))
    approved = "\n\n".join([render_entry(entry) for entry in related[:2]] + notes[:3])[:1800]
    return approved


def grounded_knowledge(question, guild_id=None, allowed_ids=None):
    return (profile_context(guild_id) + "\n" + knowledge(question, guild_id, allowed_ids)[:1600]
            + "\n" + reference_context(question))[:3400]


def profile_context(guild_id):
    try:
        data = json.loads((DATA_DIRECTORY / f"profile-{guild_id}.json").read_text(encoding="utf-8"))
        if data.get("guild_id") != guild_id:
            return ""
        return "APPROVED SERVER PROFILE (data):\n" + "\n".join(
            f"{key}: {data[key]}" for key in ("purpose", "default_language", "translation", "tone", "age_rule", "events")
            if isinstance(data.get(key), str)
        )[:900]
    except (OSError, ValueError, AttributeError):
        return ""


class TeachingStore:
    def __init__(self, guild_id):
        self.guild_id = guild_id
        self.lock = asyncio.Lock()

    async def save(self, question, answer, approved_by, message_id):
        question, answer = question.strip(), answer.strip()
        if not normal(question) or not answer:
            raise KnowledgeError("Provide both a question and its approved public answer, separated by |.")
        if len(question) > 300 or len(answer) > 1000:
            raise KnowledgeError("Keep the question within 300 characters and the answer within 1,000 characters.")
        async with self.lock:
            return await asyncio.to_thread(self._save, question, answer, approved_by, message_id)

    def _save(self, question, answer, approved_by, message_id):
        entries = load_entries(self.guild_id, strict=True)
        key = normal(question)
        existing = next((entry for entry in entries if normal(entry["question"]) == key), None)
        if existing is None and len(entries) >= MAX_ENTRIES:
            raise KnowledgeError("The FAQ store is full. Review the saved entries before adding more.")
        entry = {"question": question, "answer": answer, "approved_by": approved_by,
                 "approved_at": datetime.now(timezone.utc).isoformat(), "message_id": message_id}
        if existing is None:
            entries.append(entry)
        else:
            entries[entries.index(existing)] = entry
        DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)
        destination = DATA_DIRECTORY / f"{self.guild_id}.json"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=DATA_DIRECTORY, suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                json.dump({"version": 1, "guild_id": self.guild_id, "entries": entries}, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return existing is not None
