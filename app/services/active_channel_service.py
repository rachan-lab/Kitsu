from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from time import time
from unicodedata import category, east_asian_width

import discord

from app.services.active_channel_buttons import PokeButton


DEFAULT_DISPLAY_CHANNEL_ID = 1559298641670307880
HEADING = "## @ a c t i v e  c h a n n e l s"
LEGACY_FOOTER = "Recently active channels # Updates every 10 seconds"
UPDATE_PREFIX = "-# Recently active channels # Updates "
REFRESH_SECONDS = 10
DISPLAY_LIMIT = 5
PREVIEW_LIMIT = 100
ROW_WIDTH = 40
AUTHOR_WIDTH = 20
PREVIEW_PREFIX = "🤖  "
PREVIEW_INDENT = "\u00a0" * 6
CHANNEL_GAP = "\u00a0" * 5
TIME_LABEL_WIDTH = 14
TIME_LABEL_PADDING = "\u2007"
BANNER_PATH = Path(__file__).resolve().parents[2] / "assets" / "divider.gif"
BANNER_URL = f"attachment://{BANNER_PATH.name}"
DIVIDER_PATH = BANNER_PATH.with_name("divider-2.gif")
DIVIDER_URL = f"attachment://{DIVIDER_PATH.name}"
PANEL_CONTAINER_ID = 6201
CUSTOM_EMOJI = re.compile(r"(<a?:[A-Za-z0-9_]+:[0-9]+>)")


@dataclass(frozen=True)
class ChannelActivity:
    message_id: int
    timestamp: int
    author: str
    preview: str


@dataclass
class ActivityPanel:
    guild_id: int
    channel_id: int
    tracked: set[int] = field(default_factory=set)
    message_id: int | None = None
    current: discord.Message | None = None
    activities: dict[int, ChannelActivity] = field(default_factory=dict)
    scanned: set[int] = field(default_factory=set)
    rescan: set[int] = field(default_factory=set)
    recovered: bool = False
    repost: bool = False
    saved_message_id: int | None = None
    checked_at: float = 0
    poke_enabled: dict[int, bool] = field(default_factory=dict)


def _text_units(value: str) -> Iterator[str]:
    for index, part in enumerate(CUSTOM_EMOJI.split(value)):
        if index % 2:
            yield part
        else:
            yield from part


def _trim(value: str, limit: int, measure: Callable[[str], int]) -> str:
    if measure(value) <= limit:
        return value
    result, used = [], 0
    for unit in _text_units(value):
        size = measure(unit)
        if used + size > limit - 1:
            break
        result.append(unit)
        used += size
    return "".join(result).rstrip() + "…"


def single_line(value: str, limit: int = PREVIEW_LIMIT) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    return _trim(text, limit, len)


def text_width(value: str) -> int:
    """Estimate visible width; a complete custom emoji occupies two columns."""
    width = 0
    for unit in _text_units(value):
        if len(unit) > 1:
            width += 2
        elif category(unit) not in {"Cf", "Mn", "Me"}:
            width += 2 if east_asian_width(unit) in {"W", "F"} else 1
    return width


def fit_line(value: str, width: int) -> str:
    return _trim(value, width, text_width)


def escape_text(value: str) -> str:
    parts = CUSTOM_EMOJI.split(discord.utils.escape_mentions(value))
    return "".join(part if index % 2 else discord.utils.escape_markdown(part) for index, part in enumerate(parts))


def relative_age(timestamp: int | None, now: int) -> str:
    if timestamp is None:
        return "—"
    elapsed = max(0, now - timestamp)
    if elapsed < 60:
        return "just now"
    if elapsed < 3600:
        return f"{elapsed // 60} min ago"
    if elapsed < 86400:
        return f"{elapsed // 3600} hr ago"
    days = elapsed // 86400
    return f"{days} {'day' if days == 1 else 'days'} ago"


def human_message(message: discord.Message) -> bool:
    return (
        not message.author.bot
        and message.webhook_id is None
        and message.type in (discord.MessageType.default, discord.MessageType.reply)
    )


