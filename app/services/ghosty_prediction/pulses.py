"""Smoothed label transitions, ranked by the resulting individual probabilities."""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import datetime
import math
from typing import Any

from app.services.ghosty_forecast_policy import (
    BAND_NAMES, HIT_TOLERANCE, rank_number_candidates, select_candidate, streak_band,
)

from .features import Context, utc
from .sequences import SequenceHistory


PULSE_PRIOR = 8.0
PULSE_MIN_MATCHES = 3


def label_pulse_candidates(
    candidates: list[dict[str, Any]], history: SequenceHistory, *,
    channel: int, context: Context, at: datetime,
) -> list[dict[str, Any]]:
    """Back off from specific suffix/reset matches to the historical distribution.

    Learn the next label and then the number conditional on that label. Rank
    the resulting joint probabilities across every label, retaining support
    for short outcomes. Shared and shorter contexts regularize sparse matches.
    """
    total = sum(item["probability"] for item in candidates)
    if not candidates or total <= 0:
        return candidates
    if not context.recent_streaks and context.previous_streak is not None:
        context = replace(context, recent_streaks=(context.previous_streak + 1,))
    prior = {item["attempt"]: item["probability"] / total for item in candidates}
    bands = {attempt: streak_band(attempt) for attempt in prior}
    seen, detail = set(), None
    # _keys is most-specific first; apply increasingly specific posteriors.
    for key in reversed(list(history._keys(channel, context, utc(at)))):
        _, hits = history.tables.get(key, (Counter(), Counter()))
        counts = {attempt: hits[attempt] for attempt in prior if hits[attempt]}
        support = sum(counts.values())
        signature = tuple(sorted(counts.items()))
        if support < PULSE_MIN_MATCHES or signature in seen:
            continue
        seen.add(signature)
        masses = [sum(p for attempt, p in prior.items() if bands[attempt] == band)
                  for band in range(len(BAND_NAMES))]
        observations = [sum(n for attempt, n in counts.items() if bands[attempt] == band)
                        for band in range(len(BAND_NAMES))]
        updated = {}
        for attempt, probability in prior.items():
            band = bands[attempt]
            label = (observations[band] + PULSE_PRIOR * masses[band]) / (support + PULSE_PRIOR)
            number = (counts.get(attempt, 0) + PULSE_PRIOR * probability / masses[band]) / (
                observations[band] + PULSE_PRIOR
            )
            updated[attempt] = label * number
        prior = updated
        detail = {"previous_bands": [BAND_NAMES[index] for index in key[1]],
                  "depth": len(key[1]), "completed_matches": support,
                  "scope": "channel" if key[0] else "shared", "reset_conditioned": bool(key[2])}
    if detail is None:
        return candidates
    return rank_number_candidates([
        {**item, "probability": prior[item["attempt"]] * total, "label_pulse": detail}
        for item in candidates
    ])


def _distribution(candidates):
    total = sum(item["probability"] for item in candidates)
    points = {item["attempt"]: item["probability"] / total for item in candidates}
    labels = [sum(p for attempt, p in points.items() if streak_band(attempt) == band)
              for band in range(len(BAND_NAMES))]
    selected, _ = select_candidate(rank_number_candidates(candidates), 0)
    return selected["attempt"], points, labels


def validate_pulses(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Score issued run-start forecasts before learning that run's outcomes."""
    from .constants import MIN_COMPLETED_STREAKS
    from .recency import RecentHistory

    baseline, history = RecentHistory(decay=False), SequenceHistory()
    contexts, completed, pending = {}, Counter(), {}
    folds = [{"samples": 0, **{f"{kind}_{metric}": 0 for kind in ("model", "baseline")
              for metric in ("successes", "exact", "band_successes", "log_loss", "band_brier")}}
             for _ in range(2)]
    for index, row in enumerate(rows):
        channel, hit = int(row["channel_id"]), row["outcome"] == "hit"
        stamp = utc(row["created_at"])
        context = contexts.setdefault(channel, Context())
        context.roll_day(stamp)
        if (context.saw_hit and context.streak == 0 and completed[channel] >= MIN_COMPLETED_STREAKS
                and index >= int(.6 * len(rows))):
            old = baseline.candidates(channel, 0)
            new = label_pulse_candidates(old, history, channel=channel, context=context, at=stamp)
            if old and new:
                pending[channel] = (_distribution(old), _distribution(new), int(index >= int(.8 * len(rows))))
        if hit and channel in pending:
            old, new, fold_index = pending.pop(channel)
            actual, fold = context.streak + 1, folds[fold_index]
            actual_band = streak_band(actual)
            fold["samples"] += 1
            for kind, (target, points, labels) in (("baseline", old), ("model", new)):
                fold[f"{kind}_successes"] += abs(actual - target) <= HIT_TOLERANCE
                fold[f"{kind}_exact"] += actual == target
                fold[f"{kind}_band_successes"] += actual_band == streak_band(target)
                fold[f"{kind}_log_loss"] -= math.log(max(1e-12, points.get(actual, 0)))
                fold[f"{kind}_band_brier"] += sum((p - int(band == actual_band)) ** 2
                                                for band, p in enumerate(labels))
        if context.saw_hit:
            history.observe(channel, context, at=stamp, hit=hit)
            if hit:
                completed[channel] += 1
        baseline.observe(channel, context, hit=hit)
        context.observe(hit, at=stamp)
    samples = sum(fold["samples"] for fold in folds)
    totals = {f"{kind}_{metric}": sum(fold[f"{kind}_{metric}"] for fold in folds)
              for kind in ("model", "baseline")
              for metric in ("successes", "exact", "band_successes", "log_loss", "band_brier")}
    accepted = (
        samples >= 20 and all(fold["samples"] >= 5 for fold in folds)
        and all(fold["model_log_loss"] <= fold["baseline_log_loss"] for fold in folds)
        and totals["model_log_loss"] < .99 * totals["baseline_log_loss"]
        and all(totals[f"model_{metric}"] >= totals[f"baseline_{metric}"]
                for metric in ("successes", "exact", "band_successes"))
        and totals["model_band_brier"] <= totals["baseline_band_brier"]
    )
    return {"accepted": accepted, "samples": samples, "folds": folds,
            "model_success_percent": 100 * totals["model_successes"] / samples if samples else None,
            "baseline_success_percent": 100 * totals["baseline_successes"] / samples if samples else None,
            "model_exact": totals["model_exact"], "baseline_exact": totals["baseline_exact"],
            "model_log_loss": totals["model_log_loss"] / samples if samples else None,
            "baseline_log_loss": totals["baseline_log_loss"] / samples if samples else None,
            "model_band_brier": totals["model_band_brier"] / samples if samples else None,
            "baseline_band_brier": totals["baseline_band_brier"] / samples if samples else None,
            "validation_method": "online_chronological_production_ready"}
