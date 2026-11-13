"""Shared rules for issuing and scoring hit windows without changing outcomes."""

from __future__ import annotations

from datetime import datetime, timezone
import math
import random
from typing import Any


HIT_TOLERANCE = 2
BAND_NAMES = ("LOW", "MEDIUM", "MEDIUM HIGH", "HIGH", "EXTREMELY HIGH")
BAND_LIMITS = (15, 29, 40, 56)
# Retained to read and score previously issued forecasts accurately.
PREDICTION_POLICY = "band_then_number"
NUMBER_POLICY = "highest_probability"
PROGRESSIVE_POLICY = "earlier_probability_peaks"
VARIATION_POLICY = "highest_probability_with_cooldown"
ANCHOR_POLICY = "highest_probability_anchor_neighbors"
COOLDOWN_RUNS = 2
COOLDOWN_RADIUS = 2
FORECAST_EXCLUSION_SETTINGS = {"cooldown_radius": COOLDOWN_RADIUS, "exclusion_revision": 3,
                               "actual_hit_cooldown_runs": COOLDOWN_RUNS}
POINT_POLICIES = (NUMBER_POLICY, PROGRESSIVE_POLICY, VARIATION_POLICY, ANCHOR_POLICY)
BANDED_POLICIES = (PREDICTION_POLICY, *POINT_POLICIES)
PEAK_MIN_RELATIVE_PROBABILITY = 0.5


def streak_band(attempt: int) -> int:
    return sum(attempt >= boundary for boundary in BAND_LIMITS)


def band_interval(attempt: int, *, misses: int, maximum: int) -> tuple[int, int]:
    band = streak_band(attempt)
    lower = (1, *BAND_LIMITS)[band]
    upper = (*[boundary - 1 for boundary in BAND_LIMITS], max(BAND_LIMITS[-1], maximum))[band]
    return max(misses + 1, lower), upper


def classify_candidates(candidates: list[dict[str, Any]], *, exact: bool | None = None) -> list[dict[str, Any]]:
    """Choose the most likely band before ranking numbers within that band."""
    if exact is None:
        exact = bool(candidates) and all(item.get("number_metric") == "exact" for item in candidates)
    masses = [sum(item["probability"] for item in candidates if streak_band(item["attempt"]) == band)
              for band in range(len(BAND_NAMES))]
    result = []
    for item in candidates:
        band = streak_band(item["attempt"])
        window = sum(other["probability"] for other in candidates
                     if streak_band(other["attempt"]) == band
                     and abs(other["attempt"] - item["attempt"]) <= HIT_TOLERANCE)
        result.append({**item, "band": BAND_NAMES[band], "band_probability": min(1.0, masses[band]),
                       "number_metric": "exact" if exact else "window",
                       "window_probability": min(1.0, window), "prediction_policy": PREDICTION_POLICY})
    return sorted(result, key=candidate_rank)


def candidate_rank(candidate: dict[str, Any]) -> tuple[float, float, float, int]:
    if candidate.get("prediction_policy") in POINT_POLICIES:
        # Ignore floating-point noise before using the earlier-number tie break.
        varied = candidate.get("prediction_policy") in (VARIATION_POLICY, ANCHOR_POLICY)
        nearer = candidate.get("prediction_policy") == ANCHOR_POLICY and candidate.get("anchor_tie_break") == "earlier_attempt"
        return (int(varied and candidate.get("blocked", False)), -round(candidate["probability"], 12),
                candidate.get("tie_break", 0) if varied and not nearer else 0, candidate["attempt"])
    probability = (candidate["probability"] if candidate.get("number_metric") == "exact"
                   else candidate.get("window_probability", candidate["probability"]))
    if type(probability) not in (int, float) or not math.isfinite(probability) or not 0 < probability <= 1:
        probability = candidate["probability"]
    band_mass = candidate.get("band_probability", 0) if candidate.get("prediction_policy") == PREDICTION_POLICY else 0
    return -band_mass, -probability, 0, candidate["attempt"]


