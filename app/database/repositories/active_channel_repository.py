import asyncio

from app.database.session import db


panels = db["active_channel_panels"]


async def get_panel(guild_id: int):
    return await asyncio.to_thread(panels.find_one, {"_id": guild_id})


async def ensure_panel(guild_id: int, channel_id: int) -> None:
    await asyncio.to_thread(
        panels.update_one,
        {"_id": guild_id},
        {"$setOnInsert": {
            "channel_id": channel_id, "tracked_channel_ids": [],
            "message_id": None, "poke_enabled": {},
        }},
        upsert=True,
    )


async def set_destination(
    guild_id: int, channel_id: int, tracked: set[int], *, poke_enabled: dict[int, bool],
) -> None:
    await asyncio.to_thread(
        panels.update_one,
        {"_id": guild_id},
        {
            "$set": {
                "channel_id": channel_id,
                "message_id": None,
                "tracked_channel_ids": sorted(tracked),
                "poke_enabled": {str(key): value for key, value in poke_enabled.items() if key in tracked},
            },
        },
        upsert=True,
    )


async def set_channels(
    guild_id: int, channel_ids: set[int], *, poke_enabled: dict[int, bool],
) -> None:
    result = await asyncio.to_thread(
        panels.update_one,
        {"_id": guild_id},
        {"$set": {
            "tracked_channel_ids": sorted(channel_ids),
            "poke_enabled": {str(key): value for key, value in poke_enabled.items() if key in channel_ids},
        }},
    )
    if not result.matched_count:
        raise RuntimeError("The active-channel panel configuration no longer exists.")


async def set_message(guild_id: int, message_id: int) -> None:
    result = await asyncio.to_thread(
        panels.update_one,
        {"_id": guild_id},
        {"$set": {"message_id": message_id}},
    )
    if not result.matched_count:
        raise RuntimeError("The active-channel panel configuration no longer exists.")
