"""User-defined hit-attempt bands, shared by learning and presentation."""

from __future__ import annotations

from typing import Any

from app.services.ghosty_forecast_policy import BAND_LIMITS as BAND_LIMITS
from app.services.ghosty_forecast_policy import BAND_NAMES, streak_band

SEQUENCE_DEPTH = 4
GLOBAL_SEQUENCE_DEPTH = 6
SEQUENCE_MIN_HITS = 6


def band_summary(attempt: int) -> dict[str, Any]:
    index = streak_band(attempt)
    return {"name": BAND_NAMES[index], "preferred_wager": 1 if index == len(BAND_NAMES) - 1 else None}
