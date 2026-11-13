"""Frozen anchor groups and a normalized local curve for nearby hit numbers."""
from __future__ import annotations

from bisect import bisect_left
import math
from statistics import median

from app.services.ghosty_forecast_policy import (
    ANCHOR_POLICY, BAND_NAMES, candidate_rank, rank_varied_candidates, streak_band,
)


ANCHOR_RADIUS = 2
CURVE_VERSION = 1


def _nearby_estimate(attempt, points):
    positions = sorted(points)
    index = bisect_left(positions, attempt)
    if 0 < index < len(positions):
        left, right = positions[index - 1], positions[index]
        return points[left] + (points[right] - points[left]) * (attempt - left) / (right - left)
    # Beyond the observed curve, use a short decaying extension. The scale
    # follows observed spacing and is bounded by the three-number group.
    spacing = median(b - a for a, b in zip(positions, positions[1:])) if len(positions) > 1 else ANCHOR_RADIUS + 1
    scale = max(1.0, min(ANCHOR_RADIUS + 1, spacing))
    edge = positions[-1]
    return points[edge] * math.exp(-(attempt - edge) / scale)


def rank_anchor_candidates(candidates, *, excluded=None):
    """Fill missing +1/+2 values once; normalize the full curve, including its tail.

    Observed lengths remain the anchor pool. Added curve estimates can only
    be displayed as retries belonging to an observed anchor, never as a new
    anchor that extends the group indefinitely. No training hits are added.
    """
    if not candidates:
        return []
    if all(item.get("curve_version") == CURVE_VERSION for item in candidates):
        result = rank_varied_candidates(candidates, excluded=excluded)
    else:
        original = {item["attempt"]: dict(item) for item in candidates}
        points = {attempt: item["probability"] for attempt, item in original.items()}
        missing = {attempt + step for attempt in points for step in (1, 2)} - points.keys()
        estimates = {attempt: _nearby_estimate(attempt, points) for attempt in missing}
        normalizer = max(1.0, sum(points.values())) + sum(estimates.values())
        expanded = [
            {**item, "probability": item["probability"] / normalizer, "neighbor_only": False,
             "anchor_tie_break": "earlier_attempt"}
            for item in original.values()
        ] + [
            {"attempt": attempt, "probability": probability / normalizer, "neighbor_only": True,
             "anchor_tie_break": "earlier_attempt"}
            for attempt, probability in estimates.items() if probability > 0
        ]
        result = rank_varied_candidates(expanded, excluded=excluded)
    return sorted([
        {**item, "prediction_policy": ANCHOR_POLICY, "curve_version": CURVE_VERSION}
        for item in result
    ], key=candidate_rank)


def conditional_candidates(candidates, misses):
    survival = max(1e-12, 1 - sum(item["probability"] for item in candidates if item["attempt"] <= misses))
    result = []
    for item in candidates:
        probability = min(1.0, item["probability"] / survival) if item["attempt"] > misses else 0.0
        result.append({**item, "probability": probability})
    masses = {band: sum(item["probability"] for item in result if streak_band(item["attempt"]) == band)
              for band in range(len(BAND_NAMES))}
    return [{**item, "band_probability": min(1.0, masses[streak_band(item["attempt"])])} for item in result]


def select_anchor_candidate(candidates, misses, *, initial_attempt=None):
    anchors = [item for item in candidates if not item.get("blocked", False) and not item.get("neighbor_only", False)]
    anchor = next((item for item in anchors if item["attempt"] == initial_attempt), min(anchors, key=candidate_rank))
    while True:
        start, end = anchor["attempt"], anchor["attempt"] + ANCHOR_RADIUS
        group = sorted((item for item in candidates if start <= item["attempt"] <= end
                        and not item.get("blocked", False)), key=lambda item: item["attempt"])
        selected = next((item for item in group if item["attempt"] > misses), None)
        if selected is not None:
            pending = True
            break
        remaining = [item for item in anchors if item["attempt"] > end]
        if not remaining:
            selected, pending = group[-1], False
            break
        anchor = min(remaining, key=candidate_rank)
    conditioned = conditional_candidates(candidates, misses)
    selected = next(item for item in conditioned if item["attempt"] == selected["attempt"])
    return {**selected, "anchor_attempt": start, "anchor_end": end,
            "is_neighbor": selected["attempt"] != start}, pending
