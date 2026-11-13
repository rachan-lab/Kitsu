"""Server chat answers questions and gives instructions; it never executes actions."""

from .cog import ServerChat


async def setup(bot):
    await bot.add_cog(ServerChat(bot))
