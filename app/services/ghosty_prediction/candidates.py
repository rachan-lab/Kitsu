from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

import numpy as np

from .constants import STREAK_POOL_STRENGTH
from .features import Context, first_hit_candidates, logit, sigmoid
from .windows import occurrence_stats, rank_windows
from .pulses import label_pulse_candidates
from app.services.ghosty_forecast_policy import rank_number_candidates


def _joint_prior(allowed, local_risk, local_hits, pooled_risk, pooled_hits):
    """Pool complete outcome mass and its surviving tail, not each hazard independently."""
    maximum = max(pooled_hits, default=0)
    positions = np.arange(1, maximum + 1)
    risk = np.asarray([max(0, pooled_risk[int(k)] - local_risk[int(k)]) for k in positions], dtype=float)
    hits = np.asarray([max(0, pooled_hits[int(k)] - local_hits[int(k)]) for k in positions], dtype=float)
    hazards = hits / (risk + .5)
    survival = np.concatenate(([1.0], np.cumprod(1 - hazards)))
    masses = survival[:-1] * hazards
    # Excluded outcomes cannot absorb probability from this channel. Retain
    # the censored tail instead of forcing the last observed number to hit.
    masses = np.where([int(k) in allowed for k in positions], masses, 0)
    tail = survival[-1]
    total = float(masses.sum() + tail)
    if not masses.sum():
        return {}, {}, 0.0
    masses = masses / total
    surviving = tail / total + np.cumsum(masses[::-1])[::-1]
    return (dict(zip(map(int, positions), masses)), dict(zip(map(int, positions), surviving)), STREAK_POOL_STRENGTH)


def empirical_hazards(channel_id, streak, risks, hits, pooled_risk, pooled_hits):
    channel_hits = hits.get(channel_id, Counter())
    allowed = set(channel_hits or pooled_hits)
    future = [attempt for attempt in allowed if attempt > streak]
    positions = np.arange(streak + 1, max(future) + 1) if future else np.asarray([], dtype=int)
    channel_risk = risks.get(channel_id, Counter())
    if not len(positions):
        return np.asarray([], dtype=float), positions, allowed, channel_hits
    local_risk = np.asarray([channel_risk[int(attempt)] for attempt in positions], dtype=float)
    local_hits = np.asarray([channel_hits[int(attempt)] for attempt in positions], dtype=float)
    masses, surviving, strength = _joint_prior(allowed, channel_risk, channel_hits, pooled_risk, pooled_hits)
    prior_hits = np.asarray([masses.get(int(k), 0) for k in positions])
    prior_risk = np.asarray([surviving.get(int(k), 0) for k in positions])
    hazards = (local_hits + strength * prior_hits) / (local_risk + strength * prior_risk + .5)
    return hazards, positions, allowed, channel_hits


class OutcomeCandidates:
    def _streak_outcome_candidates(
        self, channel_id: int, streak: int, context_hazards: np.ndarray, prior: float,
        *, context: Context, now: datetime, global_recent: tuple[int, ...],
    ) -> list[dict[str, Any]]:
        if self.recency_active:
            candidates = self.recency_history.candidates(channel_id, streak)
            for candidate in candidates:
                candidate.update(occurrence_stats(
                    candidate["attempt"], self.streak_hits.get(channel_id) or self._pooled_streak_hits,
                ))
            return candidates
        hazards, positions, allowed, channel_hits = empirical_hazards(
            channel_id, streak, self.streak_risk, self.streak_hits,
            self._pooled_streak_risk, self._pooled_streak_hits,
        )
        if not len(positions):
            return []
        # Positions excluded from this channel's candidates cannot consume
        # survival probability before the allowed outcomes are classified.
        supported = np.asarray([int(attempt) in allowed for attempt in positions])
        if self.pulse_active and context.recent_streaks:
            candidates = first_hit_candidates(
                np.where(supported, hazards, 0.0), current_streak=streak,
                allowed_attempts=allowed, limit=len(allowed),
            )
            baseline = rank_number_candidates(rank_windows(candidates, channel_hits or self._pooled_streak_hits, limit=None))
            adjusted = label_pulse_candidates(baseline, self.sequence_history, channel=channel_id, context=context, at=now)
            if any(item.get("label_pulse") for item in adjusted):
                return adjusted
        sequence_match = None
        if self.global_sequence_active:
            from dataclasses import replace
            hazards, sequence_match = self.global_sequence_history.adjust(
                hazards, positions, channel=channel_id,
                context=replace(context, recent_streaks=global_recent), at=now,
            )
        if self.sequence_active and sequence_match is None:
            hazards, sequence_match = self.sequence_history.adjust(
                hazards, positions, channel=channel_id, context=context, at=now,
            )
        # Select validated effects without stacking them. A missing or sparse
        # multi-run context can still use the validated previous-run features.
        if self.active and sequence_match is None:
            indices = np.minimum(np.arange(len(positions)), len(context_hazards) - 1)
            adjustment = logit(context_hazards[indices]) - logit(np.asarray(prior))
            hazards = sigmoid(logit(hazards) + adjustment)
        # Empty positions must not accumulate invented probability against the
        # long tail. Keep every observed outcome for frozen run forecasts.
        hazards = np.where(supported, hazards, 0.0)
        candidates = first_hit_candidates(
            hazards, current_streak=streak, allowed_attempts=allowed, limit=len(allowed),
        )
        return rank_number_candidates(rank_windows(candidates, channel_hits or self._pooled_streak_hits, limit=None))
