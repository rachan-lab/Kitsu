"""Variable-order streak-band histories with reset-aware, censored hazards."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

import numpy as np

from .bands import BAND_NAMES, SEQUENCE_DEPTH, SEQUENCE_MIN_HITS, streak_band
from .features import Context, is_weekend, reset_phase, session_day


class SequenceHistory:
    def __init__(self, max_depth: int = SEQUENCE_DEPTH) -> None:
        self.max_depth = max_depth
        self.tables: dict[tuple, tuple[Counter, Counter]] = {}

    def _keys(self, channel: int, context: Context, at: datetime):
        bands = tuple(streak_band(length) for length in context.recent_streaks)
        phase = int((reset_phase(at) / (2 * np.pi)) * 4) % 4
        crossed = (
            context.run_started_at is not None
            and session_day(context.run_started_at) != session_day(at)
        )
        for depth in range(min(len(bands), self.max_depth), 0, -1):
            suffix = bands[-depth:]
            for timing in ((phase, int(is_weekend(at)), int(crossed)), (phase,), ()):
                for scope in (channel, 0):
                    yield scope, suffix, timing

    def observe(self, channel: int, context: Context, *, at: datetime, hit: bool) -> None:
        # Count every at-risk attempt, including misses in unfinished runs.
        # All keys are formed before the result updates the context.
        attempt = context.streak + 1
        for key in self._keys(channel, context, at):
            risk, hits = self.tables.setdefault(key, (Counter(), Counter()))
            risk[attempt] += 1
            if hit:
                hits[attempt] += 1

    def adjust(
        self, hazards: np.ndarray, positions: np.ndarray, *,
        channel: int, context: Context, at: datetime,
    ) -> tuple[np.ndarray, dict[str, Any] | None]:
        for key in self._keys(channel, context, at):
            risk, hits = self.tables.get(key, (Counter(), Counter()))
            support = sum(hits.values())
            if support < SEQUENCE_MIN_HITS:
                continue
            counts = np.asarray([risk[int(position)] for position in positions], dtype=float)
            successes = np.asarray([hits[int(position)] for position in positions], dtype=float)
            adjusted = (successes + 2 * hazards) / (counts + 2)
            return adjusted, {
                "previous_bands": [BAND_NAMES[index] for index in key[1]],
                "depth": len(key[1]), "completed_matches": support,
                "scope": "channel" if key[0] else "shared",
                "reset_conditioned": bool(key[2]),
            }
        return hazards, None

    def to_state(self) -> list[dict[str, Any]]:
        return [
            {"channel": channel, "bands": list(bands), "timing": list(timing),
             "risk": {str(k): v for k, v in risk.items()},
             "hits": {str(k): v for k, v in hits.items()}}
            for (channel, bands, timing), (risk, hits) in sorted(self.tables.items())
        ]

    @classmethod
    def from_state(cls, rows: list[dict[str, Any]], *, max_depth: int = SEQUENCE_DEPTH) -> SequenceHistory:
        model = cls(max_depth)
        if not isinstance(rows, list):
            raise ValueError("Invalid sequence history")
        for row in rows:
            channel = row["channel"]
            bands, timing = tuple(row["bands"]), tuple(row["timing"])
            risk = Counter({int(k): v for k, v in row["risk"].items()})
            hits = Counter({int(k): v for k, v in row["hits"].items()})
            key = channel, bands, timing
            if (
                type(channel) is not int or channel < 0
                or not 1 <= len(bands) <= max_depth
                or any(type(v) is not int or not 0 <= v < len(BAND_NAMES) for v in bands)
                or len(timing) not in (0, 1, 3)
                or any(type(v) is not int for v in timing)
                or (timing and not 0 <= timing[0] < 4)
                or any(v not in (0, 1) for v in timing[1:])
                or any(k < 1 or type(v) is not int or v < 1 for values in (risk, hits) for k, v in values.items())
                or any(v > risk[k] for k, v in hits.items()) or key in model.tables
            ):
                raise ValueError("Invalid sequence counts or context")
            model.tables[key] = risk, hits
        return model
