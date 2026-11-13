"""Per-player missed bets within the current shared channel streak."""
from . import state


def current_user_losses(stats, identity, *, result_message_id, session):
    """Reuse current counters, or bootstrap a legacy/in-progress run once."""
    streak = max(0, int(stats.get("streak", 0)))
    base = int(stats.get("total_gambles", 0)) - streak
    saved = stats.get("streak_user_losses")
    if (stats.get("streak_user_losses_base") == base and isinstance(saved, dict)
            and all(str(key).isdigit() and int(key) > 0 and type(value) is int and value >= 0
                    for key, value in saved.items())
            and sum(saved.values()) <= int(stats.get("streak_loss", 0))):
        return {str(user): loss for user, loss in saved.items()}
    losses = {}
    if not streak:
        return losses
    # Recording inserted this result first. Exclude it so a hit cannot hide
    # the preceding misses, and bound the read by the known shared streak.
    rows = state.gamble_history.find(
        {**identity, "result_message_id": {"$ne": str(result_message_id)}, "outcome": {"$in": ["hit", "miss"]}},
        {"_id": 0, "user_id": 1, "wager": 1, "outcome": 1}, session=session,
    ).sort([("created_at", -1), ("result_message_id", -1)]).limit(streak)
    for row in rows:
        if row.get("outcome") == "hit":
            break
        user = str(row.get("user_id", "0"))
        if user.isdigit() and int(user) > 0 and row.get("outcome") == "miss":
            losses[user] = losses.get(user, 0) + max(0, int(row.get("wager", 0)))
    return losses