def rank_number_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep bands as context; rank every observed number by its own probability."""
    annotated = classify_candidates(candidates, exact=True)
    return sorted([{**item, "prediction_policy": NUMBER_POLICY} for item in annotated], key=candidate_rank)


def rank_progressive_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**item, "prediction_policy": PROGRESSIVE_POLICY} for item in rank_number_candidates(candidates)]


def rank_varied_candidates(candidates, *, excluded=None):
    """Preserve probability mass; randomize exact ties once, then freeze the order."""
    result = []
    for item in rank_number_candidates(candidates):
        result.append({**item, "prediction_policy": VARIATION_POLICY,
                       "blocked": item["attempt"] in excluded if excluded is not None else item.get("blocked", False),
                       "selection_radius": COOLDOWN_RADIUS if excluded is not None else item.get("selection_radius", 0),
                       "tie_break": item["tie_break"] if "tie_break" in item else random.random()})
    return sorted(result, key=candidate_rank)


def forecast_targets(forecast, misses):
    """Replay issued targets through the last miss, including a still-pending target."""
    if not isinstance(forecast, dict) or not forecast.get("candidates"):
        return []
    candidates = forecast["candidates"]
    if (any(not isinstance(item, dict) or type(item.get("attempt")) is not int or item["attempt"] < 1
            or type(item.get("probability")) not in (int, float) or not 0 < item["probability"] <= 1
            for item in candidates) or all(item.get("blocked", False) for item in candidates)):
        return []
    first = max(0, int(forecast.get("candidates_issued_after", forecast.get("issued_after", 0))))
    targets = {forecast["hit_at"]} if type(forecast.get("hit_at")) is int else set()
    targets.update(n for n in forecast.get("previous_targets", []) if type(n) is int and n > 0)
    for count in [first, *sorted({item["attempt"] + offset for item in candidates
                                 for offset in (0, 1, HIT_TOLERANCE) if first <= item["attempt"] + offset <= misses})]:
        selected, _ = select_candidate(candidates, count, initial_attempt=forecast.get("initial_pick"))
        targets.add(selected["attempt"])
    return sorted(target for target in targets if target > 0)


def next_forecast_cooldown(stats, *, misses):
    """Age by completed channel runs, including runs without an issued forecast."""
    history = stats.get("forecast_cooldown", [])
    history = [list(item) for item in history[-COOLDOWN_RUNS:] if isinstance(item, list)] if isinstance(history, list) else []
    return [*history, forecast_targets(stats.get("streak_forecast"), misses)][-COOLDOWN_RUNS:]


def recent_actual_hits(stats, *, recent=(), last_hit=None, last_hit_misses=None):
    """Use saved actual attempts first; model context can bootstrap old stats."""
    saved = stats.get("actual_hit_cooldown")
    if isinstance(saved, list):
        return [value for value in saved if type(value) is int and value > 0][-COOLDOWN_RUNS:]
    latest = next((value for value in (
        stats.get("last_hit_length"),
        stats["last_hit_misses"] + 1 if type(stats.get("last_hit_misses")) is int and stats["last_hit_misses"] >= 0 else None,
        last_hit, last_hit_misses + 1 if type(last_hit_misses) is int and last_hit_misses >= 0 else None,
    ) if type(value) is int and value > 0), None)
    recent = [value for value in recent if type(value) is int and value > 0][-COOLDOWN_RUNS:]
    if latest is not None and (not recent or recent[-1] != latest):
        return [latest]
    return recent or ([latest] if latest is not None else [])


def forecast_exclusions(stats, *, last_hit=None, last_hit_misses=None, recent_hit_lengths=()):
    """Exclude two runs of displayed predictions and two actual hit attempts."""
    history = stats.get("forecast_cooldown", [])
    targets = {n for run in history[-COOLDOWN_RUNS:] if isinstance(run, list)
               for n in run if type(n) is int and n > 0} if isinstance(history, list) else set()
    excluded = {neighbor for n in targets for neighbor in range(max(1, n - COOLDOWN_RADIUS), n + COOLDOWN_RADIUS + 1)}
    for actual in recent_actual_hits(stats, recent=recent_hit_lengths, last_hit=last_hit, last_hit_misses=last_hit_misses):
        excluded.update(range(max(1, actual - COOLDOWN_RADIUS), actual + COOLDOWN_RADIUS + 1))
    return excluded


def _next_peak(candidates: list[dict[str, Any]], cutoff: int) -> dict[str, Any] | None:
    remaining = [item for item in candidates if item["attempt"] > cutoff]
    if not remaining:
        return None
    best = min(remaining, key=candidate_rank)
    points = {item["attempt"]: round(item["probability"], 12) for item in candidates}
    minimum = round(best["probability"], 12) * PEAK_MIN_RELATIVE_PROBABILITY
    peaks = [item for item in remaining if points[item["attempt"]] >= minimum
             and points[item["attempt"]] > points.get(item["attempt"] - 1, 0)
             and points[item["attempt"]] >= points.get(item["attempt"] + 1, 0)]
    return min(peaks, key=lambda item: item["attempt"]) if peaks else best


def _select_progressive(candidates, misses, initial_attempt):
    selected = next((item for item in candidates if item["attempt"] == initial_attempt), None) or _next_peak(candidates, 0)
    while misses >= selected["attempt"]:
        cutoff = selected["attempt"]
        next_pick = _next_peak(candidates, cutoff)
        if next_pick is None:
            return max(candidates, key=lambda item: item["attempt"]), False
        survival = max(1e-12, 1 - sum(item["probability"] for item in candidates if item["attempt"] <= cutoff))
        conditioned = rank_progressive_candidates([
            {**item, "probability": min(1.0, item["probability"] / survival)}
            for item in candidates if item["attempt"] > cutoff
        ])
        selected = next(item for item in conditioned if item["attempt"] == next_pick["attempt"])
    return selected, True


def window_passed(attempt: int, misses: int) -> bool:
    return misses >= attempt + HIT_TOLERANCE


def select_candidate(
    candidates: list[dict[str, Any]], misses: int, *, initial_attempt: int | None = None,
) -> tuple[dict[str, Any], bool]:
    if all(item.get("prediction_policy") == ANCHOR_POLICY for item in candidates):
        from app.services.ghosty_anchor_policy import select_anchor_candidate
        return select_anchor_candidate(candidates, misses, initial_attempt=initial_attempt)
    if all(item.get("prediction_policy") == PROGRESSIVE_POLICY for item in candidates):
        return _select_progressive(candidates, misses, initial_attempt)
    if all(item.get("prediction_policy") in (NUMBER_POLICY, VARIATION_POLICY) for item in candidates):
        eligible = [item for item in candidates if not item.get("blocked", False)]
        selected = next((item for item in eligible if item["attempt"] == initial_attempt),
                        min(eligible, key=candidate_rank))
        while misses >= selected["attempt"]:
            cutoff = selected["attempt"]
            radius = selected.get("selection_radius", 0) if selected.get("prediction_policy") == VARIATION_POLICY else 0
            remaining = [item for item in candidates if item["attempt"] > cutoff]
            if not any(not item.get("blocked", False) and item["attempt"] > cutoff + radius for item in remaining):
                return selected, False
            survival = max(1e-12, 1 - sum(item["probability"] for item in candidates if item["attempt"] <= cutoff))
            remaining = [{**item, "probability": min(1.0, item["probability"] / survival)} for item in remaining]
            rank = rank_varied_candidates if selected.get("prediction_policy") == VARIATION_POLICY else rank_number_candidates
            selected = next(item for item in rank(remaining)
                            if not item.get("blocked", False) and item["attempt"] > cutoff + radius)
        return selected, True
    if all(item.get("prediction_policy") == PREDICTION_POLICY for item in candidates):
        selected = min(candidates, key=candidate_rank)
        # Replay the same decisions after a restart or a skipped dashboard
        # refresh. A 9 stays visible through streak 9; another miss at 10
        # permits the next pick. Eliminating a different candidate at 8 must
        # not replace the displayed 9.
        while misses > selected["attempt"]:
            cutoff = selected["attempt"] + 1
            remaining = [item for item in candidates if item["attempt"] > cutoff]
            if not remaining:
                return max(candidates, key=lambda item: item["attempt"]), False
            survival = max(1e-12, 1 - sum(item["probability"] for item in candidates if item["attempt"] <= cutoff))
            remaining = [{**item, "probability": min(1.0, item["probability"] / survival)} for item in remaining]
            selected = classify_candidates(remaining)[0]
        return selected, True
    remaining = [item for item in candidates if not window_passed(item["attempt"], misses)]
    return (
        min(remaining, key=candidate_rank) if remaining else max(candidates, key=lambda item: item["attempt"]),
        bool(remaining),
    )


def score_hit(target: int, actual: int) -> dict[str, Any]:
    error = actual - target
    return {
        "target": target, "actual": actual, "error": error,
        "tolerance": HIT_TOLERANCE, "success": abs(error) <= HIT_TOLERANCE,
        "exact": error == 0,
    }


def clean_evaluation(value: Any) -> dict[str, Any] | None:
    """Recompute labels from exact integers rather than trusting stored booleans."""
    if not isinstance(value, dict):
        return None
    target, actual = value.get("target"), value.get("actual")
    if type(target) is not int or type(actual) is not int or min(target, actual) < 1:
        return None
    result = score_hit(target, actual)
    displayed = value.get("displayed_target")
    if type(displayed) is int and displayed > 0:
        result["displayed_target"] = displayed
    if value.get("prediction_policy") in BANDED_POLICIES:
        result.update(prediction_policy=value["prediction_policy"], predicted_band=BAND_NAMES[streak_band(target)],
                      actual_band=BAND_NAMES[streak_band(actual)], band_success=streak_band(target) == streak_band(actual))
    initial = value.get("initial_target")
    if type(initial) is int and initial > 0:
        result.update(initial_target=initial, initial_success=abs(actual - initial) <= HIT_TOLERANCE)
        if result.get("prediction_policy") in BANDED_POLICIES:
            result["initial_band_success"] = streak_band(initial) == streak_band(actual)
    return result


def evaluate_forecast(
    forecast: Any, *, base: int, misses: int, at: datetime, source: str,
) -> dict[str, Any] | None:
    """Credit a live hit within ±2 of an issued target, including one just replaced."""
    if (
        source != "live" or not isinstance(forecast, dict) or forecast.get("version") != 3
        or forecast.get("streak_base") != base
        or type(forecast.get("issued_after")) is not int
        or not 0 <= forecast["issued_after"] <= misses
        or not isinstance(forecast.get("issued_at"), datetime)
    ):
        return None
    def utc(stamp: datetime) -> datetime:
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)

    if utc(forecast["issued_at"]) >= utc(at):
        return None
    revised = forecast.get("candidates_issued_at")
    if revised is not None and (
        not isinstance(revised, datetime) or utc(revised) >= utc(at)
        or type(forecast.get("candidates_issued_after")) is not int
        or not 0 <= forecast["candidates_issued_after"] <= misses
    ):
        return None
    candidates = forecast.get("candidates")
    if not isinstance(candidates, list) or not candidates or any(
        not isinstance(item, dict) or type(item.get("attempt")) is not int or item["attempt"] < 1
        or type(item.get("probability")) not in (int, float) or not 0 < item["probability"] <= 1
        for item in candidates
    ):
        return None
    selected, _ = select_candidate(candidates, misses, initial_attempt=forecast.get("initial_pick"))
    result = score_hit(selected["attempt"], misses + 1)
    if not result["success"]:
        # The UI advances on a target's miss, but its +1/+2 learning window
        # still applies. Replay only targets issued in this run, never hidden
        # candidates or a target from another completed streak.
        nearby = [target for target in forecast_targets(forecast, misses)
                  if abs(misses + 1 - target) <= HIT_TOLERANCE]
        if nearby:
            target = min(nearby, key=lambda value: (abs(misses + 1 - value), -value))
            result = {**score_hit(target, misses + 1), "displayed_target": selected["attempt"]}
    if forecast.get("prediction_policy") in BANDED_POLICIES:
        result["prediction_policy"] = forecast["prediction_policy"]
    initial = forecast.get("hit_at")
    if type(initial) is int and initial > 0:
        result.update(initial_target=initial, initial_success=abs(misses + 1 - initial) <= HIT_TOLERANCE)
    return clean_evaluation(result)
