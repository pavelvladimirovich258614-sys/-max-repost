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
from bot.payments.yookassa_client import YooKassaClient
from bot.payments.payment_checker import check_pending_payments
from bot.payments.webhook_server import start_webhook_server
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
    
    logger.info("Database initialized (tables created if not exist)")


async def _run_column_migrations() -> None:
    """
    Run simple column migrations for SQLite.
    
    SQLite doesn't support ALTER TABLE ADD COLUMN with constraints easily,
    so we use a simple approach: try to add the column, ignore if it exists.
    """
    from sqlalchemy import text
    
    async with engine.begin() as conn:
        # Migration 1: Check if free_posts_used column exists in users table
        try:
            result = await conn.execute(
                text("SELECT 1 FROM pragma_table_info('users') WHERE name='free_posts_used'")
            )
            column_exists = result.scalar() is not None
            
            if not column_exists:
                await conn.execute(
                    text("ALTER TABLE users ADD COLUMN free_posts_used INTEGER NOT NULL DEFAULT 0")
                )
                logger.info("Migration: Added free_posts_used column to users table")
        except Exception as e:
            logger.warning(f"Migration warning (free_posts_used): {e}")
        
        # Migration 2: Check if email column exists in users table
        try:
            result = await conn.execute(
                text("SELECT 1 FROM pragma_table_info('users') WHERE name='email'")
            )
            column_exists = result.scalar() is not None
            
            if not column_exists:
                await conn.execute(
                    text("ALTER TABLE users ADD COLUMN email TEXT DEFAULT NULL")
                )
                logger.info("Migration: Added email column to users table")
        except Exception as e:
            logger.warning(f"Migration warning (email): {e}")


async def main() -> None:
    """
    Async main function - entry point for the bot application.

    Initializes logging, database, creates bot and dispatcher, starts polling.
    Also starts Max bot listener for responding to messages in Max messenger.
    Telethon client runs in parallel for receiving channel updates.
    """
    # Initialize logger
    init_logger()
    logger.info("Max-Repost Bot starting...")

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
    logger.info("AutopostManager initialized")

    # Initialize YooKassa client
    yookassa_client = YooKassaClient()
    
    # Start payment checker background task
    payment_checker_task = asyncio.create_task(
        check_pending_payments(yookassa_client, bot)
    )
    logger.info("Payment checker started")

    # Start webhook server (runs alongside polling as fallback)
    webhook_task = None
    if settings.webhook_enabled:
        webhook_task = asyncio.create_task(
            start_webhook_server(
                host=settings.webhook_host,
                port=settings.webhook_port,
            )
        )
        logger.info("Webhook server started")

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
    logger.info("Max bot listener started")

    # Start polling and Telethon event loop in parallel
    try:
        await asyncio.gather(
            dp.start_polling(
                bot,
                allowed_updates=dp.resolve_used_update_types(),
            ),
            telethon_client.run_until_disconnected(),
        )
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Received shutdown signal")
    except Exception as e:
        logger.error(f"Main loop error: {e}", exc_info=True)
    finally:
        # Graceful shutdown
        logger.info("Shutting down...")
        
        # Stop all autopost tasks
        if autopost_manager:
            try:
                await asyncio.wait_for(autopost_manager.stop_all(), timeout=15.0)
            except asyncio.TimeoutError:
                logger.warning("Autopost manager stop timed out")
        
        # Stop Max listener
        try:
            await max_listener.stop()
            listener_task.cancel()
            await asyncio.wait_for(listener_task, timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("Max listener task stop timed out")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Max listener task error: {e}")
        
        # Cancel payment checker
        try:
            payment_checker_task.cancel()
            await asyncio.wait_for(payment_checker_task, timeout=5.0)
            logger.debug("Payment checker stopped")
        except asyncio.TimeoutError:
            logger.warning("Payment checker stop timed out")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Payment checker error: {e}")
        
        # Cancel webhook server
        if webhook_task:
            try:
                webhook_task.cancel()
                await asyncio.wait_for(webhook_task, timeout=5.0)
                logger.debug("Webhook server stopped")
            except asyncio.TimeoutError:
                logger.warning("Webhook server stop timed out")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Webhook server error: {e}")
        
        # Close Max API client session
        try:
            await max_client.close()
            logger.debug("Max API client closed")
        except Exception as e:
            logger.error(f"Error closing Max API client: {e}")
        
        # Disconnect Telethon client
        try:
            await telethon_client.close()
            logger.debug("Telethon client closed")
        except Exception as e:
            logger.error(f"Error closing Telethon client: {e}")
        
        logger.info("Max-Repost Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
