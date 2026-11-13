from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from pymongo.errors import DuplicateKeyError

from app.services.ghosty_forecast_policy import COOLDOWN_RUNS, evaluate_forecast, next_forecast_cooldown

from . import state
from .cooldown import previous_actual_hits
from .history import get_gamble_by_result_message, get_gamble_by_source_message
from .personal import current_user_losses
from .state import _now


def _record_result_transaction(
    *,
    guild_id: int,
    channel_id: int,
    user_id: int,
    source_message_id: int,
    result_message_id: int,
    command_message_id: int | None,
    wager: int,
    outcome: str,
    payout: int,
    source: str,
    created_at: datetime | None,
    attribution_verified: bool = False,
) -> dict[str, Any]:
    """Record history and its channel statistics in one transaction."""

    guild_key = str(guild_id)
    channel_key = str(channel_id)
    user_key = str(user_id)

    history_identity = {
        "guild_id": guild_key,
        "source_message_id": str(source_message_id),
    }
    stats_identity = {
        "guild_id": guild_key,
        "channel_id": channel_key,
    }
    user_identity = {
        "guild_id": guild_key,
        "channel_id": channel_key,
        "user_id": user_key,
    }

    net_pl_delta = payout if outcome == "hit" else -wager
    now = _now()

    history_document = {
        **history_identity,
        **user_identity,
        "result_message_id": str(result_message_id),
        "command_message_id": (str(command_message_id) if command_message_id is not None else None),
        "wager": wager,
        "outcome": outcome,
        "payout": payout,
        "net_pl_delta": net_pl_delta,
        "source": source,
        "created_at": created_at or now,
        "recorded_at": now,
        "attribution_verified": attribution_verified,
    }

    def transaction_body(session) -> dict[str, Any]:
        # Inserting first ensures that a duplicate source message aborts
        # the transaction before any statistics are changed.
        state.gamble_history.insert_one(
            history_document,
            session=session,
        )

        stats = state.channel_stats.find_one(
            stats_identity,
            session=session,
        )

        if stats is None:
            stats = {
                **stats_identity,
                "streak": 0,
                "streak_loss": 0,
                "net_pl": 0,
                "hits": 0,
                "misses": 0,
                "total_gambles": 0,
            }

        current_streak = int(stats.get("streak", 0))
        current_loss = int(stats.get("streak_loss", 0))
        streak_net_pl = payout - current_loss if outcome == "hit" else -wager
        user_losses = current_user_losses(stats, stats_identity, result_message_id=result_message_id, session=session)
        personal_loss = user_losses.get(user_key, 0) if user_id > 0 else None
        if outcome == "miss" and personal_loss is not None:
            personal_loss += wager
            user_losses[user_key] = personal_loss
        personal_net = (payout - personal_loss if outcome == "hit" else -personal_loss) if personal_loss is not None else None

        if outcome == "hit":
            new_streak = 0
            new_loss = 0
            hit_delta = 1
            miss_delta = 0
        else:
            new_streak = current_streak + 1
            new_loss = current_loss + wager
            hit_delta = 0
            miss_delta = 1

        stats_update = {
            "$setOnInsert": stats_identity,
            "$set": {
                "streak": new_streak,
                "streak_loss": new_loss,
                "updated_at": now,
                "streak_user_losses": {} if outcome == "hit" else user_losses,
                "streak_user_losses_base": int(stats.get("total_gambles", 0)) + 1 if outcome == "hit"
                else int(stats.get("total_gambles", 0)) - current_streak,
            },
            "$inc": {
                "net_pl": net_pl_delta,
                "hits": hit_delta,
                "misses": miss_delta,
                "total_gambles": 1,
            },
        }
        if outcome == "hit":
            stats_update["$set"]["last_hit_misses"] = current_streak
            stats_update["$set"]["last_hit_length"] = current_streak + 1
            previous_hits = previous_actual_hits(stats, stats_identity, result_message_id=result_message_id, session=session)
            stats_update["$set"]["actual_hit_cooldown"] = [*previous_hits, current_streak + 1][-COOLDOWN_RUNS:]
            stats_update["$set"]["forecast_cooldown"] = next_forecast_cooldown(stats, misses=current_streak)
            evaluation = evaluate_forecast(
                stats.get("streak_forecast"),
                base=int(stats.get("total_gambles", 0)) - current_streak,
                misses=current_streak, at=created_at or now, source=source,
            )
            if evaluation is not None:
                state.gamble_history.update_one(
                    history_identity, {"$set": {"forecast_evaluation": evaluation}}, session=session,
                )
                stats_update["$set"]["last_forecast_evaluation"] = evaluation
                stats_update["$inc"].update(
                    forecast_evaluated=1, forecast_successes=int(evaluation["success"]),
                    initial_forecast_evaluated=int("initial_success" in evaluation),
                    initial_forecast_successes=int(evaluation.get("initial_success", False)),
                )
            # This streak's final tracker becomes an archived message. The
            # first miss of the next streak must not delete it, even on restart.
            stats_update["$unset"] = {"tracker_message_id": "", "streak_forecast": ""}
        state.channel_stats.update_one(
            stats_identity,
            stats_update,
            upsert=True,
            session=session,
        )

        updated_stats = state.channel_stats.find_one(
            stats_identity,
            session=session,
        )

        if updated_stats is None:
            raise RuntimeError("Channel stats disappeared during transaction.")

        last_streak = current_streak

        updated_stats["last_streak"] = last_streak
        updated_stats["streak_net_pl"] = streak_net_pl
        updated_stats["user_streak_loss"] = personal_loss
        updated_stats["user_streak_net_pl"] = personal_net
        return updated_stats

    with state.client.start_session() as session:
        return session.with_transaction(transaction_body)


