import discord
from discord.ext import commands


APPLICATION_EMOJIS = {
    "warning": 1555232371396317224,
    "crosschecked": 1556332674551914569,
    "crossunchecked": 1556332779338334351,
    "tickchecked": 1556331542257598595,
    "tickunchecked": 1556331932927660032,
    "goos": 1558941112247394334,
    "new": 1562883130267017261,
    "gear": 1562886411034697778,
    "poke": 1569776707228012585,
    "loading": 1564713766220865617,
    "ghosty_track": 1564714687084634274,
    "ghosty_pepesad": 1569038117930012702,
    "ghosty_depressed": 1569038113018347622,
    "ghosty_broken": 1569038108517994568,
    "ghosty_shocked": 1569038106085163129,
    "ghosty_pout": 1569038102918598737,
    "ghosty_angrystare": 1569038100032913570,
    "ghosty_shinchanpout": 1569038097075667115,
    "ghosty_yay": 1569037297788125286,
    "ghosty_party": 1569037012504289291,
    "ghosty_this_is_fine": 1569036509590454463,
    "ghosty_fine": 1569036460953313290,
}

ANIMATED_APPLICATION_EMOJIS = frozenset({
    "loading", "ghosty_track", "ghosty_pepesad", "ghosty_depressed",
    "ghosty_pout", "ghosty_yay", "ghosty_party",
})

_EMOJI_CACHE: dict[str, str] = {}


def get_cached_application_emoji(emoji_key: str, fallback: str = "") -> str:
    """Render known emoji markup immediately, preserving cached animation metadata."""
    if emoji_key in _EMOJI_CACHE:
        return _EMOJI_CACHE[emoji_key]
    emoji_id = APPLICATION_EMOJIS.get(emoji_key)
    if emoji_id is None:
        return fallback
    animated = "a" if emoji_key in ANIMATED_APPLICATION_EMOJIS else ""
    return f"<{animated}:{emoji_key}:{emoji_id}>"


def get_application_emoji_url(emoji_key: str) -> str | None:
    """Build a thumbnail URL locally, using cached animation metadata."""
    emoji_id = APPLICATION_EMOJIS.get(emoji_key)
    if emoji_id is None:
        return None
    emoji = discord.PartialEmoji.from_str(get_cached_application_emoji(emoji_key))
    url = str(emoji.url)
    return f"{url}{'&' if '?' in url else '?'}size=128"


async def get_application_emoji(
    bot: commands.Bot,
    emoji_key: str,
    fallback: str,
) -> str:
    """
    Fetch an application emoji once and cache it.

    Subsequent calls for the same emoji key use the cached value
    instead of making another Discord API request.
    """

    if emoji_key in _EMOJI_CACHE:
        return _EMOJI_CACHE[emoji_key]

    emoji_id = APPLICATION_EMOJIS.get(emoji_key)

    if emoji_id is None:
        return fallback

    try:
        app_emoji = await bot.fetch_application_emoji(emoji_id)

        emoji_string = str(app_emoji)

        _EMOJI_CACHE[emoji_key] = emoji_string

        return emoji_string

    except discord.HTTPException:
        return fallback
