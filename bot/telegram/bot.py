"""Telegram Bot initialization using aiogram with Memory FSM storage."""

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config.settings import settings
from bot.telegram.middlewares.db import DBMiddleware
from bot.core.autopost import AutopostManager
from bot.core.telethon_client import get_telethon_client
from bot.max_api.client import MaxClient

# Import all routers
from bot.telegram.handlers.start import start_router
from bot.telegram.handlers.autopost import autopost_router
from bot.telegram.handlers.transfer import transfer_router
from bot.telegram.handlers.channels import channels_router
from bot.telegram.handlers.payment import payment_router
from bot.telegram.handlers.admin import admin_router


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
    # Create memory storage for FSM
    storage = MemoryStorage()

    # Create dispatcher with storage
    dp = Dispatcher(storage=storage)

    # Register middleware
    dp.update.middleware(DBMiddleware())

    # Register routers (order matters - more specific first)
    dp.include_router(start_router)
    dp.include_router(autopost_router)
    dp.include_router(transfer_router)
    dp.include_router(channels_router)
    dp.include_router(payment_router)
    dp.include_router(admin_router)

    # Setup lifecycle handlers
    @dp.startup()
    async def on_startup() -> None:
        """Log bot startup and initialize autopost manager."""
        bot_user = await bot.get_me()
        print(f"Bot started: @{bot_user.username} (ID: {bot_user.id})")
        
        # Initialize AutopostManager
        telethon = get_telethon_client(
            api_id=settings.telegram_api_id,
            api_hash=settings.telegram_api_hash,
            phone=settings.telegram_phone,
            session_string=settings.telethon_session_string,
        )
        max_client = MaxClient()
        autopost_manager = AutopostManager(telethon, max_client)
        
        # Store in dispatcher workflow_data for access in handlers
        dp.workflow_data["autopost_manager"] = autopost_manager
        print("Autopost manager initialized")

    @dp.shutdown()
    async def on_shutdown() -> None:
        """Cleanup resources on shutdown."""
        await bot.session.close()
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
