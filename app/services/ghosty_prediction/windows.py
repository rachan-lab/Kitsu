"""Occurrence estimates and forward-only validation of the ±2 hit window."""

from __future__ import annotations

from collections import Counter
from copy import copy
from dataclasses import replace
from typing import Any

from app.services.ghosty_forecast_policy import (
    HIT_TOLERANCE, PROGRESSIVE_POLICY, candidate_rank, rank_progressive_candidates, select_candidate,
)

from .constants import MAX_HIT_CANDIDATES
from .features import Context, utc
from .bands import GLOBAL_SEQUENCE_DEPTH, streak_band


def occurrence_stats(attempt: int, hits: dict[int, int]) -> dict[str, Any]:
    total = sum(hits.values())
    occurrences = sum(count for position, count in hits.items() if abs(position - attempt) <= HIT_TOLERANCE)
    return {
        "occurrence_hits": occurrences, "occurrence_samples": total,
        "occurrence_percent": 100 * occurrences / total if total else None,
    }


def rank_windows(
    candidates: list[dict[str, Any]], hits: Counter, *, limit: int | None = MAX_HIT_CANDIDATES,
) -> list[dict[str, Any]]:
    for candidate in candidates:
        attempt = candidate["attempt"]
        candidate["window_probability"] = min(1.0, sum(
            item["probability"] for item in candidates
            if abs(item["attempt"] - attempt) <= HIT_TOLERANCE
        ))
        candidate.update(occurrence_stats(attempt, hits))
    return sorted(candidates, key=candidate_rank)[:limit]


