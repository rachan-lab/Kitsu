from __future__ import annotations

import asyncio
import logging
import random
from time import monotonic

import discord

from app.services.active_channel_buttons import PokeBackButton
from app.services.active_channel_service import escape_text, is_panel_message, single_line, source_visible


logger = logging.getLogger(__name__)

POKE_MESSAGES = (
    "A wild **{name}** poked the chat!",
    "**{name}** used Poke. Is it super effective?",
    "**{name}** nudged the chat awake.",
    "**{name}** checked the chat’s pulse. Anyone alive?",
    "**{name}** tossed a pebble into the chat. *plop.*",
    "**{name}** has summoned the chat. Come say hi!",
)
# Discord's public sticker-pack catalogue: Wumpus, Choco, Clyde, Nelly and Phibi.
WAVE_STICKER_IDS = (
    749054660769218631, 781291131828699156, 754108890559283200,
    751606379340365864, 783787234091466793,
)
POKE_COOLDOWN = 10


class ActiveChannelPokes:
    def __init__(self, cog):
        self.cog = cog
        self._cooldowns: dict[tuple[str, int, int], float] = {}

    async def _target(self, interaction: discord.Interaction, guild_id: int, channel_id: int, action: str):
        guild = interaction.guild
        if guild is None or interaction.guild_id != guild_id:
            raise ValueError("Use this button in its original server.")
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise ValueError("That chat is no longer available.")
        panel = self.cog._panels.get(guild_id)
        if panel is None:
            # Recover the whitelist once after a restart. Warm clicks read the
            # current cached configuration without waiting on panel refresh I/O.
            async with self.cog._lock(guild_id):
                panel = await self.cog._get_panel(guild)
        if panel is None or channel_id not in panel.tracked:
            raise ValueError("That chat is no longer in the active channel list.")
        if not panel.poke_enabled.get(channel_id, True):
            raise ValueError("Poking is disabled for that chat. Open it from the activity panel instead.")
        destination = guild.get_channel(panel.channel_id)
        if not isinstance(destination, discord.TextChannel) or not source_visible(channel, destination):
            raise ValueError("That chat is currently unavailable for poking.")
        expected_channel = panel.channel_id if action == "poke" else channel_id
        message = interaction.message
        if (
            interaction.channel_id != expected_channel
            or message is None
            or message.author.id != self.cog.bot.user.id
            or (action == "poke" and not is_panel_message(message, self.cog.bot.user.id))
        ):
            raise ValueError("Use this button on its original message.")
        if not isinstance(interaction.user, discord.Member):
            raise ValueError("Only server members can poke a chat.")
        permissions = channel.permissions_for(interaction.user)
        if not permissions.view_channel or not permissions.send_messages or interaction.user.is_timed_out():
            raise ValueError("You need permission to view and send messages in that chat.")
        bot_permissions = channel.permissions_for(guild.me)
        if not bot_permissions.view_channel or not bot_permissions.send_messages:
            raise ValueError("I need View Channel and Send Messages in that chat.")
        return channel

    def _reserve(self, action: str, channel_id: int, user_id: int) -> None:
        now = monotonic()
        self._cooldowns = {key: expiry for key, expiry in self._cooldowns.items() if expiry > now}
        key = (action, channel_id, user_id)
        if key in self._cooldowns:
            raise ValueError("Give that chat a moment. You can use this button again in a few seconds.")
        self._cooldowns[key] = now + POKE_COOLDOWN

    async def handle(self, interaction: discord.Interaction, guild_id: int, channel_id: int, *, action: str):
        # A silent component acknowledgement creates no private response. Run it
        # alongside delivery rather than adding another round trip before send.
        acknowledgement = asyncio.create_task(interaction.response.defer(thinking=False))
        error_message = None
        try:
            channel = await self._target(interaction, guild_id, channel_id, action)
            self._reserve(action, channel_id, interaction.user.id)
            name = escape_text(single_line(interaction.user.display_name, 80) or "Someone")
            if action == "poke":
                view = discord.ui.View(timeout=None)
                view.add_item(PokeBackButton(guild_id, channel_id))
                await channel.send(
                    content=random.choice(POKE_MESSAGES).format(name=name), view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                sticker_id = random.choice(WAVE_STICKER_IDS)
                # Sending stickers needs their known IDs, not a metadata GET.
                sticker = discord.StickerItem(state=self.cog.bot._connection, data={
                    "id": str(sticker_id), "name": "Wave",
                    "format_type": 2 if sticker_id == 781291131828699156 else 3,
                })
                await channel.send(
                    content=f"**{name}** poked back!", stickers=[sticker],
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except ValueError as error:
            error_message = str(error)
        except (discord.HTTPException, discord.RateLimited):
            logger.warning("Could not send %s in channel %s", action, channel_id, exc_info=True)
            error_message = "I couldn't send that poke. Please try again in a moment."
        except Exception:
            logger.exception("Could not process %s in channel %s", action, channel_id)
            error_message = "Poking is temporarily unavailable. Please try again."
        finally:
            await acknowledgement
        if error_message is not None:
            await interaction.followup.send(error_message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