def activity_from(message: discord.Message) -> ChannelActivity:
    # Preview spoilers without revealing their contents on a public dashboard.
    text = re.sub(r"\|\|.*?\|\|", "[spoiler]", message.clean_content, flags=re.S)
    if not text.strip():
        if message.attachments:
            text = "[Attachment]" if len(message.attachments) == 1 else "[Attachments]"
        elif message.stickers:
            text = "[Sticker]"
        else:
            text = "[Message without text]"
    return ChannelActivity(
        message_id=message.id,
        timestamp=int(message.created_at.timestamp()),
        author=single_line(message.author.display_name, 80),
        preview=single_line(text),
    )


def source_error(channel: discord.TextChannel, destination: discord.TextChannel) -> str | None:
    """Validate a source explicitly selected by a server administrator."""
    if channel.guild.id != destination.guild.id:
        return "Choose a text channel in this server."
    if channel.id == destination.id:
        return "The display channel cannot track itself. Choose a conversation channel."
    permissions = channel.permissions_for(channel.guild.me)
    required = {"view_channel": "View Channel", "read_message_history": "Read Message History"}
    missing = [label for key, label in required.items() if not getattr(permissions, key)]
    if missing:
        return f"I need {', '.join(missing)} in {channel.mention} to track its activity."
    if channel.is_nsfw() and not destination.is_nsfw():
        return "An age-restricted source channel needs an age-restricted display channel."
    return None


def source_visible(channel: discord.TextChannel, destination: discord.TextChannel) -> bool:
    return source_error(channel, destination) is None


def validate_destination(channel: discord.TextChannel) -> None:
    permissions = channel.permissions_for(channel.guild.me)
    required = {
        "view_channel": "View Channel",
        "send_messages": "Send Messages",
        "read_message_history": "Read Message History",
        "embed_links": "Embed Links",
        "attach_files": "Attach Files",
    }
    missing = [label for key, label in required.items() if not getattr(permissions, key)]
    if missing:
        raise ValueError("I need these permissions in the display channel: " + ", ".join(missing) + ".")


def activity_rows(panel: ActivityPanel, destination: discord.TextChannel) -> list[tuple[str, str, int | None]]:
    channels = [
        channel
        for channel_id in panel.tracked
        if isinstance(channel := destination.guild.get_channel(channel_id), discord.TextChannel)
        and source_visible(channel, destination)
    ]
    channels.sort(
        key=lambda channel: (
            panel.activities[channel.id].message_id if channel.id in panel.activities else 0,
            channel.id,
        ),
        reverse=True,
    )
    rows = []
    for index, channel in enumerate(channels[:DISPLAY_LIMIT], start=1):
        activity = panel.activities.get(channel.id)
        name = f"{index}. {channel.mention}"
        value = "No messages yet."
        if activity is not None:
            author = fit_line(activity.author, AUTHOR_WIDTH)
            preview = fit_line(activity.preview, ROW_WIDTH - text_width(PREVIEW_PREFIX) - text_width(author) - 2)
            author = escape_text(author)
            preview = escape_text(preview)
            value = f"{PREVIEW_PREFIX}**{author}:** {preview}"
        rows.append((name, value, activity.timestamp if activity is not None else None))
    if not channels:
        rows.append(("\u200b", "No channels selected yet." if not panel.tracked else "No activity to show yet.", None))
    return rows


