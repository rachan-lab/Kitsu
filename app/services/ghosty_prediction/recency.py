"""Recent completed runs, evaluated by issuing predictions before each result."""
from collections import Counter
import math

import numpy as np

from app.services.ghosty_forecast_policy import (
    HIT_TOLERANCE, rank_number_candidates, select_candidate, streak_band,
)

from .candidates import empirical_hazards
from .features import Context, first_hit_candidates
from .windows import combine_validation, rank_windows


RECENT_HALF_LIFE = 4


class RecentHistory:
    def __init__(self, *, decay: bool = True):
        self.decay = decay
        self.risk: dict[int, Counter] = {}
        self.hits: dict[int, Counter] = {}

    def observe(self, channel: int, context: Context, *, hit: bool):
        if not context.saw_hit:
            return
        risk, hits = self.risk.setdefault(channel, Counter()), self.hits.setdefault(channel, Counter())
        if self.decay and context.streak == 0:
            for counts in (risk, hits):
                for position, count in list(counts.items()):
                    count *= 2 ** (-1 / RECENT_HALF_LIFE)
                    if count < 1e-12:
                        del counts[position]
                    else:
                        counts[position] = count
        attempt = context.streak + 1
        risk[attempt] += 1
        if hit:
            hits[attempt] += 1

    def candidates(self, channel, streak):
        pooled_risk, pooled_hits = Counter(), Counter()
        for counts in self.risk.values():
            pooled_risk.update(counts)
        for counts in self.hits.values():
            pooled_hits.update(counts)
        hazards, positions, allowed, channel_hits = empirical_hazards(
            channel, streak, self.risk, self.hits, pooled_risk, pooled_hits,
        )
        if not len(positions):
            return []
        hazards = np.where([int(attempt) in allowed for attempt in positions], hazards, 0)
        candidates = first_hit_candidates(hazards, current_streak=streak, allowed_attempts=allowed, limit=len(allowed))
        return rank_number_candidates(rank_windows(candidates, Counter(), limit=None))

    def to_state(self):
        return {name: {str(channel): {str(position): count for position, count in counts.items()}
                       for channel, counts in values.items()}
                for name, values in (("risk", self.risk), ("hits", self.hits))}

    @classmethod
    def from_state(cls, value, *, counts):
        result = cls()
        for name in ("risk", "hits"):
            values = {int(channel): Counter({int(position): weight for position, weight in weights.items()})
                      for channel, weights in value[name].items()}
            if any(channel not in counts or any(
                position < 1 or type(weight) not in (int, float) or not math.isfinite(weight) or weight <= 0
                for position, weight in weights.items()
            ) or sum(weights.values()) > counts[channel]["total" if name == "risk" else "hits"] + 1e-8
                   for channel, weights in values.items()):
                raise ValueError("Invalid recent outcome counts")
            setattr(result, name, values)
        if set(result.risk) != set(result.hits) or any(
            weight > result.risk[channel][position] + 1e-8
            for channel, weights in result.hits.items() for position, weight in weights.items()
        ):
            raise ValueError("Recent hit counts exceed attempts")
        return result


def validate_recency(rows):
    baseline, recent = RecentHistory(decay=False), RecentHistory()
    contexts, completed, pending = {}, Counter(), {}
    folds = [{"samples": 0, "model_successes": 0, "baseline_successes": 0,
              "model_band_successes": 0, "baseline_band_successes": 0,
              "model_exact": 0, "baseline_exact": 0, "production_samples": 0} for _ in range(2)]
    for index, row in enumerate(rows):
        channel, hit = int(row["channel_id"]), row["outcome"] == "hit"
        context = contexts.setdefault(channel, Context())
        if context.saw_hit and context.streak == 0 and completed[channel] >= 1 and index >= int(.6 * len(rows)):
            old, new = baseline.candidates(channel, 0), recent.candidates(channel, 0)
            if old and new:
                pending[channel] = (select_candidate(rank_number_candidates(old), 0)[0]["attempt"],
                                    select_candidate(rank_number_candidates(new), 0)[0]["attempt"],
                                    int(index >= int(.8 * len(rows))), completed[channel] >= 20)
        if hit and channel in pending:
            old, new, fold_index, production_ready = pending.pop(channel)
            actual, fold = context.streak + 1, folds[fold_index]
            fold["samples"] += 1
            fold["production_samples"] += production_ready
            for name, target in (("baseline", old), ("model", new)):
                fold[f"{name}_successes"] += abs(actual - target) <= HIT_TOLERANCE
                fold[f"{name}_band_successes"] += streak_band(actual) == streak_band(target)
                fold[f"{name}_exact"] += actual == target
        if hit and context.saw_hit:
            completed[channel] += 1
        for history in (baseline, recent):
            history.observe(channel, context, hit=hit)
        context.observe(hit, at=row["created_at"])
    result = combine_validation(folds)
    wins = sum(fold["model_successes"] - fold["baseline_successes"] for fold in folds)
    exact = sum(fold["model_exact"] for fold in folds)
    baseline_exact = sum(fold["baseline_exact"] for fold in folds)
    result.update(half_life=RECENT_HALF_LIFE, number_metric="exact", exact_hits=exact,
                  baseline_exact_hits=baseline_exact, net_window_wins=wins,
                  validation_method="online_chronological")
    result["accepted"] = result["accepted"] and wins >= 4 and exact >= baseline_exact
    return result
