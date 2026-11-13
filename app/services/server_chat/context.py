"""Copy permitted server metadata into immutable facts, without fetching history."""

from dataclasses import dataclass
import hashlib
import json

import discord


def safe(value, limit=160):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value or "")[:limit]))


def normal(value):
    return " ".join("".join(c if c.isalnum() else " " for c in str(value).casefold()).split())


@dataclass(frozen=True)
class ChannelFact:
    id: int
    name: str
    category_id: int | None
    kind: str
    topic: str = ""
    parent_id: int | None = None


@dataclass(frozen=True)
class CategoryFact:
    id: int
    name: str


@dataclass(frozen=True)
class ServerFacts:
    guild_id: int
    name: str
    current_channel_id: int
    current_category_id: int | None
    channels: tuple[ChannelFact, ...]
    categories: tuple[CategoryFact, ...]
    private: bool = False

    @property
    def fingerprint(self):
        return hashlib.sha256(repr((self.channels, self.categories, self.private)).encode()).hexdigest()

    def context(self, question, category_id=None):
        words = set(normal(question).split())
        ranked = sorted(self.channels, key=lambda c: (
            c.id != self.current_channel_id,
            not words.intersection(normal(c.name + " " + c.topic).split()),
            c.category_id != category_id,
        ))
        data = {
            "server": self.name[:80],
            "current_category_id": self.current_category_id,
            "categories": [{"id": c.id, "name": c.name} for c in self.categories[:16]],
            "relevant_channels": [{"id": c.id, "name": c.name[:100], "kind": c.kind,
                                   "category_id": c.category_id, "parent_id": c.parent_id, "topic": c.topic[:100]}
                                  for c in ranked[:10]],
            "visibility": "member-accessible" if self.private else "safe for a public reply",
        }
        while len(json.dumps(data, ensure_ascii=False)) > 3000 and data["relevant_channels"]:
            data["relevant_channels"].pop()
        return json.dumps(data, ensure_ascii=False)


def build_facts(guild, member, current, *, private=False):
    """Public mention replies never disclose channels hidden from @everyone.

    /ask is ephemeral and can include channels accessible to the acting member.
    The current channel itself is already visible to its response audience.
    """
    channels = []
    categories = {}
    bot_member = guild.me
    if bot_member is None:
        return ServerFacts(guild.id, guild.name, current.id, None, (), (), private)
    current_parent = current.parent if isinstance(current, discord.Thread) else current
    current_category_id = getattr(current_parent, "category_id", None)
    ordered = sorted(guild.channels, key=lambda c: (getattr(c, "position", 0), c.id))
    for channel in ordered:
        if isinstance(channel, discord.CategoryChannel):
            continue
        if not channel.permissions_for(member).view_channel or not channel.permissions_for(bot_member).view_channel:
            continue
        if not private and channel.id != current.id and not channel.permissions_for(guild.default_role).view_channel:
            continue
        kind = getattr(channel.type, "name", "channel")
        channels.append(ChannelFact(channel.id, channel.name, channel.category_id, kind, getattr(channel, "topic", "") or ""))
        category = channel.category
        if category is not None:
            categories[category.id] = CategoryFact(category.id, category.name)
    allowed = {channel.id for channel in channels}
    for thread in guild.threads:
        if thread.parent_id not in allowed or thread.archived:
            continue
        if not thread.permissions_for(member).view_channel or not thread.permissions_for(bot_member).view_channel:
            continue
        if thread.is_private() and (not private or (not thread.permissions_for(member).manage_threads and thread.get_member(member.id) is None)):
            continue
        parent = next(c for c in channels if c.id == thread.parent_id)
        channels.append(ChannelFact(thread.id, thread.name, parent.category_id, "thread", parent_id=thread.parent_id))
    return ServerFacts(guild.id, guild.name, current.id, current_category_id, tuple(channels), tuple(categories.values()), private)