def build_view(
    panel: ActivityPanel, destination: discord.TextChannel, *, banner_url: str = BANNER_URL,
    divider_url: str = DIVIDER_URL,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.TextDisplay(HEADING))
    card = discord.ui.Container(accent_colour=0x313338, id=PANEL_CONTAINER_ID)
    # Components V2 allows the banner to appear above the channel list.
    card.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(banner_url, description="Active channels banner")))
    card.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(divider_url, description="Grayscale animated divider")))
    rows = activity_rows(panel, destination)
    # Preserve the existing small/large divider spacing for shorter/full lists.
    explicit_spacing = len(rows) <= 4

    def blank_lines(count: int):
        # Invisible anchors keep otherwise empty lines from being trimmed.
        return discord.ui.TextDisplay("\u200b" + "\n\u200b" * count)

    # Five channels plus both GIFs fill Discord's 40-component allowance.
    if explicit_spacing:
        card.add_item(blank_lines(1))
    now = int(time())
    for index, (name, value, timestamp) in enumerate(rows, start=1):
        if name == "\u200b":
            card.add_item(discord.ui.TextDisplay(value))
            continue
        number, _, channel = name.partition(" ")
        # Two adjacent Sections put the age beside the channel and its action
        # beside the preview. No spacer separates the lines within an entry.
        age = relative_age(timestamp, now).ljust(TIME_LABEL_WIDTH, TIME_LABEL_PADDING)
        channel_id = int(channel.removeprefix("<#").removesuffix(">"))
        card.add_item(discord.ui.Section(
            discord.ui.TextDisplay(f"### **{number}**{CHANNEL_GAP}{channel}"),
            accessory=discord.ui.Button(
                label=age, style=discord.ButtonStyle.secondary,
                custom_id=f"activechannels:time:{index}", disabled=True,
            ),
        ))
        card.add_item(discord.ui.Section(
            discord.ui.TextDisplay(f"> {PREVIEW_INDENT}{value}"),
            accessory=(
                PokeButton(panel.guild_id, channel_id)
                if panel.poke_enabled.get(channel_id, True)
                else discord.ui.Button(
                    # Keep only Discord's native link icon visible.
                    label="\u200b", style=discord.ButtonStyle.link,
                    url=f"https://discord.com/channels/{panel.guild_id}/{channel_id}",
                )
            ),
        ))
        card.add_item(discord.ui.Separator(
            spacing=discord.SeparatorSpacing.small if explicit_spacing else discord.SeparatorSpacing.large,
        ))
    # if explicit_spacing:
    #     card.add_item(blank_lines(1))
    # card.add_item(discord.ui.Separator(
    #     spacing=discord.SeparatorSpacing.small if explicit_spacing else discord.SeparatorSpacing.large,
    # ))
    next_refresh = now + REFRESH_SECONDS
    card.add_item(discord.ui.TextDisplay(f"{UPDATE_PREFIX}<t:{next_refresh}:R>"))
    view.add_item(card)
    return view


def banner_media(message: discord.Message, path: Path = BANNER_PATH) -> discord.UnfurledMediaItem | None:
    if len(message.components) != 2 or message.components[1].type.value != 17:
        return None
    children = message.components[1].children
    for gallery in children:
        if gallery.type.value != 12:
            continue
        for item in gallery.items:
            media = item.media
            filename = media.url.split("?", 1)[0].rsplit("/", 1)[-1]
            # Referenced files can be present only in gallery metadata.
            if filename == path.name and (
                media.attachment_id is not None
                or media.url.startswith(("https://cdn.discordapp.com/attachments/", "https://media.discordapp.net/attachments/"))
                or any(attachment.filename == filename for attachment in message.attachments)
            ):
                return media
    return None


def is_panel_message(message: discord.Message, bot_id: int) -> bool:
    if message.author.id != bot_id:
        return False
    components = [item.to_dict() for item in message.components]
    if len(components) == 2 and components[0].get("content") == HEADING:
        card = components[1]
        if card.get("type") == 17 and card.get("id") == PANEL_CONTAINER_ID:
            footer = card.get("components", [])[-1:]
            text = footer[0].get("content", "").split("\n")[0] if footer else ""
            return re.fullmatch(rf"{re.escape(UPDATE_PREFIX)}<t:\d+:R>", text) is not None
    # Adopt legacy embeds during upgrades instead of posting another copy.
    if message.content == HEADING and len(message.embeds) == 1:
        embed = message.embeds[0]
        return embed.footer.text == LEGACY_FOOTER or bool(
            embed.fields
            and re.fullmatch(rf"{re.escape(UPDATE_PREFIX)}<t:\d+:R>", embed.fields[-1].value)
        )
    return False


def panel_matches(message: discord.Message, view: discord.ui.LayoutView) -> bool:
    def comparable(value):
        if isinstance(value, dict):
            # Discord assigns IDs to components that did not supply one.
            return {key: comparable(item) for key, item in value.items() if key != "id"}
        if isinstance(value, list):
            return [comparable(item) for item in value]
        return value

    return (
        not message.content
        and not message.embeds
        and comparable([item.to_dict() for item in message.components]) == comparable(view.to_components())
    )
