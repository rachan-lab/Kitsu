from __future__ import annotations

import logging
import time
from contextlib import ExitStack, closing

import discord

from app.database.repositories import active_channel_repository as repository
from app.services.active_channel_service import (
    BANNER_PATH,
    DIVIDER_PATH,
    ActivityPanel,
    activity_from,
    banner_media,
    build_view,
    human_message,
    is_panel_message,
    panel_matches,
    source_visible,
    validate_destination,
)


logger = logging.getLogger(__name__)


class ActiveChannelDelivery:
    async def _delete_panel_message(self, channel: discord.TextChannel, message_id: int):
        try:
            await channel.get_partial_message(message_id).delete()
        except discord.NotFound:
            pass

    async def _recover_message(self, panel: ActivityPanel, channel: discord.TextChannel):
        current = None
        if panel.message_id:
            try:
                candidate = await channel.fetch_message(panel.message_id)
                if is_panel_message(candidate, self.bot.user.id):
                    current = candidate
            except discord.NotFound:
                pass
        options = {"limit": None, "oldest_first": False}
        if current is not None:
            options["after"] = discord.Object(id=current.id)
        duplicates = []
        async for candidate in channel.history(**options):
            if not is_panel_message(candidate, self.bot.user.id):
                continue
            if current is None or candidate.id > current.id:
                if current is not None:
                    duplicates.append(current.id)
                current = candidate
            else:
                duplicates.append(candidate.id)
        for message_id in duplicates:
            await self._delete_panel_message(channel, message_id)
        panel.current = current
        panel.message_id = current.id if current is not None else None
        panel.recovered = True
        panel.checked_at = time.monotonic()

    async def _refresh_sources(self, panel: ActivityPanel, destination: discord.TextChannel):
        for channel_id in sorted(panel.tracked):
            channel = destination.guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel) or not source_visible(channel, destination):
                panel.activities.pop(channel_id, None)
                panel.scanned.discard(channel_id)
                continue
            if channel_id in panel.scanned and channel_id not in panel.rescan:
                continue
            # Only seed/recover channels that were explicitly selected. Gateway
            # events maintain their latest human chat between refresh cycles.
            latest = None
            try:
                async for message in channel.history(limit=None, oldest_first=False):
                    if human_message(message):
                        latest = activity_from(message)
                        break
            except (discord.HTTPException, discord.RateLimited):
                panel.activities.pop(channel_id, None)
                panel.rescan.add(channel_id)
                logger.warning("Could not read activity in channel %s; will retry.", channel_id)
                continue
            if latest is None:
                panel.activities.pop(channel_id, None)
            else:
                panel.activities[channel_id] = latest
            panel.scanned.add(channel_id)
            panel.rescan.discard(channel_id)

    async def _persist_message(self, panel: ActivityPanel):
        if panel.message_id is not None and panel.saved_message_id != panel.message_id:
            await repository.set_message(panel.guild_id, panel.message_id)
            panel.saved_message_id = panel.message_id

    async def _deliver(self, panel: ActivityPanel, channel: discord.TextChannel):
        validate_destination(channel)
        if not panel.recovered:
            await self._recover_message(panel, channel)
        await self._persist_message(panel)
        current = panel.current
        if current is not None and time.monotonic() - panel.checked_at >= 60:
            try:
                panel.current = current = await channel.fetch_message(current.id)
            except discord.NotFound:
                panel.current = current = None
                panel.message_id = None
            panel.checked_at = time.monotonic()
        latest_id = channel.last_message_id
        repost = panel.repost or (
            current is not None and latest_id is not None and latest_id > current.id
        )
        if current is not None and repost:
            # Never send another copy if deleting the old one failed.
            await self._delete_panel_message(channel, current.id)
            panel.current = current = None
            panel.message_id = None
        if current is not None:
            media = {path: banner_media(current, path) for path in (BANNER_PATH, DIVIDER_PATH)}
            urls, missing = {}, []
            for path, item in media.items():
                attachment = next((item for item in current.attachments if item.filename == path.name), None)
                if item is not None or attachment is not None:
                    urls["banner_url" if path == BANNER_PATH else "divider_url"] = (item or attachment).url
                else:
                    missing.append(path)
            view = build_view(panel, channel, **urls)
            if missing or not panel_matches(current, view):
                try:
                    with ExitStack() as files:
                        options = {}
                        if missing:
                            attachments = list(current.attachments)
                            # An explicit attachment update must also retain
                            # existing files exposed only by gallery metadata.
                            for path, item in media.items():
                                if item is None:
                                    continue
                                attachment_id = item.attachment_id
                                if attachment_id is None:
                                    candidate = item.url.split("?", 1)[0].rsplit("/", 2)[-2]
                                    attachment_id = int(candidate) if candidate.isdigit() else None
                                if attachment_id is not None and not any(value.id == attachment_id for value in attachments):
                                    attachments.append(discord.Attachment(data={
                                        "id": str(attachment_id), "filename": path.name, "size": 0,
                                        "url": item.url, "proxy_url": item.proxy_url or item.url,
                                    }, state=current._state))
                            attachments.extend(files.enter_context(closing(discord.File(path))) for path in missing)
                            options["attachments"] = attachments
                        panel.current = await current.edit(
                            content=None,
                            embeds=[],
                            view=view,
                            allowed_mentions=discord.AllowedMentions.none(),
                            **options,
                        )
                except discord.NotFound:
                    panel.current = current = None
                    panel.message_id = None
            if current is not None:
                panel.repost = False
                return
        try:
            with ExitStack() as files:
                banners = [files.enter_context(closing(discord.File(path))) for path in (BANNER_PATH, DIVIDER_PATH)]
                current = await channel.send(
                    view=build_view(panel, channel),
                    files=banners,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except Exception:
            # A timeout can arrive after Discord accepted the send. Recover it
            # before retrying so an uncertain response cannot create two copies.
            panel.recovered = False
            raise
        # Retain an accepted send in memory even if saving its ID fails. Startup
        # recovery also adopts that copy if the process exits before MongoDB saves.
        panel.current = current
        panel.message_id = current.id
        panel.repost = False
        panel.checked_at = time.monotonic()
        await self._persist_message(panel)
