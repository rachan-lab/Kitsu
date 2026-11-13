"""Completed channel streaks and a chart of saved hit possibilities."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
import math
import threading
from typing import Any

import numpy as np

from app.database.repositories import ghosty_repository as repository
from app.services.ghosty_prediction import MAX_HIT_CANDIDATES
from app.services.ghosty_forecast_policy import candidate_rank, window_passed


_PLOT_LOCK = threading.Lock()
BACKGROUND, TEXT, MUTED = "#202127", "#F2F3F5", "#B5BAC1"
SELECTED, OTHER, MISSED = "#7CC6B0", "#B7A6E8", "#737781"


@dataclass(frozen=True)
class CompletedStreak:
    hit_at: int
    loss: int
    payout: int

    @property
    def net(self) -> int:
        return self.payout - self.loss


def completed_streaks(history: list[dict[str, Any]], *, limit: int = 5) -> list[CompletedStreak]:
    """Summarize known hit-to-hit runs, newest first; skip the partial first run."""
    known_start, misses, loss = False, 0, 0
    completed = []
    for row in history:
        if row["outcome"] == "hit":
            if known_start:
                completed.append(CompletedStreak(misses + 1, loss, int(row.get("payout", 0))))
            known_start, misses, loss = True, 0, 0
        elif row["outcome"] == "miss" and known_start:
            misses += 1
            loss += int(row.get("wager", 0))
    return list(reversed(completed[-max(1, min(limit, 5)):]))


@dataclass
class StreakSnapshot:
    now: datetime
    stats: dict[str, Any]
    previous_attempts: list[CompletedStreak]
    prediction: dict[str, Any]


class GhostyStreakService:
    def __init__(self, tracker: Any):
        self.tracker = tracker

    async def load(self, guild_id: int, channel_id: int) -> StreakSnapshot:
        now = datetime.now(timezone.utc)
        history, stats, minimum = await asyncio.gather(
            repository.get_completed_streak_history(guild_id, channel_id, now=now, limit=5),
            self.tracker.get_stats(guild_id, channel_id),
            repository.get_config(guild_id, "minimum_samples", 50),
        )
        prediction = await self.tracker.get_prediction_data(
            guild_id, channel_id, minimum_samples=int(minimum), stats=stats,
        )
        return StreakSnapshot(now, stats, completed_streaks(history), prediction)


def chart_candidates(snapshot: StreakSnapshot) -> list[dict[str, Any]]:
    """Use the same frozen candidates as the tracker, never a new fitted curve."""
    if not snapshot.prediction.get("ready"):
        return []
    candidates = snapshot.prediction.get("hit_candidates", [])
    valid = [candidate for candidate in candidates if (
        type(candidate.get("attempt")) is int and candidate["attempt"] > 0
        and type(candidate.get("probability")) in (int, float)
        and math.isfinite(candidate["probability"]) and 0 < candidate["probability"] <= 1
    )]
    # Keep remaining long outcomes visible after the early peaks have passed.
    ranked = sorted(valid, key=lambda item: (
        item["attempt"] != snapshot.prediction.get("hit_at"),
        window_passed(item["attempt"], max(0, int(snapshot.stats.get("streak", 0)))),
        candidate_rank(item),
    ))
    return sorted(ranked[:MAX_HIT_CANDIDATES], key=lambda candidate: candidate["attempt"])


def render_previous_hits_chart(snapshot: StreakSnapshot) -> bytes:
    """Plot the latest completed hits in display order, with a label at each point."""
    with _PLOT_LOCK:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure

        figure = Figure(figsize=(8, 2.4), dpi=150, facecolor=BACKGROUND)
        FigureCanvasAgg(figure)
        ax = figure.subplots()
        ax.set_facecolor(BACKGROUND)
        ax.set_axis_off()
        values = [row.hit_at for row in snapshot.previous_attempts[:5]]
        if values:
            positions = np.arange(len(values))
            ax.plot(positions, values, color=SELECTED, linewidth=2.5, marker="o",
                    markersize=7, markeredgecolor=BACKGROUND, markeredgewidth=1.5)
            ax.fill_between(positions, values, 0, color=SELECTED, alpha=0.08)
            for position, value in zip(positions, values):
                ax.annotate(f"{value:,}", (position, value), xytext=(0, 11),
                            textcoords="offset points", ha="center", va="bottom",
                            fontsize=18, fontweight="bold", color=TEXT)
            ax.set_xlim(-0.35, max(0.35, len(values) - 0.65))
            ax.set_ylim(0, max(values) * 1.3)
        else:
            ax.text(0.5, 0.5, "No completed hits yet", ha="center", va="center",
                    color=MUTED, fontsize=18, transform=ax.transAxes)
        figure.tight_layout(pad=1.1)
        output = BytesIO()
        figure.savefig(output, format="png", facecolor=BACKGROUND)
        figure.clear()
        return output.getvalue()


def render_streak_chart(snapshot: StreakSnapshot) -> bytes:
    """Draw positive, closed candidate curves; outer peaks use saved chances."""
    with _PLOT_LOCK:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        from matplotlib.lines import Line2D
        from matplotlib.ticker import MaxNLocator, PercentFormatter

        figure = Figure(figsize=(8, 4), dpi=150, facecolor=BACKGROUND)
        FigureCanvasAgg(figure)
        ax = figure.subplots()
        ax.set_facecolor(BACKGROUND)
        ax.tick_params(colors=MUTED, labelsize=12, length=0, pad=8)
        ax.spines[["top", "right"]].set_visible(False)
        for edge in ("left", "bottom"):
            ax.spines[edge].set_color("#555962")
        ax.set_axisbelow(True)
        ax.grid(axis="y", color="#42454F", linewidth=0.7, alpha=0.6)
        ax.set_title("Possible hit attempts", color=TEXT, loc="left", fontsize=18, pad=16)
        ax.set_ylabel("Saved chance (%)", color=MUTED, fontsize=13, labelpad=12)
        ax.set_xlabel("Hit attempt", color=MUTED, fontsize=13, labelpad=12)
        ax.yaxis.set_major_formatter(PercentFormatter(xmax=100))
        candidates = chart_candidates(snapshot)
        if not candidates:
            ax.text(0.5, 0.5, "Still learning", ha="center", va="center", color=MUTED,
                    fontsize=18, transform=ax.transAxes)
            ax.set_xticks([])
            ax.set_ylim(0, 100)
            ax.set_yticks([0, 25, 50, 75, 100])
        else:
            streak = max(0, int(snapshot.stats.get("streak", 0)))
            selected = snapshot.prediction.get("hit_at")
            highest = max(candidate["probability"] for candidate in candidates) * 100
            ticks = MaxNLocator(nbins=4, steps=[1, 2, 2.5, 5, 10]).tick_values(0, highest * 1.2)
            ceiling = min(100.0, float(ticks[-1]))
            ax.set_ylim(0, ceiling)
            ax.set_yticks(ticks[(ticks >= 0) & (ticks <= ceiling)])
            positions = np.asarray([item["attempt"] for item in candidates], dtype=float)
            spacing = float(np.min(np.diff(positions))) if len(positions) > 1 else 1.0
            # Sparse, widely separated outcomes otherwise make adjacent labels
            # unreadable. Explicitly label that layout as listed outcomes.
            if len(positions) > 1 and np.ptp(positions) / spacing > 10:
                positions = np.arange(len(candidates), dtype=float)
                spacing = 1.0
                ax.set_xlabel("Hit attempt (listed outcomes)", color=MUTED, fontsize=13, labelpad=12)
            shape_x = np.linspace(-1, 1, 201)
            shape_y = np.cos(shape_x * np.pi / 2) ** 2
            shape_y[[0, -1]] = 0
            for position, candidate in zip(positions, candidates):
                attempt, chance = candidate["attempt"], candidate["probability"] * 100
                missed = window_passed(attempt, streak) or candidate.get("status") == "missed"
                color = MISSED if missed else SELECTED if attempt == selected else OTHER
                width = spacing * 0.42
                ax.fill(position + shape_x * width, chance * shape_y, closed=True,
                        facecolor=color + "20", edgecolor=color, linewidth=1.7, zorder=3)
                ax.fill(position + shape_x * width * 0.76, chance * (0.08 + 0.72 * shape_y),
                        closed=True, fill=False, edgecolor=color, linewidth=1.2, alpha=0.8, zorder=4)
                percent = f"{chance:.2g}%" if chance < 0.1 else f"{chance:.1f}".rstrip("0").rstrip(".") + "%"
                near_top = chance > ceiling * 0.88
                ax.annotate(percent, (position, chance), xytext=(0, -11 if near_top else 9),
                            textcoords="offset points", ha="center", va="top" if near_top else "bottom",
                            color=color, fontsize=15, fontweight="bold", zorder=5)
            labels = [f"@ {item['attempt']:,}" + (" ×" if window_passed(item["attempt"], streak) or item.get("status") == "missed" else "")
                      for item in candidates]
            ax.set_xticks(positions, labels)
            ax.set_xlim(positions[0] - spacing * 0.7, positions[-1] + spacing * 0.7)
            ax.legend(handles=[Line2D([], [], color=color, linewidth=2, label=label)
                               for label, color in (("Selected", SELECTED), ("Alternative", OTHER), ("Passed", MISSED))],
                      loc="upper center", bbox_to_anchor=(0.5, -0.25), ncol=3,
                      frameon=False, labelcolor=MUTED, fontsize=12, handlelength=1.3)
            figure.text(0.55, 0.025, "Rule: outer peak = saved chance at that attempt.",
                        color=MUTED, fontsize=12, ha="center", va="bottom")
        figure.subplots_adjust(left=0.13, right=0.97, top=0.84, bottom=0.29)
        output = BytesIO()
        figure.savefig(output, format="png", facecolor=BACKGROUND)
        figure.clear()
        return output.getvalue()
