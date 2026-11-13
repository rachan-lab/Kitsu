"""Cached application emojis and usable guild emojis; never invent emoji IDs."""

import logging
import re

import discord

from app.services.emoji_services import APPLICATION_EMOJIS


logger = logging.getLogger(__name__)
TOKEN = re.compile(r"<a?:[A-Za-z0-9_]+:(\d+)>")
ALIAS = re.compile(r"\[emoji:([A-Za-z0-9_]+)\]")
PURPOSES = {
    "hello": ("wave", "hello", "hi", "kitsu", "goos"),
    "thanks": ("heart", "love", "thanks", "goos"),
    "success": ("tickchecked", "check", "success"),
    "warning": ("warning", "crossunchecked", "error"),
    "guide": ("gear", "help", "question"),
    "update": ("new", "news"),
    "loading": ("loading",),
}


class ChatEmojis:
    def __init__(self):
        self.application = {name: discord.PartialEmoji(name=name, id=value, animated=name == "loading")
                            for name, value in APPLICATION_EMOJIS.items()}

    async def load(self, bot):
        try:
            self.application = {emoji.name: emoji for emoji in await bot.fetch_application_emojis()}
        except (discord.HTTPException, AttributeError):
            logger.warning("Could not refresh chat application emojis; using configured emoji IDs")

    def available(self, guild):
        server = [emoji for emoji in getattr(guild, "emojis", ()) if emoji.available and emoji.is_usable()]
        return [*server, *self.application.values()]

    def select(self, guild, purpose):
        available = self.available(guild)
        for name in PURPOSES.get(purpose, ()):
            found = next((emoji for emoji in available if name in emoji.name.casefold().split("_")), None)
            if found is not None:
                return str(found)
        return ""

    def prompt(self, guild, question=""):
        words = set(re.findall(r"[a-z]+", question.casefold()))
        available = self.available(guild)
        available.sort(key=lambda emoji: -len(words.intersection(emoji.name.casefold().split("_"))))
        return list(dict.fromkeys(f"[emoji:{emoji.name}]" for emoji in available[:16]))

    def render(self, text, guild, purpose=None):
        available = self.available(guild)
        names = {}
        for emoji in available:
            names.setdefault(emoji.name.casefold(), str(emoji))
        text = ALIAS.sub(lambda match: names.get(match.group(1).casefold(), ""), text)
        allowed = {emoji.id: str(emoji) for emoji in available}
        count = [0]

        def replace(match):
            value = allowed.get(int(match.group(1)), "")
            if value and count[0] < 2:
                count[0] += 1
                return value
            return ""

        text = TOKEN.sub(replace, text).strip()
        if purpose is None:
            lower = text.casefold()
            if lower.startswith("hey!"):
                purpose = "hello"
            elif lower.startswith("you're welcome"):
                purpose = "thanks"
            elif lower.startswith("to "):
                purpose = "guide"
            elif "· checked " in lower or "checked snapshot" in lower:
                purpose = "update"
            elif "i couldn't" in lower or "i can't" in lower or "isn't enabled" in lower:
                purpose = "warning"
        if not count[0] and purpose:
            emoji = self.select(guild, purpose)
            if emoji:
                text = emoji + " " + text
        return text[:1990]
