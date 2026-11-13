"""Export formats and GIF sizes share one dropdown."""

from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING

import discord

from .constants import GIF_EXPORT_PRESETS, GIF_EXPORT_SIZE, MAX_GIF_EXPORT_SIZE, logger
from .export import export_item
from .models import StealItem

if TYPE_CHECKING:
    from .view import StealEmojiView


def export_options(item: StealItem) -> list[discord.SelectOption]:
    options = []
    for fmt in item.export_options:
        if fmt != "GIF":
            options.append(discord.SelectOption(label=fmt, value=fmt))
            continue
        options.extend(discord.SelectOption(
            label=f"GIF〃{size}px", value=f"GIF:{size}",
        ) for size in GIF_EXPORT_PRESETS)
        options.append(discord.SelectOption(
            label="GIF〃Custom", value="GIF:custom", description=f"Choose a maximum side, up to {MAX_GIF_EXPORT_SIZE}px",
        ))
    return options


async def can_export(view: StealEmojiView, interaction: discord.Interaction) -> bool:
    if view.inactive:
        text = "This steal menu is now inactive. Run the command again."
    elif interaction.user.id != view.owner_id:
        text = "Only the person who ran this command can export this asset."
    else:
        return True
    await interaction.response.send_message(text, ephemeral=True)
    return False


async def send_export(
    interaction: discord.Interaction, item: StealItem, fmt: str, *, max_size: int = GIF_EXPORT_SIZE,
) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        data, extension = await export_item(item, fmt, max_size=max_size)
        safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", item.name)[:50] or "discord_asset"
        file = discord.File(io.BytesIO(data), filename=f"{safe_name}.{extension}")
        try:
            text = f"Exported **{discord.utils.escape_markdown(item.name)}** as **{fmt}**."
            await interaction.followup.send(content=text, file=file, ephemeral=True)
        finally:
            file.close()
    except ValueError as error:
        await interaction.followup.send(str(error), ephemeral=True)
    except Exception:
        logger.exception("[STEAL] Export failed | item_id=%s | format=%s", item.item_id, fmt)
        await interaction.followup.send("Export failed. Please try again.", ephemeral=True)


class CustomGifSizeModal(discord.ui.Modal):
    def __init__(self, view: StealEmojiView, item: StealItem):
        super().__init__(title="GIF size", timeout=180)
        self.parent_view, self.item = view, item
        self.size = discord.ui.TextInput(default=str(GIF_EXPORT_SIZE), min_length=1, max_length=3)
        self.add_item(discord.ui.Label(
            text="Maximum side (px)", description=f"1-{MAX_GIF_EXPORT_SIZE}. Proportions stay unchanged.", component=self.size,
        ))

    async def on_submit(self, interaction: discord.Interaction):
        if not await can_export(self.parent_view, interaction):
            return
        try:
            size = int(self.size.value.strip())
            if not 1 <= size <= MAX_GIF_EXPORT_SIZE:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                f"Enter a whole number from 1 to {MAX_GIF_EXPORT_SIZE}.", ephemeral=True,
            )
            return
        await send_export(interaction, self.item, "GIF", max_size=size)


class StealExportSelect(discord.ui.Select):
    def __init__(self, parent_view: StealEmojiView):
        super().__init__(placeholder="Export as", options=export_options(parent_view.items[parent_view.page]))
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        view = self.parent_view
        if not await can_export(view, interaction):
            return
        choice = self.values[0]
        if choice not in {option.value for option in self.options}:
            await interaction.response.send_message("Choose one of the export options.", ephemeral=True)
            return
        item = view.items[view.page]
        if choice == "GIF:custom":
            await interaction.response.send_modal(CustomGifSizeModal(view, item))
            return
        fmt, _, size = choice.partition(":")
        await send_export(interaction, item, fmt, max_size=int(size) if size else GIF_EXPORT_SIZE)
