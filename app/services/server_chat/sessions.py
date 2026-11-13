"""Bounded, short-lived conversations, isolated by server, room, member and scope."""

from collections import OrderedDict
from dataclasses import dataclass, field
import time


@dataclass
class Conversation:
    updated: float
    category_id: int | None = None
    intent: str | None = None
    fingerprint: str = ""
    turns: list[dict] = field(default_factory=list)
    reply_ids: list[int] = field(default_factory=list)
    busy: bool = False
    language: str = "en"
    last_answer: str = ""
    last_answer_language: str = "en"
    last_answer_kind: str = "answer"

    def remember(self, question, answer):
        self.turns.extend(({"role": "user", "content": question[:500]}, {"role": "assistant", "content": answer[:700]}))
        self.turns = self.turns[-4:]

    def sync(self, fingerprint):
        if self.fingerprint and self.fingerprint != fingerprint:
            self.turns.clear()
            self.category_id = None
            self.last_answer = ""
            self.last_answer_language = "en"
            self.last_answer_kind = "answer"
        self.fingerprint = fingerprint


class Conversations:
    def __init__(self, ttl=120, limit=512, *, clock=time.monotonic):
        self.ttl, self.limit, self.clock = ttl, limit, clock
        self.items = OrderedDict()

    def get(self, key, *, create=False):
        now = self.clock()
        expired = [key for key, value in self.items.items() if now - value.updated > self.ttl and not value.busy]
        for old in expired:
            self.items.pop(old, None)
        item = self.items.get(key)
        if item is None and create:
            if len(self.items) >= self.limit:
                idle = next((key for key, value in self.items.items() if not value.busy), None)
                if idle is None:
                    return None
                self.items.pop(idle)
            item = self.items[key] = Conversation(updated=now)
        if item is not None:
            self.items.move_to_end(key)
        return item

    def touch(self, item):
        item.updated = self.clock()

    def forget(self, key):
        self.items.pop(key, None)
