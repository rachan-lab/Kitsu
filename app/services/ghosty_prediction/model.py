from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import datetime
from typing import Any

import numpy as np

from app.services.ghosty_forecast_policy import ANCHOR_POLICY, NUMBER_POLICY, band_interval, clean_evaluation, select_candidate

from .constants import (
    FEATURES,
    FORECAST_HORIZON,
    MAX_HIT_CANDIDATES,
    MIN_COMPLETED_STREAKS,
    NEW_CHANNEL_HOURS,
)
from .features import (
    Context,
    channel_age_hours,
    feature_vector,
    fit_logistic,
    logit,
    losses,
    rates,
    session_day,
    sigmoid,
    supported_features,
    utc,
)
from .serialization import PredictorState
from .bands import BAND_NAMES, GLOBAL_SEQUENCE_DEPTH, band_summary, streak_band
from .candidates import OutcomeCandidates
from .sequences import SequenceHistory
from .recency import RecentHistory, validate_recency
from .pulses import validate_pulses
from .windows import accuracy_summary, combine_sequence_validation, combine_validation, validate_windows


class GhostyPredictor(OutcomeCandidates, PredictorState):
    def __init__(self, history: list[dict[str, Any]], *, now: datetime, enabled: bool = True):
        now = utc(now)
        self.trained_at = now
        self.patterns_enabled = enabled
        self.training_fingerprint: str | None = None
        rows = [
            row
            for row in history
            if isinstance(row.get("created_at"), datetime)
            and row.get("outcome") in {"hit", "miss"}
            and str(row.get("channel_id", "")).isdigit()
            and utc(row["created_at"]) <= now
        ]
        rows.sort(key=lambda row: (utc(row["created_at"]), int(row.get("result_message_id") or 0)))
        self.contexts: dict[int, Context] = {}
        channels, vectors, outcomes, days = [], [], [], []
        self.counts: dict[int, Counter] = {}
        self.streak_risk: dict[int, Counter] = {}
        self.streak_hits: dict[int, Counter] = {}
        self.sequence_history = SequenceHistory()
        self.sequence_active = False
        self.global_sequence_history = SequenceHistory(GLOBAL_SEQUENCE_DEPTH)
        self.global_sequence_active = False
        self.global_recent_streaks: tuple[int, ...] = ()
        self.recency_history = RecentHistory()
        self.recency_active = False
        self.pulse_active = False
        for row in rows:
            channel_id = int(row["channel_id"])
            stamp = utc(row["created_at"])
            context = self.contexts.setdefault(channel_id, Context())
            context.roll_day(stamp)
            # Construct features before revealing this result to its context.
            vectors.append(feature_vector(context, channel_id, stamp))
            channels.append(channel_id)
            days.append(session_day(stamp))
            hit = row["outcome"] == "hit"
            risk = self.streak_risk.setdefault(channel_id, Counter())
            hits = self.streak_hits.setdefault(channel_id, Counter())
            # The start of the first observed run is unknown. Only count
            # attempts after a recorded hit establishes a real run boundary.
            if context.saw_hit:
                attempt = context.streak + 1
                risk[attempt] += 1
                if hit:
                    hits[attempt] += 1
                self.sequence_history.observe(channel_id, context, at=stamp, hit=hit)
                self.global_sequence_history.observe(
                    channel_id, replace(context, recent_streaks=self.global_recent_streaks), at=stamp, hit=hit,
                )
                if hit:
                    self.global_recent_streaks = (*self.global_recent_streaks, attempt)[-GLOBAL_SEQUENCE_DEPTH:]
            outcomes.append(float(hit))
            self.counts.setdefault(channel_id, Counter())["total"] += 1
            self.counts[channel_id]["hits"] += hit
            self.recency_history.observe(channel_id, context, hit=hit)
            context.observe(hit, at=stamp)

        self.samples = len(rows)
        self._pool_streak_history()
        self.weights = np.zeros(len(FEATURES))
        self.mask = np.zeros(len(FEATURES))
        self.active = False
        self.validation: dict[str, Any] = {
            "status": "disabled" if not enabled else "insufficient_data",
            "forecast_accuracy": accuracy_summary([
                evaluation for row in rows if row["outcome"] == "hit"
                if (evaluation := clean_evaluation(row.get("forecast_evaluation"))) is not None
            ]),
            "observations": {
                "known_previous_streak": sum(vector[15] == 1 for vector in vectors),
                "weekend_gambles": sum(vector[18] == 1 for vector in vectors),
                "weekend_hits": sum(int(hit) for vector, hit in zip(vectors, outcomes) if vector[18] == 1),
                "weekday_gambles": sum(vector[18] == 0 for vector in vectors),
                "weekday_hits": sum(int(hit) for vector, hit in zip(vectors, outcomes) if vector[18] == 0),
            },
        }
        y = np.asarray(outcomes, dtype=float)
        self.pooled_rate, self.channel_rates = rates(channels, y)
        if not enabled or len(rows) < 300 or y.sum() < 20 or (1 - y).sum() < 20:
            return

        x = np.asarray(vectors, dtype=float)
        first_split = int(len(rows) * 0.6)
        # Discover features in the earliest training window; require later
        # observations of the new patterns before testing their coefficients.
        mask = supported_features(
            x[:first_split], y[:first_split], set(days[:first_split]),
            validation=(x[first_split:], y[first_split:]),
        )
        model_scores, baseline_scores, historical_scores, fold_results = [], [], [], []
        fold_metrics = []
        window_folds = []
        for start, end in (
            (int(len(rows) * 0.6), int(len(rows) * 0.8)),
            (int(len(rows) * 0.8), len(rows)),
        ):
            train_y, test_y = y[:start], y[start:end]
            if train_y.sum() < 10 or test_y.sum() < 3 or (1 - test_y).sum() < 3:
                return
            pooled, channel_rates = rates(channels[:start], train_y)
            offsets = logit(np.asarray([channel_rates[channel] for channel in channels[:start]]))
            weights = fit_logistic(x[:start] * mask, train_y, offsets)
            window_folds.append(validate_windows(type(self), rows[:start], rows[start:end], weights, mask))
            baseline = np.asarray(
                [channel_rates.get(channel, pooled) for channel in channels[start:end]]
            )
            totals = Counter(channels[:start])
            hits = Counter(channel for channel, hit in zip(channels[:start], train_y) if hit)
            historical = np.asarray(
                [
                    hits[channel] / totals[channel] if totals[channel] else pooled
                    for channel in channels[start:end]
                ]
            )
            predicted = sigmoid(logit(baseline) + (x[start:end] * mask) @ weights)
            model_loss, _ = losses(test_y, predicted)
            baseline_loss, _ = losses(test_y, baseline)
            historical_loss, _ = losses(test_y, historical)
            fold_results.append(model_loss < min(baseline_loss, historical_loss))
            fold_metrics.append({
                "samples": len(test_y), "model_log_loss": model_loss,
                "baseline_log_loss": baseline_loss, "historical_log_loss": historical_loss,
                "weekend_gambles": int(x[start:end, 18].sum()),
                "weekend_hits": int(test_y[x[start:end, 18] == 1].sum()),
            })
            model_scores.extend(predicted)
            baseline_scores.extend(baseline)
            historical_scores.extend(historical)

        test_y = y[int(len(rows) * 0.6) :]
        model_loss, model_brier = losses(test_y, np.asarray(model_scores))
        base_loss, base_brier = losses(test_y, np.asarray(baseline_scores))
        historical_loss, historical_brier = losses(test_y, np.asarray(historical_scores))
        gamble_validated = (
            all(fold_results)
            and model_loss <= min(base_loss, historical_loss) * 0.99
            and model_brier < min(base_brier, historical_brier)
        )
        window_validation = combine_validation(window_folds)
        sequence_validation = combine_sequence_validation(window_folds)
        global_validation = combine_sequence_validation(window_folds, "global")
        self.global_sequence_active = global_validation["accepted"] and (
            global_validation["model_success_percent"] >= max(
                window_validation["model_success_percent"], sequence_validation["model_success_percent"],
            )
        ) and (not sequence_validation["accepted"] or
               global_validation["model_success_percent"] > sequence_validation["model_success_percent"])
        self.sequence_active = sequence_validation["accepted"] and (
            sequence_validation["model_success_percent"] >= window_validation["model_success_percent"]
        ) and not self.global_sequence_active
        self.active = gamble_validated or window_validation["accepted"]
        recency_validation = validate_recency(rows)
        self.recency_active = recency_validation["accepted"] and (
            recency_validation["model_success_percent"] >= max(
                value["model_success_percent"] or 0
                for value in (window_validation, sequence_validation, global_validation)
            )
        )
        if self.recency_active:
            self.sequence_active = self.global_sequence_active = False
        pulse_validation = validate_pulses(rows)
        self.pulse_active = pulse_validation["accepted"]
        if self.pulse_active:
            self.sequence_active = self.global_sequence_active = self.recency_active = False
        self.validation = {
            **self.validation,
            "status": "validated" if self.active or self.sequence_active or self.global_sequence_active or self.recency_active or self.pulse_active else "baseline_better",
            "samples": len(test_y),
            "model_log_loss": model_loss,
            "baseline_log_loss": base_loss,
            "model_brier": model_brier,
            "baseline_brier": base_brier,
            "historical_log_loss": historical_loss,
            "historical_brier": historical_brier,
            "folds": fold_metrics,
            "hit_window": window_validation,
            "sequence_windows": sequence_validation,
            "global_sequence_windows": global_validation,
            "recent_streaks": recency_validation,
            "label_pulses": pulse_validation,
            "selection_metric": "label_pulses" if self.pulse_active else "recent_streaks" if self.recency_active else "global_sequence_windows" if self.global_sequence_active else "sequence_windows" if self.sequence_active else (
                "gamble_probability" if gamble_validated else "hit_window" if self.active else "historical"
            ),
            "supported_pattern_features": [FEATURES[i] for i in range(15, len(FEATURES)) if mask[i]],
        }
        self.mask = mask
        if self.active:
            offsets = logit(np.asarray([self.channel_rates[channel] for channel in channels]))
            self.weights = fit_logistic(x * self.mask, y, offsets)

    def _pool_streak_history(self) -> None:
        self._pooled_streak_risk = Counter()
        self._pooled_streak_hits = Counter()
        for values in self.streak_risk.values():
            self._pooled_streak_risk.update(values)
        for values in self.streak_hits.values():
            self._pooled_streak_hits.update(values)

    def predict(
        self,
        channel_id: int,
        *,
        now: datetime,
        current_streak: int,
        minimum_samples: int = 50,
        historical_counts: dict[str, int] | None = None,
        last_gamble_at: datetime | None = None,
        context_override: Context | None = None,
        global_recent_override: tuple[int, ...] | None = None,
    ) -> dict[str, Any]:
        count = (
            Counter(historical_counts)
            if historical_counts is not None
            else self.counts.get(channel_id, Counter())
        )
        pooled = (
            self.active
            and bool(self.mask[5])
            and channel_age_hours(channel_id, now) < NEW_CHANNEL_HOURS
            and count["total"] < minimum_samples
        )
        ready = count["total"] >= max(1, minimum_samples) or pooled
        # A large number of misses is not a large sample of completed runs.
        # Gate displayed streak targets separately from per-gamble rates. The
        # raw candidates remain available for chronological model validation.
        completed_streaks = sum(
            (self.streak_hits.get(channel_id) or self._pooled_streak_hits).values()
        )
        result = {
            "ready": ready,
            "forecast_ready": ready and completed_streaks >= MIN_COMPLETED_STREAKS,
            "forecast_samples": completed_streaks,
            "minimum_forecast_samples": MIN_COMPLETED_STREAKS,
            "streak_band": band_summary(max(1, current_streak)),
            "sequence_patterns_active": self.sequence_active,
            "global_sequence_patterns_active": self.global_sequence_active,
            "recent_patterns_active": self.recency_active,
            "label_pulse_patterns_active": self.pulse_active,
            "prediction_policy": NUMBER_POLICY,
            "forecast_policy": ANCHOR_POLICY,
            "predicted_band": None,
            "global_previous_streak_bands": [BAND_NAMES[streak_band(length)] for length in (
                self.global_recent_streaks if global_recent_override is None else global_recent_override
            )],
            "previous_streak_bands": [
                BAND_NAMES[streak_band(length)] for length in
                (context_override or self.contexts.get(channel_id, Context())).recent_streaks
            ],
            "samples": count["total"],
            "training_samples": self.samples,
            "minimum_samples": minimum_samples,
            "hit_percent": None,
            "hit_at": None,
            "hit_interval": None,
            "hit_candidates": [],
            "streak_occurrences": dict(self.streak_hits.get(channel_id) or self._pooled_streak_hits),
            "model": "label_pulses" if self.pulse_active else "recent_streaks" if self.recency_active else "global_sequence_patterns" if self.global_sequence_active else "sequence_patterns" if self.sequence_active else (
                "pattern_ml" if self.active else "historical"
            ),
            "candidate_model": "label_pulse_history" if self.pulse_active else "recent_streak_history" if self.recency_active else "global_sequence_history" if self.global_sequence_active else "sequence_history" if self.sequence_active else (
                "channel_streak_history" if self.streak_hits.get(channel_id) else "pooled_streak_history"
            ),
            "pooled": pooled,
            "validation": self.validation,
        }
        if not ready:
            return result

        streak = max(0, current_streak)
        context = replace(
            context_override
            if context_override is not None
            else self.contexts.get(channel_id, Context())
        )
        result["last_hit_length"] = context.previous_streak + 1 if context.previous_streak is not None else None
        result["last_hit_misses"] = context.previous_streak
        result["recent_hit_lengths"] = list(context.recent_streaks)
        if last_gamble_at is not None:
            context.last_gamble_at = utc(last_gamble_at)
        context.roll_day(now)
        context.streak = streak
        context.session_streak = min(context.session_streak, streak)
        prior = self.channel_rates.get(
            channel_id,
            (count["hits"] + 20 * self.pooled_rate) / (count["total"] + 20),
        )
        if self.active:
            vectors = []
            for step in range(FORECAST_HORIZON + 1):
                future = replace(
                    context,
                    streak=streak + step,
                    session_streak=context.session_streak + step,
                    # Conditional attempts after the next one follow an assumed
                    # miss now; their last gamble is no longer the historical one.
                    last_gamble_at=context.last_gamble_at if step == 0 else utc(now),
                )
                vectors.append(feature_vector(future, channel_id, now))
            hazards = sigmoid(
                logit(np.asarray([prior]))[0] + (np.asarray(vectors) * self.mask) @ self.weights
            )
        else:
            hazards = np.full(FORECAST_HORIZON + 1, count["hits"] / count["total"])

        probability = float(hazards[0])
        result["hit_percent"] = round(probability * 100, 2)
        result["forecast_candidates"] = self._streak_outcome_candidates(
            channel_id, streak, hazards, prior, context=context, now=now,
            global_recent=self.global_recent_streaks if global_recent_override is None else global_recent_override,
        )
        result["hit_candidates"] = result["forecast_candidates"][:MAX_HIT_CANDIDATES]
        if result["forecast_candidates"]:
            selected, _ = select_candidate(result["forecast_candidates"], streak)
            result.update(hit_at=selected["attempt"], predicted_band=selected["band"],
                          predicted_band_percent=100 * selected["band_probability"])
            result["hit_interval"] = band_interval(
                selected["attempt"], misses=streak, maximum=max(item["attempt"] for item in result["forecast_candidates"]),
            )
        return result
