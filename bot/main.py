"""Main entry point for the Max-Repost Bot."""

import asyncio

from bot.utils.logger import init_logger
from bot.telegram.bot import init_bot
from bot.database import Base, engine


async def init_db() -> None:
    """
    Initialize database by creating all tables.
    
    This function creates tables based on SQLAlchemy models if they don't exist.
    Safe to run multiple times - won't recreate existing tables.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Database initialized (tables created if not exist)")


async def main() -> None:
    """
    Async main function - entry point for the bot application.

    Initializes logging, database, creates bot and dispatcher, starts polling.
    """
    # Initialize logger
    init_logger()
    print("Max-Repost Bot starting...")

    # Initialize database (create tables)
    await init_db()

    # Initialize bot and dispatcher
    bot, dp = await init_bot()

    # Start polling
    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        # Graceful shutdown handled by dispatcher.shutdown()
        print("Max-Repost Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
