"""Private destination and upload-type selection for shared steal previews."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import discord

from app.bot.permissions import is_guild_moderator

from .constants import STEAL_TIMEOUT, logger
from .models import StealItem


async def mutual_guilds(client: discord.Client, user_id: int) -> list[discord.Guild]:
    """Use cached memberships, fetching misses without blocking the gateway."""
    semaphore = asyncio.Semaphore(5)

    async def shared(guild):
        if guild.unavailable:
            return None
        if guild.owner_id == user_id or guild.get_member(user_id) is not None:
            return guild
        async with semaphore:
            try:
                await guild.fetch_member(user_id)
            except discord.NotFound:
                return None
            except (discord.HTTPException, discord.RateLimited):
                logger.warning("Could not check steal membership in guild %s", guild.id)
                return None
        return guild

    results = await asyncio.gather(*(shared(guild) for guild in client.guilds))
    return sorted((guild for guild in results if guild is not None), key=lambda guild: (guild.name.casefold(), guild.id))


class DestinationSelect(discord.ui.Select):
    def __init__(self, view: StealDestinationView):
        start = view.page * 25
        super().__init__(
            placeholder=f"Choose a server ({view.page + 1}/{view.page_count})",
            options=[discord.SelectOption(
                label=guild.name[:100], value=str(guild.id),
                default=guild.id == view.guild_id,
            ) for guild in view.guilds[start:start + 25]],
            row=0,
        )
        self.menu = view

    async def callback(self, interaction: discord.Interaction):
        self.menu.guild_id = int(self.values[0])
        self.menu.render()
        await interaction.response.edit_message(view=self.menu)


class UploadTypeSelect(discord.ui.Select):
    def __init__(self, view: StealDestinationView):
        kinds = (view.item.item_type, "sticker" if view.item.item_type == "emoji" else "emoji")
        super().__init__(
            placeholder="Add as...", row=1,
            options=[discord.SelectOption(
                label=f"As {kind}", value=kind, default=kind == view.kind,
            ) for kind in kinds],
        )
        self.menu = view

    async def callback(self, interaction: discord.Interaction):
        self.menu.kind = self.values[0]
        self.menu.render()
        await interaction.response.edit_message(view=self.menu)


class DestinationPageButton(discord.ui.Button):
    def __init__(self, view: StealDestinationView, step: int):
        super().__init__(
            label="Previous servers" if step < 0 else "Next servers",
            style=discord.ButtonStyle.secondary, row=2,
            disabled=view.page == (0 if step < 0 else view.page_count - 1),
        )
        self.menu, self.step = view, step

    async def callback(self, interaction: discord.Interaction):
        self.menu.page += self.step
        self.menu.guild_id = None
        self.menu.render()
        await interaction.response.edit_message(view=self.menu)


class UploadButton(discord.ui.Button):
    def __init__(self, view: StealDestinationView):
        super().__init__(
            label=f"Add as {view.kind}", style=discord.ButtonStyle.primary,
            row=3, disabled=view.guild_id is None,
        )
        self.menu = view

    async def callback(self, interaction: discord.Interaction):
        view = self.menu
        # Also guard here so duplicate clicks cannot schedule two uploads.
        if not await view.interaction_check(interaction):
            return
        if view.guild_id is None:
            await interaction.response.send_message("Choose a destination server first.", ephemeral=True)
            return
        guild_id, kind = view.guild_id, view.kind
        if guild_id not in {guild.id for guild in view.guilds} or kind not in ("emoji", "sticker"):
            await interaction.response.send_message("Choose a valid server and upload type.", ephemeral=True)
            return
        view.busy = True
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            guild = interaction.client.get_guild(guild_id)
            if guild is None or guild.unavailable:
                await interaction.followup.send("Kitsu is no longer connected to that server. Open Steal again.", ephemeral=True)
                return
            # Fetch again: a member may have left or lost their roles since the
            # picker opened. Source-server privileges never authorize a target.
            try:
                member = await guild.fetch_member(interaction.user.id)
            except (discord.HTTPException, discord.RateLimited):
                await interaction.followup.send("I couldn't verify your membership in that server. Open Steal again.", ephemeral=True)
                return
            if not is_guild_moderator(member):
                await interaction.followup.send("Only server admins and moderators can add assets to the selected server.", ephemeral=True)
                return
            bot_member = guild.me
            if bot_member is None or not (
                bot_member.guild_permissions.administrator or bot_member.guild_permissions.create_expressions
            ):
                await interaction.followup.send("I need the **Create Expressions** permission in the selected server.", ephemeral=True)
                return
            from .components import upload_to_server

            await upload_to_server(interaction, guild, replace(view.item, item_type=kind))
            # Consume this picker even if the API result was uncertain, avoiding
            # accidental duplicate uploads from a second queued interaction.
            view.finished = True
            view.stop()
            for child in view.children:
                child.disabled = True
            if view.message is not None:
                try:
                    await view.message.edit(view=view)
                except discord.HTTPException:
                    logger.warning("Could not disable completed steal picker")
        finally:
            view.busy = False


class StealDestinationView(discord.ui.View):
    def __init__(self, item: StealItem, owner_id: int, guilds: list[discord.Guild]):
        super().__init__(timeout=STEAL_TIMEOUT)
        self.item, self.owner_id, self.guilds = item, owner_id, guilds
        self.guild_id: int | None = None
        self.kind = item.item_type
        self.page = 0
        self.busy = False
        self.finished = False
        self.message = None
        self.render()

    @property
    def page_count(self):
        return (len(self.guilds) + 24) // 25

    def render(self):
        self.clear_items()
        self.add_item(DestinationSelect(self))
        self.add_item(UploadTypeSelect(self))
        if self.page_count > 1:
            self.add_item(DestinationPageButton(self, -1))
            self.add_item(DestinationPageButton(self, 1))
        self.add_item(UploadButton(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            text = "Only the person who opened this server picker can use it."
        elif self.finished or self.is_finished():
            text = "This server picker is inactive. Open Steal again."
        elif self.busy:
            text = "An upload is already in progress."
        else:
            return True
        await interaction.response.send_message(text, ephemeral=True)
        return False

    async def on_timeout(self):
        self.finished = True
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                logger.warning("Could not disable expired steal picker")
