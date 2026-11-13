import logging

import discord
from discord.ext import commands

from app.database.repositories.prefix_repository import (
    get_prefix as get_guild_prefix,
)
from app.services.schedule_service import ScheduleService


logger = logging.getLogger(__name__)


class Kitsu(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True

        super().__init__(
            command_prefix=self.get_prefix,
            strip_after_prefix=True,
            intents=intents,
        )

        self.schedule_service = None
        self._synced_guild_ids: set[int] = set()

    async def setup_hook(self):
        # Load extensions.
        await self.load_extension("app.cogs.emoji_gif")
        print("Emoji GIF extension loaded")

        await self.load_extension("app.cogs.prefix")
        print("Prefix extension loaded")

        await self.load_extension("app.cogs.schedules")
        print("Schedules extension loaded")

        await self.load_extension("app.cogs.sticky")
        print("Sticky extension loaded")

        await self.load_extension("app.cogs.active_channels")
        print("Active channels extension loaded")

        await self.load_extension("app.cogs.steal_emoji")
        print("Steal emoji extension loaded")

        await self.load_extension("app.cogs.emoji_mood")
        print("Emoji mood extension loaded")

        await self.load_extension("app.cogs.ghosty")
        print("Ghosty extension loaded")

        await self.load_extension("app.cogs.server_chat")
        print("Read-only server chat extension loaded")

        # Start the schedule service.
        self.schedule_service = ScheduleService(self)
        self.schedule_service.start()
        print("Schedule service started")

        # The gateway cache is empty in setup_hook. Fetch joined servers over
        # HTTP so every restart registers new/changed/deleted commands before
        # connecting. Keep the source definitions for servers that join later.
        async for guild in self.fetch_guilds(limit=None):
            await self.sync_guild_commands(guild)

    async def sync_guild_commands(self, guild: discord.abc.Snowflake) -> None:
        self.tree.clear_commands(guild=guild)
        self.tree.copy_global_to(guild=guild)
        try:
            synced = await self.tree.sync(guild=guild)
        except (discord.HTTPException, discord.RateLimited):
            self._synced_guild_ids.discard(guild.id)
            logger.exception("Could not sync commands in guild %s", guild.id)
            return
        self._synced_guild_ids.add(guild.id)
        logger.info("Synced commands in guild %s: %s", guild.id, [cmd.name for cmd in synced])

    async def get_prefix(self, message: discord.Message):
        # Ignore DMs.
        if message.guild is None:
            return ",k"

        return await get_guild_prefix(message.guild.id)

    async def on_message(self, message: discord.Message):
        # Prefix commands only work in servers, not DMs.
        if message.guild is None:
            return

        if message.author.bot:
            return
        ctx = await self.get_context(message)
        chat = self.get_cog("ServerChat")
        if ctx.prefix is None and chat is not None and await chat.handle_message(message, ctx):
            return
        await self.invoke(ctx)

    async def on_guild_join(self, guild: discord.Guild):
        await self.sync_guild_commands(guild)

    async def on_guild_available(self, guild: discord.Guild):
        if guild.id not in self._synced_guild_ids:
            await self.sync_guild_commands(guild)

    async def on_guild_remove(self, guild: discord.Guild):
        self._synced_guild_ids.discard(guild.id)
        self.tree.clear_commands(guild=guild)

    async def on_ready(self):
        print(f"Logged in as {self.user}")

        print(f"Connected to {len(self.guilds)} servers")

        print(
            "Loaded prefix commands:",
            [command.name for command in self.commands],
        )

        print(
            "Loaded slash command definitions:",
            [command.name for command in self.tree.get_commands()],
        )
