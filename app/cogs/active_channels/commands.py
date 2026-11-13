from __future__ import annotations

import logging

import discord
from discord import app_commands

from app.database.repositories import active_channel_repository as repository
from app.services.active_channel_service import ActivityPanel, source_error, validate_destination


logger = logging.getLogger(__name__)


class ActiveChannelCommands:
    async def _command_error(self, interaction: discord.Interaction, error: Exception):
        if isinstance(error, ValueError):
            text = str(error)
        else:
            logger.error("Active-channel command failed.", exc_info=(type(error), error, error.__traceback__))
            text = "I couldn't update the active-channel panel. Check my channel permissions and try again."
        await interaction.followup.send(text, ephemeral=True)

    def _require_panel(self, panel: ActivityPanel | None) -> ActivityPanel:
        if panel is None:
            raise ValueError("Choose a display channel first with `/activechannels setup`.")
        return panel

    @app_commands.command(name="setup", description="Choose the sticky active-channel display channel.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel="Where the heading and activity card will stay.")
    async def setup_panel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        try:
            if channel.guild.id != interaction.guild.id:
                raise ValueError("Choose a text channel in this server.")
            validate_destination(channel)
            async with self._lock(interaction.guild.id):
                panel = await self._get_panel(interaction.guild)
                if panel is not None and panel.channel_id != channel.id:
                    old = interaction.guild.get_channel(panel.channel_id)
                    if isinstance(old, discord.TextChannel):
                        if not panel.recovered:
                            await self._recover_message(panel, old)
                        if panel.current is not None:
                            await self._delete_panel_message(old, panel.current.id)
                            panel.current = None
                    tracked = panel.tracked - {channel.id}
                    poke_enabled = {key: value for key, value in panel.poke_enabled.items() if key in tracked}
                    await repository.set_destination(
                        interaction.guild.id, channel.id, tracked, poke_enabled=poke_enabled,
                    )
                    panel = ActivityPanel(
                        interaction.guild.id, channel.id, tracked=tracked, poke_enabled=poke_enabled,
                    )
                    self._panels[panel.guild_id] = panel
                elif panel is None:
                    await repository.ensure_panel(interaction.guild.id, channel.id)
                    panel = ActivityPanel(interaction.guild.id, channel.id)
                    self._panels[panel.guild_id] = panel
                await self._deliver(panel, channel)
            await interaction.followup.send(
                f"The sticky activity panel is in {channel.mention}. Add tracked channels individually with `/activechannels add`.",
                ephemeral=True,
            )
        except Exception as error:
            await self._command_error(interaction, error)

    @app_commands.command(name="add", description="Add one channel to the active-channel list.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        channel="The conversation channel to track.",
        poke="True: show Poke buttons. False: show a link that opens the channel.",
    )
    async def add_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel, poke: bool,
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            async with self._lock(interaction.guild.id):
                panel = self._require_panel(await self._get_panel(interaction.guild))
                destination = interaction.guild.get_channel(panel.channel_id)
                if not isinstance(destination, discord.TextChannel):
                    raise ValueError("The display channel is unavailable. Choose one with `/activechannels setup`.")
                error = source_error(channel, destination)
                if error is not None:
                    raise ValueError(error)
                selected = panel.tracked | {channel.id}
                poke_enabled = {**panel.poke_enabled, channel.id: poke}
                await repository.set_channels(panel.guild_id, selected, poke_enabled=poke_enabled)
                panel.tracked = selected
                panel.poke_enabled = poke_enabled
                panel.rescan.add(channel.id)
            mode = "Poke enabled" if poke else "Open channel link enabled"
            await interaction.followup.send(
                f"Added {channel.mention}. {mode}. The panel refreshes every 10 seconds.", ephemeral=True,
            )
        except Exception as error:
            await self._command_error(interaction, error)

    @app_commands.command(name="remove", description="Remove one channel from the active-channel list.")
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        try:
            async with self._lock(interaction.guild.id):
                panel = self._require_panel(await self._get_panel(interaction.guild))
                if channel.id not in panel.tracked:
                    raise ValueError("That channel is not being tracked.")
                selected = panel.tracked - {channel.id}
                poke_enabled = {key: value for key, value in panel.poke_enabled.items() if key in selected}
                await repository.set_channels(panel.guild_id, selected, poke_enabled=poke_enabled)
                panel.tracked = selected
                panel.poke_enabled = poke_enabled
                panel.activities.pop(channel.id, None)
                panel.scanned.discard(channel.id)
                panel.rescan.discard(channel.id)
            await interaction.followup.send(f"Removed {channel.mention} from the activity list.", ephemeral=True)
        except Exception as error:
            await self._command_error(interaction, error)

    @app_commands.command(name="list", description="Show the display channel and manually selected channels.")
    @app_commands.checks.has_permissions(administrator=True)
    async def list_channels(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            async with self._lock(interaction.guild.id):
                panel = self._require_panel(await self._get_panel(interaction.guild))
                names = "\n".join(
                    f"• <#{channel_id}> — {'Poke' if panel.poke_enabled.get(channel_id, True) else 'Open channel'}"
                    for channel_id in sorted(panel.tracked)
                )
                text = f"Display: <#{panel.channel_id}>\n\nTracked channels ({len(panel.tracked)}):\n" + (names or "None. Add them individually with `/activechannels add`.")
            await interaction.followup.send(text[:1950], ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception as error:
            await self._command_error(interaction, error)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        text = "You need Administrator permission to manage active channels."
        if not isinstance(error, (app_commands.MissingPermissions, app_commands.NoPrivateMessage)):
            logger.error("Active-channel command check failed: %s", error)
            text = "I couldn't complete that active-channel command."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
