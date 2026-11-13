"""Compact hit cards with a persistent personal notification toggle."""
from __future__ import annotations

import asyncio
import re
from weakref import WeakValueDictionary

import discord
import emoji

from app.database.repositories import ghosty_repository as repository

from .constants import HIT_SUMMARY_COLOR, logger


_EDIT_LOCKS: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()
# Reserve about 500 px at Discord's default 14 px text size in the existing
# blank line. Zero-width anchors keep the em spaces from being trimmed; the
# spaces can still wrap when the channel is narrower than the card.
_HIT_CARD_WIDTH_SPACER = "\u200b" + "\u2003" * 36 + "\u200b"
# The thumbnail and its gutter occupy about seven ems of the same card width.
_HIT_CARD_THUMBNAIL_SPACER = "\u200b" + "\u2003" * 29 + "\u200b"


def format_hit_reaction(reaction: str) -> tuple[str, str | None]:
    """Separate the reaction heading from its emoji thumbnail."""
    reaction = reaction.strip()
    text, separator, icon = reaction.rpartition(" ")
    if separator and re.fullmatch(r"<a?:\w+:[0-9]+>", icon):
        return f"### {text}", discord.PartialEmoji.from_str(icon).url
    if separator and emoji.is_emoji(icon):
        codepoints = "-".join(f"{ord(char):x}" for char in icon.replace("\ufe0f", ""))
        return f"### {text}", (
            "https://cdn.jsdelivr.net/gh/twitter/twemoji@v14.0.2/assets/72x72/"
            f"{codepoints}.png"
        )
    return f"### {reaction}", None


def _edit_lock(message_id: int) -> asyncio.Lock:
    lock = _EDIT_LOCKS.get(message_id)
    if lock is None:
        lock = _EDIT_LOCKS[message_id] = asyncio.Lock()
    return lock


async def edit_hit_estimate(message: discord.Message, estimate: str) -> None:
    """Merge the estimate into the current card without undoing a button toggle."""
    async with _edit_lock(message.id):
        current = await message.fetch()
        view = discord.ui.LayoutView.from_message(current, timeout=None)
        texts = [item for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)]
        texts[-1].content = estimate
        await current.edit(view=view, allowed_mentions=discord.AllowedMentions.none())


class SilentPingButton(discord.ui.DynamicItem[discord.ui.Button], template=r"ghosty:silent_ping:(?P<guild_id>[0-9]+)"):
    def __init__(self, guild_id: int, *, enabled: bool = False):
        self.guild_id = guild_id
        super().__init__(discord.ui.Button(
            emoji="🔕", style=discord.ButtonStyle.danger if enabled else discord.ButtonStyle.secondary,
            custom_id=f"ghosty:silent_ping:{guild_id}",
        ))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Item, match: re.Match[str], /):
        return cls(int(match["guild_id"]), enabled=item.style == discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id != self.guild_id:
            await interaction.response.send_message("Use this button in its original server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        message = interaction.message
        color_saved = True
        async with _edit_lock(message.id if message is not None else 0):
            try:
                enabled = await repository.toggle_silent_ping(self.guild_id, interaction.user.id)
            except Exception:
                logger.exception("Could not update a Ghosty Silent ping preference")
                await interaction.followup.send("I couldn't save your Silent ping preference. Please try again.", ephemeral=True)
                return
            self.item.style = discord.ButtonStyle.danger if enabled else discord.ButtonStyle.secondary
            if message is not None:
                try:
                    current = await message.fetch()
                    view = discord.ui.LayoutView.from_message(current, timeout=None)
                    for item in view.walk_children():
                        if isinstance(item, discord.ui.Button) and item.custom_id == self.item.custom_id:
                            item.style = self.item.style
                    await current.edit(view=view, allowed_mentions=discord.AllowedMentions.none())
                except discord.HTTPException:
                    color_saved = False
                    logger.exception("Saved a Ghosty Silent ping preference but could not update its button")
        text = (
            "Silent ping enabled. When you hit, the market notification won't ping anyone, "
            "including you and the contributors. Click again to restore pings for your hits."
            if enabled else "Silent ping disabled. Your future hits can ping you and the contributors in the market again."
        )
        if not color_saved:
            text += " Your setting was saved, but I couldn't update the button's color."
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class GhostyHitView(discord.ui.LayoutView):
    def __init__(
        self, guild_id: int, summary: str, estimate: str, *,
        ping_text: str | None = None, silent_ping: bool = False,
        thumbnail_url: str | None = None,
    ):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.summary = summary
        self.ping_text = ping_text
        if ping_text:
            self.add_item(discord.ui.TextDisplay(ping_text))
        width_spacer = _HIT_CARD_THUMBNAIL_SPACER if thumbnail_url else _HIT_CARD_WIDTH_SPACER
        padded_summary = summary.rstrip().replace("\n\n", f"\n{width_spacer}\n", 1)
        summary_item = discord.ui.TextDisplay(padded_summary)
        if thumbnail_url:
            summary_item = discord.ui.Section(
                summary_item, accessory=discord.ui.Thumbnail(thumbnail_url, description="Ghosty reaction"),
            )
        self.add_item(discord.ui.Container(
            summary_item,
            discord.ui.Section(discord.ui.TextDisplay(estimate), accessory=SilentPingButton(guild_id, enabled=silent_ping)),
            accent_colour=HIT_SUMMARY_COLOR,
        ))
