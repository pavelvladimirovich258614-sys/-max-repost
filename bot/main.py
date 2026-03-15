"""Main entry point for the Max-Repost Bot."""

import asyncio

from bot.utils.logger import init_logger
from bot.telegram.bot import init_bot


async def main() -> None:
    """
    Async main function - entry point for the bot application.

    Initializes logging, creates bot and dispatcher, starts polling.
    """
    # Initialize logger
    init_logger()
    print("Max-Repost Bot starting...")

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