def accuracy_summary(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(evaluations)
    successes = sum(item["success"] for item in evaluations)
    initial = [item for item in evaluations if "initial_success" in item]
    band_scores = [item for item in evaluations if "band_success" in item]
    initial_bands = [item for item in evaluations if "initial_band_success" in item]
    return {
        "tolerance": HIT_TOLERANCE, "samples": count, "successes": successes,
        "success_percent": 100 * successes / count if count else None,
        "initial_samples": len(initial),
        "initial_successes": sum(item["initial_success"] for item in initial),
        "band_samples": len(band_scores),
        "band_success_percent": 100 * sum(item["band_success"] for item in band_scores) / len(band_scores) if band_scores else None,
        "initial_band_samples": len(initial_bands),
        "initial_band_success_percent": 100 * sum(item["initial_band_success"] for item in initial_bands) / len(initial_bands) if initial_bands else None,
    }


def validate_windows(predictor_type, training, heldout, weights, mask) -> dict[str, Any]:
    """Issue each run's first prediction before observing its held-out outcomes.

    Counts, hit positions and coefficients come exclusively from the prefix.
    Score the initial window for model selection so advancing to another
    candidate cannot turn an earlier failed prediction into a validation win.
    """
    baseline = predictor_type(training, now=utc(training[-1]["created_at"]), enabled=False)
    pattern = copy(baseline)
    pattern.active, pattern.weights, pattern.mask = True, weights, mask
    sequence = copy(baseline)
    sequence.sequence_active = True
    global_sequence = copy(baseline)
    global_sequence.global_sequence_active = True
    recent = baseline.global_recent_streaks
    contexts = {channel: replace(context) for channel, context in baseline.contexts.items()}
    pending: dict[int, tuple] = {}
    scores = {"samples": 0, "model_successes": 0, "baseline_successes": 0, "tolerance": HIT_TOLERANCE}
    scores.update(sequence_successes=0, sequence_wins=0, sequence_losses=0)
    scores.update(global_successes=0, global_wins=0, global_losses=0)
    scores.update({f"{kind}_band_successes": 0 for kind in ("model", "baseline", "sequence", "global")})
    for row in heldout:
        channel = int(row["channel_id"])
        context = contexts.setdefault(channel, Context())
        stamp = utc(row["created_at"])
        context.roll_day(stamp)
        if context.saw_hit and context.streak == 0:
            targets = []
            for model in (pattern, baseline, sequence, global_sequence):
                prediction = model.predict(
                    channel, now=stamp, current_streak=0, minimum_samples=1,
                    context_override=context, global_recent_override=recent,
                )
                candidates = prediction.get("forecast_candidates", [])
                if prediction.get("forecast_policy") == PROGRESSIVE_POLICY:
                    candidates = rank_progressive_candidates(candidates)
                targets.append(select_candidate(candidates, 0)[0]["attempt"] if candidates else None)
            if all(target is not None for target in targets):
                pending[channel] = tuple(targets)
        hit = row["outcome"] == "hit"
        if hit and channel in pending:
            targets = pending.pop(channel)
            model_target, base_target, sequence_target, global_target = targets
            actual = context.streak + 1
            scores["samples"] += 1
            scores["model_successes"] += abs(actual - model_target) <= HIT_TOLERANCE
            scores["baseline_successes"] += abs(actual - base_target) <= HIT_TOLERANCE
            sequence_success = abs(actual - sequence_target) <= HIT_TOLERANCE
            baseline_success = abs(actual - base_target) <= HIT_TOLERANCE
            scores["sequence_successes"] += sequence_success
            scores["sequence_wins"] += sequence_success and not baseline_success
            scores["sequence_losses"] += baseline_success and not sequence_success
            global_success = abs(actual - global_target) <= HIT_TOLERANCE
            scores["global_successes"] += global_success
            scores["global_wins"] += global_success and not baseline_success
            scores["global_losses"] += baseline_success and not global_success
            for kind, target in zip(("model", "baseline", "sequence", "global"), targets):
                scores[f"{kind}_band_successes"] += streak_band(actual) == streak_band(target)
        if hit and context.saw_hit:
            recent = (*recent, context.streak + 1)[-GLOBAL_SEQUENCE_DEPTH:]
        context.observe(hit, at=stamp)
    return scores


def combine_validation(folds: list[dict[str, Any]]) -> dict[str, Any]:
    count = sum(fold["samples"] for fold in folds)
    model_hits = sum(fold["model_successes"] for fold in folds)
    base_hits = sum(fold["baseline_successes"] for fold in folds)
    # Require a useful improvement in both chronological windows, with at
    # least 20 complete held-out runs overall and five in each window.
    accepted = (
        count >= 20 and all(fold["samples"] >= 5 for fold in folds)
        and all(fold["model_successes"] >= fold["baseline_successes"] for fold in folds)
        and (model_hits - base_hits) / count >= 0.05
        and all(fold.get("model_band_successes", 0) >= fold.get("baseline_band_successes", 0) for fold in folds)
    )
    return {
        "tolerance": HIT_TOLERANCE, "samples": count,
        "model_success_percent": 100 * model_hits / count if count else None,
        "baseline_success_percent": 100 * base_hits / count if count else None,
        "model_band_success_percent": 100 * sum(fold.get("model_band_successes", 0) for fold in folds) / count if count else None,
        "baseline_band_success_percent": 100 * sum(fold.get("baseline_band_successes", 0) for fold in folds) / count if count else None,
        "accepted": accepted, "folds": folds,
    }


def combine_sequence_validation(folds: list[dict[str, Any]], kind: str = "sequence") -> dict[str, Any]:
    result = combine_validation([
        {**fold, "model_successes": fold[f"{kind}_successes"],
         "model_band_successes": fold.get(f"{kind}_band_successes", 0)} for fold in folds
    ])
    wins = sum(fold[f"{kind}_wins"] for fold in folds)
    losses = sum(fold[f"{kind}_losses"] for fold in folds)
    # A tiny accidental advantage must not turn an assumed cycle into policy.
    result.update(wins=wins, losses=losses)
    result["accepted"] = result["accepted"] and wins - losses >= 4
    return result
