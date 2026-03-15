"""Telegram Bot initialization using aiogram with Redis FSM storage."""

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage

from config.settings import settings
from bot.telegram.middlewares.db import DBMiddleware
from bot.telegram.handlers.start import start_router


def create_bot() -> Bot:
    """
    Create aiogram Bot instance.

    Returns:
        Configured Bot instance
    """
    return Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def setup_dispatcher(bot: Bot) -> Dispatcher:
    """
    Configure dispatcher with routers, middleware, and FSM storage.

    Args:
        bot: Bot instance

    Returns:
        Configured Dispatcher
    """
    # Create Redis storage for FSM
    storage = RedisStorage.from_url(settings.redis_url)

    # Create dispatcher with storage
    dp = Dispatcher(storage=storage)

    # Register middleware
    dp.update.middleware(DBMiddleware())

    # Register routers (order matters - more specific first)
    dp.include_router(start_router)

    # Setup lifecycle handlers
    @dp.startup()
    async def on_startup() -> None:
        """Log bot startup."""
        bot_user = await bot.get_me()
        print(f"Bot started: @{bot_user.username} (ID: {bot_user.id})")

    @dp.shutdown()
    async def on_shutdown() -> None:
        """Cleanup resources on shutdown."""
        await bot.session.close()
        await storage.close()
        print("Bot shut down gracefully")

    return dp


async def init_bot() -> tuple[Bot, Dispatcher]:
    """
    Initialize aiogram Bot and Dispatcher.

    Returns:
        Tuple of (Bot, Dispatcher) instances
    """
    bot = create_bot()
    dp = setup_dispatcher(bot)
    return bot, dp
