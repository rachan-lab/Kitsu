from __future__ import annotations

from typing import Any

from app.services.ghosty_forecast_policy import (
    ANCHOR_POLICY, BANDED_POLICIES, HIT_TOLERANCE, POINT_POLICIES, PREDICTION_POLICY, PROGRESSIVE_POLICY,
    VARIATION_POLICY, band_interval, rank_number_candidates, rank_progressive_candidates,
    rank_varied_candidates, select_candidate, window_passed,
)
from app.services.ghosty_prediction.windows import occurrence_stats
from app.services.ghosty_anchor_policy import conditional_candidates, rank_anchor_candidates


class GhostyForecastDisplay:
    @staticmethod
    def _valid_streak_forecast(value: Any, base: int) -> bool:
        return (
            isinstance(value, dict)
            and value.get("version") == 3
            and value.get("streak_base") == base
            and isinstance(value.get("candidates"), list)
            and bool(value["candidates"])
            and (value.get("prediction_policy") not in BANDED_POLICIES or all(
                candidate.get("prediction_policy") == value["prediction_policy"]
                for candidate in value["candidates"] if isinstance(candidate, dict)
            ))
            and (value.get("prediction_policy") not in POINT_POLICIES or (
                type(value.get("initial_pick")) is int and any(
                    isinstance(item, dict) and item.get("attempt") == value["initial_pick"]
                    for item in value["candidates"]
                )
            ))
            and (value.get("prediction_policy") not in (VARIATION_POLICY, ANCHOR_POLICY)
                 or any(not item.get("blocked", False) and not item.get("neighbor_only", False)
                        for item in value["candidates"] if isinstance(item, dict)))
            and all(
                isinstance(candidate, dict)
                and type(candidate.get("attempt")) is int
                and candidate["attempt"] > 0
                and type(candidate.get("probability")) in (int, float)
                and 0 < candidate["probability"] <= 1
                for candidate in value["candidates"]
            )
        )

    @staticmethod
    def _display_streak_forecast(
        prediction: dict[str, Any], forecast: dict[str, Any], streak: int
    ) -> dict[str, Any]:
        candidates = forecast["candidates"]
        if forecast.get("prediction_policy") in POINT_POLICIES:
            rank = {PROGRESSIVE_POLICY: rank_progressive_candidates, VARIATION_POLICY: rank_varied_candidates,
                    ANCHOR_POLICY: rank_anchor_candidates}.get(
                forecast["prediction_policy"], rank_number_candidates)
            candidates = rank(candidates)
        anchor = forecast.get("initial_pick")
        selected, pending = select_candidate(candidates, streak, initial_attempt=anchor)
        occurrence = (
            selected if selected.get("occurrence_percent") is not None
            else occurrence_stats(selected["attempt"], prediction.get("streak_occurrences", {}))
        )
        band_fields = {}
        if forecast.get("prediction_policy") in BANDED_POLICIES:
            initial_target = select_candidate(candidates, 0)[0]["attempt"]
            band_fields = {"predicted_band": selected["band"],
                           "predicted_band_percent": selected["band_probability"] * 100,
                           "forecast_reselected": pending and streak == initial_target + 1
                           and selected["attempt"] != initial_target}
            if forecast["prediction_policy"] in POINT_POLICIES:
                previous, _ = select_candidate(candidates, max(0, streak - 1), initial_attempt=anchor)
                band_fields["forecast_reselected"] = pending and selected["attempt"] != previous["attempt"]
            if pending:
                band_fields["hit_interval"] = band_interval(
                    selected["attempt"], misses=min(streak, selected["attempt"] - 1),
                    maximum=max(item["attempt"] for item in candidates),
                )
        displayed = conditional_candidates(candidates, streak) if forecast.get("prediction_policy") == ANCHOR_POLICY else candidates
        return {
            **prediction,
            "hit_at": selected["attempt"],
            "candidate_percent": selected["probability"] * 100,
            "occurrence_percent": occurrence.get("occurrence_percent"),
            "occurrence_samples": occurrence.get("occurrence_samples", 0),
            "forecast_tolerance": HIT_TOLERANCE,
            "anchor_attempt": selected.get("anchor_attempt"),
            "anchor_end": selected.get("anchor_end"),
            "is_neighbor": selected.get("is_neighbor", False),
            "recent_hit_lengths": prediction.get("recent_hit_lengths", []),
            "actual_hit_cooldown": forecast.get("actual_hit_cooldown", prediction.get("actual_hit_cooldown", [])),
            "hit_interval": forecast.get("hit_interval"),
            **band_fields,
            "streak_forecast": forecast,
            "hit_candidates": [
                {**candidate, "status": "missed" if (
                    candidate["attempt"] <= streak if forecast.get("prediction_policy") in POINT_POLICIES else
                    candidate["attempt"] < streak if forecast.get("prediction_policy") == PREDICTION_POLICY
                    else window_passed(candidate["attempt"], streak)
                ) else "pending"}
                for candidate in displayed
            ],
            "estimate_status": "pending" if pending else "missed",
        }
