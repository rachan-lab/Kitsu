from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from app.database.repositories import active_channel_repository as repository
from app.services.active_channel_buttons import PokeBackButton, PokeButton
from app.services.active_channel_service import (
    DEFAULT_DISPLAY_CHANNEL_ID,
    REFRESH_SECONDS,
    ActivityPanel,
    activity_from,
    human_message,
    is_panel_message,
    source_visible,
)

from .commands import ActiveChannelCommands
from .delivery import ActiveChannelDelivery
from .poke import ActiveChannelPokes


logger = logging.getLogger(__name__)


@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
class ActiveChannels(ActiveChannelCommands, ActiveChannelDelivery, commands.GroupCog, group_name="activechannels"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._panels: dict[int, ActivityPanel] = {}
        self._loaded: set[int] = set()
        self._locks: dict[int, asyncio.Lock] = {}
        self.pokes = ActiveChannelPokes(self)

    async def cog_load(self):
        self.bot.add_dynamic_items(PokeButton, PokeBackButton)
        self.refresh_panels.start()

    def cog_unload(self):
        self.refresh_panels.cancel()
        self.bot.remove_dynamic_items(PokeButton, PokeBackButton)

    def _lock(self, guild_id: int) -> asyncio.Lock:
        return self._locks.setdefault(guild_id, asyncio.Lock())

    async def _get_panel(self, guild: discord.Guild) -> ActivityPanel | None:
        if guild.id in self._panels:
            return self._panels[guild.id]
        if guild.id in self._loaded:
            return None
        default = guild.get_channel(DEFAULT_DISPLAY_CHANNEL_ID)
        if isinstance(default, discord.TextChannel):
            await repository.ensure_panel(guild.id, default.id)
        record = await repository.get_panel(guild.id)
        if record is not None:
            panel = ActivityPanel(
                guild_id=guild.id,
                channel_id=int(record["channel_id"]),
                tracked={int(value) for value in record.get("tracked_channel_ids", [])},
                message_id=record.get("message_id"),
                saved_message_id=record.get("message_id"),
                poke_enabled={int(key): value for key, value in record.get("poke_enabled", {}).items()},
            )
            self._panels[guild.id] = panel
        self._loaded.add(guild.id)
        return self._panels.get(guild.id)

    @tasks.loop(seconds=REFRESH_SECONDS)
    async def refresh_panels(self):
        for guild in self.bot.guilds:
            try:
                await self.refresh_guild(guild)
            except Exception:
                logger.exception("Could not refresh active-channel panel in guild %s; will retry.", guild.id)

    @refresh_panels.before_loop
    async def before_refresh(self):
        await self.bot.wait_until_ready()

    async def refresh_guild(self, guild: discord.Guild):
        async with self._lock(guild.id):
            panel = await self._get_panel(guild)
            if panel is None:
                return
            destination = guild.get_channel(panel.channel_id)
            if not isinstance(destination, discord.TextChannel):
                return
            await self._refresh_sources(panel, destination)
            await self._deliver(panel, destination)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or not isinstance(message.channel, discord.TextChannel):
            return
        async with self._lock(message.guild.id):
            panel = self._panels.get(message.guild.id)
            if panel is None:
                return
            if message.channel.id == panel.channel_id:
                if not self.bot.user or not is_panel_message(message, self.bot.user.id):
                    panel.repost = True
                return
            destination = message.guild.get_channel(panel.channel_id)
            if (
                message.channel.id not in panel.tracked
                or not human_message(message)
                or not isinstance(destination, discord.TextChannel)
                or not source_visible(message.channel, destination)
            ):
                return
            previous = panel.activities.get(message.channel.id)
            if previous is None or message.id > previous.message_id:
                panel.activities[message.channel.id] = activity_from(message)
                panel.scanned.add(message.channel.id)

    def _invalidate_messages(self, guild_id: int | None, channel_id: int, message_ids: set[int]):
        panel = self._panels.get(guild_id)
        if panel is None:
            return
        if channel_id == panel.channel_id and panel.message_id in message_ids:
            panel.current = None
            panel.message_id = None
            panel.repost = True
        activity = panel.activities.get(channel_id)
        if activity is not None and activity.message_id in message_ids:
            panel.activities.pop(channel_id, None)
            panel.rescan.add(channel_id)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        if payload.guild_id is not None:
            async with self._lock(payload.guild_id):
                self._invalidate_messages(payload.guild_id, payload.channel_id, {payload.message_id})

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent):
        if payload.guild_id is not None:
            async with self._lock(payload.guild_id):
                self._invalidate_messages(payload.guild_id, payload.channel_id, payload.message_ids)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        if payload.guild_id is not None:
            async with self._lock(payload.guild_id):
                panel = self._panels.get(payload.guild_id)
                if panel is not None:
                    activity = panel.activities.get(payload.channel_id)
                    if activity is not None and activity.message_id == payload.message_id:
                        panel.activities.pop(payload.channel_id, None)
                        panel.rescan.add(payload.channel_id)

    @commands.Cog.listener()
    async def on_ready(self):
        for panel in self._panels.values():
            async with self._lock(panel.guild_id):
                panel.rescan.update(panel.tracked)
                panel.recovered = False

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        self._panels.pop(guild.id, None)
        self._loaded.discard(guild.id)
        self._locks.pop(guild.id, None)


async def setup(bot: commands.Bot):
    await bot.add_cog(ActiveChannels(bot))
