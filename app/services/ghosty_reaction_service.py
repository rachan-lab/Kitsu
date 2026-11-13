"""Instant, outcome-specific Ghosty reactions from a cached local message bank."""

from __future__ import annotations

from functools import lru_cache
import json
import logging
from pathlib import Path
import random
import re
from typing import Any

import discord

from app.services.emoji_services import get_cached_application_emoji
from app.services.ghosty_forecast_policy import BAND_NAMES, streak_band


logger = logging.getLogger(__name__)
REACTIONS_FILE = Path(__file__).resolve().parents[2] / "assets" / "ghosty_reactions.json"
FALLBACK_REACTIONS = {
    "win": ("A net win after {display_name}'s hit! {ghosty_yay}",),
    "loss": ("{display_name} broke the streak; the remaining loss would like a word. {ghosty_pepesad}",),
    "break_even": ("{display_name} ended all that suspense exactly at break-even. {ghosty_fine}",),
}
_TEMPLATE_TOKEN = re.compile(r"\{(display_name|ghosty_[a-z_]+)\}")
EXTREME_STREAK_HINT = "👻 Extremely high streak - better to throw 1s while trying to break it."


def _messages(value: Any) -> tuple[str, ...] | None:
    if isinstance(value, list) and value and all(
        isinstance(message, str) and message.strip() for message in value
    ):
        return tuple(message.strip() for message in value)
    return None


def _band_reactions(data: Any, general: dict[str, Any]) -> dict[str, dict[str, tuple[str, ...]]]:
    data = data if isinstance(data, dict) else {}
    result = {}
    for band in BAND_NAMES:
        outcomes = data.get(band, {})
        outcomes = outcomes if isinstance(outcomes, dict) else {}
        result[band] = {
            outcome: _messages(outcomes.get(outcome)) or tuple(general[outcome])
            for outcome in FALLBACK_REACTIONS
        }
    return result


@lru_cache(maxsize=1)
def load_hit_reactions() -> dict[str, Any]:
    """Read once; an unavailable or invalid file must not block hit delivery."""
    try:
        with REACTIONS_FILE.open(encoding="utf-8") as source:
            data = json.load(source)
        if not isinstance(data, dict):
            raise ValueError("Reaction file must contain an object")
        reactions = {}
        for outcome in FALLBACK_REACTIONS:
            messages = _messages(data.get(outcome))
            if messages is None:
                raise ValueError(f"Missing or invalid {outcome} reactions")
            reactions[outcome] = messages
        reactions["bands"] = _band_reactions(data.get("bands"), reactions)
        return reactions
    except (OSError, ValueError):
        logger.exception("Could not load Ghosty reaction file; using local defaults.")
        return {**FALLBACK_REACTIONS, "bands": _band_reactions(None, FALLBACK_REACTIONS)}


def get_hit_reaction(
    *, net_result: int, streak_length: int | None = None, display_name: str = "A player",
) -> str:
    outcome = "win" if net_result > 0 else "loss" if net_result < 0 else "break_even"
    bank = load_hit_reactions()
    messages = bank[outcome] if streak_length is None else bank["bands"][BAND_NAMES[streak_band(streak_length)]][outcome]
    template = random.choice(messages)
    name = " ".join(display_name.split()) or "A player"
    name = discord.utils.escape_markdown(discord.utils.escape_mentions(name))
    # Replace once so a display name containing template syntax stays literal.
    return _TEMPLATE_TOKEN.sub(
        lambda match: name if match[1] == "display_name" else get_cached_application_emoji(match[1]),
        template,
    )


def get_streak_hint(streak_length: int) -> str:
    return EXTREME_STREAK_HINT if streak_band(streak_length) == len(BAND_NAMES) - 1 else ""
