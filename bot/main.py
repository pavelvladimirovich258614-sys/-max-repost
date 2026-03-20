"""Main entry point for the Max-Repost Bot."""

import warnings
warnings.filterwarnings("ignore", message="Field.*model_custom_emoji_id.*")

import asyncio
import logging
import signal

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
from bot.payments.webhook_server import start_webhook_server, cleanup_webhook_server
from config.settings import settings

# Global shutdown event for coordinating graceful shutdown
shutdown_event = asyncio.Event()


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
        
        # Migration 3: Check if referral_code column exists in users table
        try:
            result = await conn.execute(
                text("SELECT 1 FROM pragma_table_info('users') WHERE name='referral_code'")
            )
            column_exists = result.scalar() is not None
            
            if not column_exists:
                await conn.execute(
                    text("ALTER TABLE users ADD COLUMN referral_code TEXT DEFAULT NULL")
                )
                logger.info("Migration: Added referral_code column to users table")
        except Exception as e:
            logger.warning(f"Migration warning (referral_code): {e}")
        
        # Migration 4: Check if referred_by column exists in users table
        try:
            result = await conn.execute(
                text("SELECT 1 FROM pragma_table_info('users') WHERE name='referred_by'")
            )
            column_exists = result.scalar() is not None
            
            if not column_exists:
                await conn.execute(
                    text("ALTER TABLE users ADD COLUMN referred_by INTEGER DEFAULT NULL")
                )
                logger.info("Migration: Added referred_by column to users table")
        except Exception as e:
            logger.warning(f"Migration warning (referred_by): {e}")


async def _graceful_shutdown_tasks(loop: asyncio.AbstractEventLoop, timeout: float = 10.0) -> None:
    """
    Gracefully shutdown all tasks with timeout.
    
    First gives tasks time to finish naturally, then cancels remaining ones.
    
    Args:
        loop: The asyncio event loop
        timeout: Total timeout in seconds for graceful shutdown
    """
    logger.info(f"Allowing tasks {timeout}s to finish gracefully...")
    
    # Get all tasks except current
    current_task = asyncio.current_task(loop)
    all_tasks = [task for task in asyncio.all_tasks(loop) if task is not current_task]
    
    if not all_tasks:
        logger.debug("No tasks to clean up")
        return
    
    # Wait for tasks to complete naturally (give them a chance to respond to shutdown_event)
    try:
        await asyncio.wait_for(
            asyncio.gather(*all_tasks, return_exceptions=True),
            timeout=timeout
        )
        logger.info("All tasks finished gracefully")
    except asyncio.TimeoutError:
        logger.warning(f"Timeout after {timeout}s, cancelling remaining tasks...")
        # Cancel any remaining tasks
        for task in all_tasks:
            if not task.done():
                task.cancel()
        # Wait briefly for cancellations to complete
        try:
            await asyncio.wait_for(
                asyncio.gather(*[t for t in all_tasks if not t.done()], return_exceptions=True),
                timeout=2.0
            )
        except asyncio.TimeoutError:
            logger.warning("Some tasks did not cancel in time")


def setup_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    """
    Set up signal handlers for graceful shutdown on SIGTERM and SIGINT.

    Args:
        loop: The asyncio event loop to use for signal handling
    """
    def handle_signal(sig: signal.Signals) -> None:
        logger.info(f"Received signal {sig.name}, initiating graceful shutdown...")
        shutdown_event.set()
        # Schedule graceful shutdown in the event loop
        asyncio.create_task(_graceful_shutdown_tasks(loop, timeout=10.0))

    try:
        loop.add_signal_handler(signal.SIGTERM, lambda: handle_signal(signal.SIGTERM))
        loop.add_signal_handler(signal.SIGINT, lambda: handle_signal(signal.SIGINT))
        logger.info("Signal handlers registered (SIGTERM, SIGINT)")
    except NotImplementedError:
        # Windows doesn't support add_signal_handler, but we're targeting Linux VPS
        logger.warning("Signal handlers not supported on this platform")


async def _load_autopost_subscriptions(autopost_manager) -> None:
    """
    Load autopost subscriptions in background without blocking bot startup.

    Each channel is started with a 2-second delay to avoid API spam.
    Failed channels don't block other channels from loading.
    """
    async with get_session() as session:
        repo = AutopostSubscriptionRepository(session)
        active_subs = await repo.get_active_subscriptions()

        if not active_subs:
            logger.info("Autopost: no active subscriptions to load")
            return

        channels_list = [f"@{s.tg_channel}" for s in active_subs]
        logger.info(f"Autopost: loading {len(active_subs)} active subscriptions in background: {', '.join(channels_list)}")

        loaded_count = 0
        for i, sub in enumerate(active_subs):
            # Add delay between channels to avoid API spam
            if i > 0:
                await asyncio.sleep(2)

            try:
                success = await autopost_manager.start_monitoring(sub)
                if success:
                    loaded_count += 1
            except Exception as e:
                logger.error(f"Failed to start autopost for @{sub.tg_channel}: {e}")

        logger.info(f"Autopost: successfully loaded {loaded_count}/{len(active_subs)} subscriptions")


