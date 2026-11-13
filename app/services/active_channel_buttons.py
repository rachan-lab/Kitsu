from __future__ import annotations

import re

import discord

from app.services.emoji_services import get_cached_application_emoji


class _ChannelButton:
    action: str

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Item, match: re.Match[str], /):
        return cls(int(match["guild_id"]), int(match["channel_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("ActiveChannels")
        if cog is None:
            await interaction.response.send_message("Poking is temporarily unavailable. Please try again.", ephemeral=True)
            return
        await cog.pokes.handle(interaction, self.guild_id, self.channel_id, action=self.action)


class PokeButton(
    _ChannelButton, discord.ui.DynamicItem[discord.ui.Button],
    template=r"activechannels:poke:(?P<guild_id>[0-9]+):(?P<channel_id>[0-9]+)",
):
    action = "poke"

    def __init__(self, guild_id: int, channel_id: int):
        self.guild_id, self.channel_id = guild_id, channel_id
        super().__init__(discord.ui.Button(
            label="🖱️", style=discord.ButtonStyle.success,
            custom_id=f"activechannels:poke:{guild_id}:{channel_id}",
        ))


class PokeBackButton(
    _ChannelButton, discord.ui.DynamicItem[discord.ui.Button],
    template=r"activechannels:poke_back:(?P<guild_id>[0-9]+):(?P<channel_id>[0-9]+)",
):
    action = "poke_back"

    def __init__(self, guild_id: int, channel_id: int, *, emoji: str | None = None):
        self.guild_id, self.channel_id = guild_id, channel_id
        super().__init__(discord.ui.Button(
            label="Poke back", emoji=emoji or get_cached_application_emoji("poke"),
            style=discord.ButtonStyle.secondary,
            custom_id=f"activechannels:poke_back:{guild_id}:{channel_id}",
        ))