async def record_result_atomically(
    *,
    guild_id: int,
    channel_id: int,
    user_id: int,
    source_message_id: int,
    result_message_id: int,
    command_message_id: int | None = None,
    wager: int,
    outcome: str,
    payout: int,
    source: str = "live",
    created_at: datetime | None = None,
    attribution_verified: bool = False,
) -> dict[str, Any] | None:
    """Record a result and update all related state atomically.

    Returns updated channel statistics for a new result.
    Returns None if this source message has already been recorded.

    Transaction retries are handled by PyMongo. All database operations
    within the transaction are synchronous and run in the same worker thread.
    """

    if outcome not in {"hit", "miss"}:
        raise ValueError("outcome must be 'hit' or 'miss'")

    if wager <= 0:
        raise ValueError("wager must be greater than zero")

    if payout < 0:
        raise ValueError("payout cannot be negative")

    if outcome == "hit" and payout != wager * 10:
        raise ValueError("A hit payout must equal 10 times the wager.")

    if outcome == "miss" and payout != 0:
        raise ValueError("A miss must have a payout of zero.")

    # A previously recorded response must not be counted again, including
    # legacy records whose source_message_id is the original command ID.
    existing_result = await get_gamble_by_result_message(
        guild_id,
        result_message_id,
    )
    if existing_result is not None:
        return None

    try:
        return await asyncio.to_thread(
            _record_result_transaction,
            guild_id=guild_id,
            channel_id=channel_id,
            user_id=user_id,
            source_message_id=source_message_id,
            result_message_id=result_message_id,
            command_message_id=command_message_id,
            wager=wager,
            outcome=outcome,
            payout=payout,
            source=source,
            created_at=created_at,
            attribution_verified=attribution_verified,
        )
    except DuplicateKeyError:
        # Only treat it as an idempotent duplicate if this exact source
        # message already exists. Other unique-index conflicts must surface.
        existing = await get_gamble_by_source_message(
            guild_id,
            source_message_id,
        )
        if existing is not None:
            return None

        existing_result = await get_gamble_by_result_message(
            guild_id,
            result_message_id,
        )
        if existing_result is not None:
            return None

        raise
