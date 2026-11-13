"""Small tracker charts drawn from the saved forecast and completed hit lengths."""
from __future__ import annotations

from io import BytesIO
import math
import random

from app.services.ghosty_streak_service import _PLOT_LOCK


CHART_WIDTH, CHART_HEIGHT = 480, 160
BACKGROUND, TEXT, MUTED, SELECTED = "#242429", "#DBDEE1", "#8E929A", "#7CC6B0"


def choose_tracker_chart(prediction):
    if (not prediction.get("ready") or prediction.get("estimate_status") == "missed"
            or prediction.get("is_neighbor") or prediction.get("anchor_attempt") != prediction.get("hit_at")
            or not prediction.get("hit_candidates")):
        return None
    choices = [None, None, "curve"]
    if len(prediction.get("recent_hit_lengths", [])) >= 2:
        choices.append("history")
    return random.choice(choices)


def render_tracker_chart(prediction, streak, kind):
    """Return a 480 × 160 PNG. Presentation varies; forecast values do not."""
    with _PLOT_LOCK:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        from matplotlib.ticker import MaxNLocator, PercentFormatter

        figure = Figure(figsize=(CHART_WIDTH / 100, CHART_HEIGHT / 100), dpi=100, facecolor=BACKGROUND)
        FigureCanvasAgg(figure)
        ax = figure.add_subplot(111)
        figure.subplots_adjust(left=.14, right=.97, bottom=.19, top=.78)
        ax.set_facecolor(BACKGROUND)
        ax.tick_params(axis="both", colors=TEXT, labelsize=9, length=0, pad=5)
        for spine in ax.spines.values():
            spine.set_color("#45464E")
            spine.set_linewidth(.7)
        ax.grid(axis="y", color="#373840", linewidth=.7)
        ax.xaxis.set_label_position("top")
        ax.yaxis.set_major_locator(MaxNLocator(nbins=3, min_n_ticks=2))
        if kind == "history":
            values = [int(value) for value in prediction.get("recent_hit_lengths", [])[-5:] if value > 0]
            if not values:
                return None
            positions = list(range(1, len(values) + 1))
            ax.plot(positions, values, color=SELECTED, linewidth=1.5, marker="o", markersize=3)
            ax.set_xlim(.7, len(values) + .3)
            ax.set_ylim(0, max(values) * 1.25)
            ax.set_xticks(positions)
            ax.set_xlabel("Recent hit", color=TEXT, fontsize=10, labelpad=7)
            ax.set_ylabel("Hit streak length", color=TEXT, fontsize=9, labelpad=6)
            for position, value in zip(positions, values):
                ax.annotate(str(value), (position, value), xytext=(0, 5), textcoords="offset points",
                            color=TEXT, fontsize=9, ha="center", va="bottom")
        else:
            points = sorted((item["attempt"], item["probability"] * 100)
                            for item in prediction.get("hit_candidates", [])
                            if type(item.get("attempt")) is int and type(item.get("probability")) in (int, float)
                            and math.isfinite(item["probability"]) and 0 <= item["probability"] <= 1)
            if not points:
                return None
            target = prediction["hit_at"]
            actual_markers = {value for value in prediction.get("actual_hit_cooldown", [])
                              if type(value) is int and value > 0}
            positions, values = zip(*points)
            peak, peak_chance = max(points, key=lambda item: item[1])
            peak_blocked = any(item.get("blocked", False) and item.get("attempt") == peak
                               for item in prediction["hit_candidates"])
            ax.plot(positions, values, color=SELECTED, linewidth=1.5, marker="o", markersize=2)
            ax.set_xlim(max(0, min(streak, min(positions), *actual_markers) - 1),
                        max(target, max(positions), *actual_markers) + 1)
            ax.set_ylim(0, max(.01, max(values) * 1.3))
            # Always put the peak's exact attempt below it, even on cooldown.
            # Keep nearby automatic ticks from crowding the peak/target labels.
            left, right = ax.get_xlim()
            marked = {peak, target} | actual_markers
            ticks = [tick for tick in MaxNLocator(nbins=4, integer=True).tick_values(left, right)
                     if left <= tick <= right and all(abs(tick - number) > (right - left) * .055 for number in marked)]
            ax.set_xticks(sorted(set(ticks) | marked))
            ax.yaxis.set_major_formatter(PercentFormatter(xmax=100, decimals=0 if max(values) >= 2 else 1))
            ax.set_xlabel("Hit attempt", color=TEXT, fontsize=10, labelpad=7)
            ax.set_ylabel("Chance (%)", color=TEXT, fontsize=10, labelpad=6)
            ax.axvline(streak, color=MUTED, linewidth=.8, linestyle=(0, (3, 3)))
            ax.axvspan(target, prediction.get("anchor_end", target + 2), color=SELECTED, alpha=.07)
            if peak != target:
                peak_color = MUTED if peak_blocked else SELECTED
                ax.vlines(peak, 0, peak_chance, colors=peak_color, linewidth=.8, linestyles=(0, (2, 3)))
                ax.scatter([peak], [peak_chance], s=18, color=peak_color, zorder=4)
                for tick, label in zip(ax.get_xticks(), ax.get_xticklabels()):
                    if tick == peak:
                        label.set_color(peak_color)
            for actual in actual_markers - {target}:
                ax.plot([actual], [0], marker="|", markersize=5, color=MUTED, clip_on=False, zorder=5)
            previous_tick = None
            staggered = False
            for tick, tick_label in zip(ax.get_xticks(), ax.get_xticklabels()):
                if tick in actual_markers and tick != target:
                    tick_label.set_color(MUTED)
                close = previous_tick is not None and tick - previous_tick < (right - left) * .045
                staggered = close and not staggered
                if staggered:
                    tick_label.set_y(-.12)
                previous_tick = tick
            probability = prediction.get("candidate_percent", 0)
            ax.scatter([target], [probability], s=22, color=SELECTED, zorder=4)
            align = "right" if target > sum(ax.get_xlim()) / 2 else "left"
            ax.annotate(f"{target} · {probability:.1f}%", (target, probability), xytext=(0, 6),
                        textcoords="offset points", color=TEXT, fontsize=9, ha=align, va="bottom")
        output = BytesIO()
        figure.savefig(output, format="png", facecolor=BACKGROUND, dpi=100)
        figure.clear()
        return output.getvalue()