async def _init_telethon_and_autopost(
    telethon_client: TelethonChannelClient,
    max_client: MaxClient,
    bot,
) -> TelethonChannelClient | None:
    """
    Initialize Telethon client and autopost manager in background.

    This runs after aiogram polling has already started, so the bot
    remains responsive even if Telethon takes time to connect.

    Args:
        telethon_client: Telethon client instance
        max_client: Max API client instance
        bot: Aiogram bot instance

    Returns:
        Telethon client if successful, None if failed
    """
    try:
        logger.info("Telethon: starting background initialization...")
        await telethon_client._get_client()
        logger.info("Telethon: connected successfully")
    except (Exception, RuntimeError) as e:
        logger.error(f"Telethon: failed to initialize - {e}")
        logger.warning("Bot continues WITHOUT Telethon - autopost and transfer features disabled")
        return None

    # Telethon connected - initialize autopost
    autopost_manager = AutopostManager(telethon_client, max_client, bot)
    set_autopost_manager(autopost_manager)
    logger.info("AutopostManager initialized")

    # Load subscriptions in background
    asyncio.create_task(_load_autopost_subscriptions(autopost_manager))

    return telethon_client


async def main() -> None:
    """
    Async main function - entry point for the bot application.

    CRITICAL: aiogram polling starts FIRST, before Telethon initialization.
    This ensures bot remains responsive even if Telethon takes time to connect.
    """
    # Get the event loop and set up signal handlers early
    loop = asyncio.get_running_loop()
    setup_signal_handlers(loop)

    # Initialize logger
    init_logger()
    logger.info("Max-Repost Bot starting...")

    # Initialize database (create tables)
    await init_db()

    # Initialize bot and dispatcher
    bot, dp = await init_bot()

    # Initialize Max API client (needed for autopost)
    max_client = MaxClient(settings.max_access_token)

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

    # Start Max bot listener (responds to messages in Max messenger)
    max_listener = MaxBotListener(settings.max_access_token)
    listener_task = asyncio.create_task(max_listener.start())
    logger.info("Max bot listener started")

    # ============================================================
    # CRITICAL: Start aiogram polling FIRST - bot becomes responsive
    # ============================================================
    polling_task = asyncio.create_task(
        dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    )
    logger.info("Aiogram polling started - bot is now responsive")

    # ============================================================
    # Initialize Telethon in BACKGROUND (after polling started)
    # ============================================================
    telethon_client = TelethonChannelClient(
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
        phone=settings.telegram_phone,
        session_string=settings.telethon_session_string,
        proxy_url=settings.socks_proxy,
    )

    # Start Telethon initialization in background - doesn't block polling
    telethon_task = asyncio.create_task(
        _init_telethon_and_autopost(telethon_client, max_client, bot)
    )

    # Store tasks for cleanup
    all_tasks = [payment_checker_task, listener_task]
    if webhook_task:
        all_tasks.append(webhook_task)

    # ============================================================
    # Main loop: wait for polling task
    # ============================================================
    telethon_connected = None

    try:
        # Wait a bit for Telethon to initialize (non-blocking)
        try:
            telethon_connected = await asyncio.wait_for(telethon_task, timeout=5.0)
            if telethon_connected:
                logger.info("Telethon connected, bot has full functionality")
        except asyncio.TimeoutError:
            logger.info("Telethon still connecting in background...")

        # Main loop - wait for polling (or polling + telethon)
        if telethon_connected:
            # Both polling and Telethon running - wait for both
            await asyncio.gather(
                polling_task,
                telethon_connected.run_until_disconnected(),
            )
        else:
            # Only polling running - Telethon may still be connecting in background
            # or failed completely
            await polling_task

    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Received shutdown signal")
    except Exception as e:
        logger.error(f"Main loop error: {e}", exc_info=True)
    finally:
        # Graceful shutdown
        logger.info("Shutting down...")

        # Get autopost manager for cleanup (may be None if Telethon failed)
        from bot.core.autopost import get_autopost_manager
        autopost_manager = get_autopost_manager()

        # Stop all autopost tasks
        if autopost_manager:
            try:
                await asyncio.wait_for(autopost_manager.stop_all(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Autopost manager stop timed out")

        # Stop Max listener
        try:
            await max_listener.stop()
            listener_task.cancel()
            await asyncio.wait_for(listener_task, timeout=2.0)
        except asyncio.TimeoutError:
            logger.warning("Max listener task stop timed out")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Max listener task error: {e}")

        # Cancel payment checker
        try:
            payment_checker_task.cancel()
            await asyncio.wait_for(payment_checker_task, timeout=2.0)
            logger.debug("Payment checker stopped")
        except asyncio.TimeoutError:
            logger.warning("Payment checker stop timed out")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Payment checker error: {e}")

        # Cancel webhook server and cleanup runner
        if webhook_task:
            try:
                # First cleanup the runner (stops the server properly)
                await cleanup_webhook_server()
                # Then cancel the task
                webhook_task.cancel()
                await asyncio.wait_for(webhook_task, timeout=2.0)
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

        # Disconnect Telethon client (if connected)
        if telethon_connected:
            try:
                await telethon_connected.close()
                logger.debug("Telethon client closed")
            except Exception as e:
                logger.error(f"Error closing Telethon client: {e}")

        logger.info("Max-Repost Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
