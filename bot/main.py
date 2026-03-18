"""Main entry point for the Max-Repost Bot."""

import asyncio
import logging

from loguru import logger

from bot.utils.logger import init_logger
from bot.telegram.bot import init_bot
from bot.database import Base, engine
from bot.max_api.max_bot_handler import MaxBotListener
from bot.max_api.client import MaxClient
from bot.core.autopost import AutopostManager, set_autopost_manager
from bot.core.telethon_client import TelethonChannelClient
from bot.database.repositories.autopost_subscription import AutopostSubscriptionRepository
from bot.database.connection import get_session
from config.settings import settings


async def init_db() -> None:
    """
    Initialize database by creating all tables.
    
    This function creates tables based on SQLAlchemy models if they don't exist.
    Safe to run multiple times - won't recreate existing tables.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Run migrations for new columns
    await _run_column_migrations()
    
    print("Database initialized (tables created if not exist)")


async def _run_column_migrations() -> None:
    """
    Run simple column migrations for SQLite.
    
    SQLite doesn't support ALTER TABLE ADD COLUMN with constraints easily,
    so we use a simple approach: try to add the column, ignore if it exists.
    """
    from sqlalchemy import text
    
    async with engine.begin() as conn:
        # Check if free_posts_used column exists in users table
        try:
            # SQLite specific: check table info
            result = await conn.execute(
                text("SELECT 1 FROM pragma_table_info('users') WHERE name='free_posts_used'")
            )
            column_exists = result.scalar() is not None
            
            if not column_exists:
                # Add the column with default 0
                await conn.execute(
                    text("ALTER TABLE users ADD COLUMN free_posts_used INTEGER NOT NULL DEFAULT 0")
                )
                print("Migration: Added free_posts_used column to users table")
        except Exception as e:
            # If anything fails, log it but don't stop the bot
            print(f"Migration warning (non-critical): {e}")


async def main() -> None:
    """
    Async main function - entry point for the bot application.

    Initializes logging, database, creates bot and dispatcher, starts polling.
    Also starts Max bot listener for responding to messages in Max messenger.
    Telethon client runs in parallel for receiving channel updates.
    """
    # Initialize logger
    init_logger()
    print("Max-Repost Bot starting...")

    # Initialize database (create tables)
    await init_db()

    # Initialize bot and dispatcher
    bot, dp = await init_bot()

    # Initialize Telethon client (for user-based MTProto operations)
    telethon_client = TelethonChannelClient(
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
        phone=settings.telegram_phone,
    )
    
    # Initialize and start the Telethon client
    # This connects and starts the internal update loop
    await telethon_client._get_client()
    
    max_client = MaxClient(settings.max_access_token)
    autopost_manager = AutopostManager(telethon_client, max_client, bot)
    set_autopost_manager(autopost_manager)
    print("AutopostManager initialized")

    # Load active autopost subscriptions
    async with get_session() as session:
        repo = AutopostSubscriptionRepository(session)
        active_subs = await repo.get_active_subscriptions()
        for sub in active_subs:
            await autopost_manager.start_monitoring(sub)
        logger.info(f"Autopost: loaded {len(active_subs)} active subscriptions")

    # Start Max bot listener (responds to messages in Max messenger)
    max_listener = MaxBotListener(settings.max_access_token)
    listener_task = asyncio.create_task(max_listener.start())
    print("Max bot listener started")

    # Start polling and Telethon event loop in parallel
    # Both must run in the same asyncio event loop for Telethon events to work
    try:
        await asyncio.gather(
            dp.start_polling(
                bot,
                allowed_updates=dp.resolve_used_update_types(),
            ),
            telethon_client.run_until_disconnected(),
        )
    finally:
        # Graceful shutdown
        print("Shutting down...")
        
        # Stop all autopost tasks
        if autopost_manager:
            await autopost_manager.stop_all()
        
        await max_listener.stop()
        listener_task.cancel()
        try:
            await listener_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Max listener task failed: {e}", exc_info=True)
        
        # Disconnect Telethon client
        await telethon_client.close()
        
        print("Max-Repost Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
