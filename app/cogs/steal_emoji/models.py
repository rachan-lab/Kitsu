from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


@dataclass(slots=True)
class StealItem:
    item_type: Literal["emoji", "sticker"]
    name: str
    item_id: int
    url: str
    animated: bool
    source_format: str
    description: str = ""
    emoji: str = "🍥"

    @property
    def download_url(self) -> str:
        """Download native emoji dimensions; keep enlarged URLs for previews."""
        parts = urlsplit(self.url)
        if parts.hostname in {"cdn.discordapp.com", "media.discordapp.net"} and parts.path.startswith("/emojis/"):
            query = [(key, value) for key, value in parse_qsl(parts.query)
                     if key.lower() not in {"size", "width", "height"}]
            return urlunsplit(parts._replace(query=urlencode(query)))
        return self.url

    @property
    def export_options(self) -> list[str]:
        if self.source_format == "lottie":
            return ["JSON", "GIF", "APNG", "PNG"]

        if self.animated:
            return ["GIF", "APNG"]

        if self.source_format == "unknown":
            return ["PNG"]

        return ["PNG", "GIF", "WEBP", "JPEG"]
