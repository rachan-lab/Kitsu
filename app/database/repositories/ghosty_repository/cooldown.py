"""Recover the preceding actual hit for channels created by an older import."""
from app.services.ghosty_forecast_policy import recent_actual_hits

from . import state


def previous_actual_hits(stats, identity, *, result_message_id, session):
    saved = recent_actual_hits(stats)
    if saved:
        return saved
    # Only a legacy channel without saved hit lengths needs this read.
    # The first partial run is not treated as a completed hit-to-hit run.
    seen_hit, misses = False, 0
    rows = state.gamble_history.find(
        {**identity, "result_message_id": {"$ne": str(result_message_id)}, "outcome": {"$in": ["hit", "miss"]}},
        {"_id": 0, "outcome": 1}, session=session,
    ).sort([("created_at", -1), ("result_message_id", -1)])
    for row in rows:
        if row.get("outcome") == "hit":
            if seen_hit:
                return [misses + 1]
            seen_hit = True
        elif seen_hit and row.get("outcome") == "miss":
            misses += 1
    return []
