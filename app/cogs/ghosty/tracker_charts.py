"""Attach optional charts after delivering the tracker text."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from io import BytesIO

import discord

from app.services.ghosty_tracker_chart import choose_tracker_chart, render_tracker_chart

from .constants import logger


class GhostyTrackerCharts:
    def _cancel_tracker_chart(self, channel_id):
        task = self._tracker_chart_jobs.pop(channel_id, None)
        if task is not None:
            task.cancel()

    def _queue_tracker_chart(self, message, recorded, prediction):
        kind = choose_tracker_chart(prediction)
        if kind is None:
            return
        member = getattr(message.guild, "me", None)
        if member is not None and not message.channel.permissions_for(member).attach_files:
            return
        channel_id = int(recorded["channel_id"])
        self._tracker_chart_jobs[channel_id] = self._background(
            self._attach_tracker_chart(message, recorded, deepcopy(prediction), kind)
        )

    async def _attach_tracker_chart(self, message, recorded, prediction, kind):
        channel_id = int(recorded["channel_id"])
        task = asyncio.current_task()
        try:
            data = await asyncio.to_thread(render_tracker_chart, prediction, int(recorded["stats"]["streak"]), kind)
            if data is None:
                return
            key = (int(recorded["guild_id"]), channel_id)
            async with self._live_result_locks.setdefault(key, asyncio.Lock()):
                if self._tracker_chart_jobs.get(channel_id) is not task:
                    return
                embed = message.embeds[0].copy()
                embed.set_image(url="attachment://ghosty-tracker.png")
                chart = discord.File(BytesIO(data), filename="ghosty-tracker.png")
                try:
                    await message.edit(embed=embed, attachments=[chart], allowed_mentions=discord.AllowedMentions.none())
                finally:
                    chart.close()
        except discord.NotFound:
            pass
        except Exception:
            logger.exception("Could not attach a Ghosty tracker chart in channel %s", channel_id)
        finally:
            if self._tracker_chart_jobs.get(channel_id) is task:
                self._tracker_chart_jobs.pop(channel_id, None)
