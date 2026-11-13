"""Per-hitter market-mention preferences, scoped to a server."""
from __future__ import annotations

import asyncio

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from . import state
from .state import _now


async def get_silent_ping_user_ids(guild_id: int, user_ids: list[int]) -> set[int]:
    keys = {f"silent_ping:{user_id}": user_id for user_id in user_ids if user_id > 0}
    if not keys:
        return set()
    cursor = state.config_collection.find(
        {"guild_id": str(guild_id), "key": {"$in": list(keys)}, "value": True},
        {"_id": 0, "key": 1},
    )
    rows = await asyncio.to_thread(list, cursor)
    return {keys[row["key"]] for row in rows if row.get("key") in keys}


async def toggle_silent_ping(guild_id: int, user_id: int) -> bool:
    """Toggle atomically so concurrent clicks cannot lose each other's changes."""
    identity = {"guild_id": str(guild_id), "key": f"silent_ping:{user_id}"}
    pipeline = [{"$set": {**identity, "value": {"$not": [{"$eq": ["$value", True]}]}, "updated_at": _now()}}]

    def toggle():
        try:
            return state.config_collection.find_one_and_update(
                identity, pipeline, upsert=True, return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            # Another first click created the unique preference row first.
            return state.config_collection.find_one_and_update(
                identity, pipeline, return_document=ReturnDocument.AFTER,
            )

    document = await asyncio.to_thread(toggle)
    if document is None or type(document.get("value")) is not bool:
        raise RuntimeError("Could not save the Silent ping preference")
    return document["value"]
