"""A compact Components V2 panel for previous attempts and the current streak."""

from __future__ import annotations

import asyncio
from io import BytesIO
import logging
from typing import Any

import discord

from app.services.ghosty_streak_service import (
    GhostyStreakService, StreakSnapshot, render_previous_hits_chart, render_streak_chart,
)


logger = logging.getLogger(__name__)
CHART_FILENAME = "ghosty-streak.png"


def format_selected_forecast(prediction: dict[str, Any]) -> str:
    """Show the selected attempt and its conditional hit probability."""
    hit_at = prediction.get("hit_at")
    percent = prediction.get("candidate_percent")
    if not prediction.get("ready") or hit_at is None or percent is None:
        return ""
    target = f"{hit_at:,}"
    chance = "<0.1%" if 0 < percent < 0.1 else f"{percent:.1f}".rstrip("0").rstrip(".") + "%"
    if prediction.get("estimate_status") == "missed":
        target = f"was {target} MISSED"
    return f"Estimated hit at `{target}` @ `{chance} surity.`"


class GhostyStreakView(discord.ui.LayoutView):
    def __init__(
        self, *, service: GhostyStreakService, snapshot: StreakSnapshot,
        owner_id: int, guild_id: int, channel_id: int, prefix: str,
        can_attach_files: bool = True,
    ):
        super().__init__(timeout=300)
        self.service, self.snapshot = service, snapshot
        self.owner_id, self.guild_id, self.channel_id = owner_id, guild_id, channel_id
        self.prefix, self.can_attach_files = prefix, can_attach_files
        self.mode = "previous"
        self.message: discord.Message | None = None
        self._charts: dict[str, bytes] = {}
        self._lock = asyncio.Lock()
        self._expired = False
        self._build()

    async def chart_file(self) -> discord.File | None:
        if not self.can_attach_files or (self.mode == "previous" and not self.snapshot.previous_attempts):
            return None
        if self.mode not in self._charts:
            render = render_previous_hits_chart if self.mode == "previous" else render_streak_chart
            self._charts[self.mode] = await asyncio.to_thread(render, self.snapshot)
        return discord.File(BytesIO(self._charts[self.mode]), filename=CHART_FILENAME)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self._expired:
            text = f"This panel expired. Run `{self.prefix}ghosty streak` again."
        elif interaction.user.id != self.owner_id:
            text = f"Run `{self.prefix}ghosty streak` to open your own channel overview."
        elif (
            interaction.guild is None or interaction.guild.id != self.guild_id
            or interaction.channel_id != self.channel_id
        ):
            text = "Use this panel in its original channel."
        else:
            return True
        await interaction.response.send_message(text, ephemeral=True)
        return False

    def _build(self) -> None:
        self.clear_items()
        menu = discord.ui.Select(
            placeholder="Choose a view...", custom_id="ghosty:streak:view",
            options=[
                discord.SelectOption(label=label, value=value, default=self.mode == value)
                for label, value in (("Previous Attempts", "previous"), ("Current Streak", "current"))
            ],
        )

        async def choose(interaction: discord.Interaction) -> None:
            await self._update(interaction, mode=menu.values[0])

        menu.callback = choose
        refresh = discord.ui.Button(label="Refresh", custom_id="ghosty:streak:refresh", style=discord.ButtonStyle.primary)

        async def reload_data(interaction: discord.Interaction) -> None:
            await self._update(interaction, refresh=True)

        refresh.callback = reload_data
        menu.disabled = refresh.disabled = self._expired
        previous = self.mode == "previous"
        count = min(len(self.snapshot.previous_attempts), 5) or 5
        heading = f"> Previous Attempts\n\n**Last {count} {'hit' if count == 1 else 'hits'} @**\n\n"
        items = [discord.ui.TextDisplay(heading if previous else self._current())]
        if self.can_attach_files and (not previous or self.snapshot.previous_attempts):
            if previous:
                items.append(discord.ui.Separator(visible=False))
            items.append(discord.ui.MediaGallery(discord.MediaGalleryItem(
                f"attachment://{CHART_FILENAME}",
                description="Previous hit attempts, latest first" if previous
                else "Saved possible hit attempts for the current streak",
            )))
        elif previous and self.snapshot.previous_attempts:
            items.append(discord.ui.TextDisplay("-# I need Attach Files permission to display the graph."))
        if previous:
            items.append(discord.ui.TextDisplay(self._previous()))
        items.extend((discord.ui.ActionRow(menu), discord.ui.ActionRow(refresh)))
        self.add_item(discord.ui.Container(*items, accent_colour=0x7CC6B0))

    def _previous(self) -> str:
        attempts = self.snapshot.previous_attempts[:5]
        if not attempts:
            return "No completed streaks recorded yet."
        headings = ("Hit @", "Lost", "Received", "Win/Loss")
        rows = [(f"{row.hit_at:,}", f"{row.loss:,}", f"{row.payout:,}", f"{row.net:+,}") for row in attempts]
        widths = [max(len(heading), *(len(row[index]) for row in rows)) for index, heading in enumerate(headings)]
        table = ["  ".join(value.rjust(width) for value, width in zip(row, widths)) for row in rows]
        return (
            "> -# Hit @  Lost  Received  Win/Loss\n```text\n" + "\n".join(table) + "\n```"
        )

    def _current(self) -> str:
        stats = self.snapshot.stats
        forecast = format_selected_forecast(self.snapshot.prediction)
        header = (
            "> Current Streak\n\n"
            f"**Streak:** `{int(stats.get('streak', 0)):,}` · "
            f"**Loss:** `{int(stats.get('streak_loss', 0)):,} Goos`"
        )
        return header + ("\n\n" + forecast if forecast else "")

    async def _update(
        self, interaction: discord.Interaction, *, mode: str | None = None, refresh: bool = False,
    ) -> None:
        await interaction.response.defer()
        async with self._lock:
            if mode == "current" and not self.can_attach_files:
                await interaction.followup.send("I need **Attach Files** in this channel to show the graph.", ephemeral=True)
                return
            old = self.snapshot, self.mode, self._charts
            try:
                if refresh:
                    self.snapshot = await self.service.load(self.guild_id, self.channel_id)
                    self._charts = {}
                elif mode in {"previous", "current"}:
                    self.mode = mode
                chart = await self.chart_file()
                self._build()
                await interaction.edit_original_response(
                    view=self, attachments=[chart] if chart is not None else [],
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                self.snapshot, self.mode, self._charts = old
                self._build()
                logger.exception("Could not update Ghosty streak panel for channel %s.", self.channel_id)
                await interaction.followup.send("I couldn’t update this panel. Please try again.", ephemeral=True)

    async def on_timeout(self) -> None:
        self._expired = True
        for item in self.walk_children():
            if isinstance(item, (discord.ui.Select, discord.ui.Button)):
                item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                logger.debug("Ghosty streak panel expired after its message became unavailable.")
