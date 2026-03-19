"""Autoposting manager for automatic forwarding of new posts from TG to Max."""

from __future__ import annotations

import asyncio
import io
import os
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger
from telethon.tl.types import Message

from bot.core.transfer_engine import convert_entities_to_html
from bot.max_api.client import MaxClient
from bot.database.balance import get_balance, charge_autopost_with_subscription
from bot.database.repositories.autopost_subscription import AutopostSubscriptionRepository
from bot.database.repositories.balance import UserBalanceRepository
from bot.database.connection import get_session
from config.settings import settings

# Singleton instance storage
_autopost_manager: AutopostManager | None = None


def set_autopost_manager(manager: AutopostManager) -> None:
    """Set the global autopost manager singleton instance."""
    global _autopost_manager
    _autopost_manager = manager


def get_autopost_manager() -> AutopostManager | None:
    """Get the global autopost manager singleton instance."""
    return _autopost_manager


class AutopostManager:
    """
    Manages autoposting for all active channels.
    
    Uses polling to check for new messages in TG channels
    and automatically forwards them to Max channels.
    Polling is more reliable than event handlers for channels
    where the user is the author.
    """
    
    # Polling interval in seconds
    POLL_INTERVAL = 10
    # Error retry interval in seconds
    ERROR_INTERVAL = 30
    # Album buffer wait time in seconds (time to collect all album parts)
    ALBUM_BUFFER_WAIT = 2
    
    # Temp directory for downloads
    TEMP_DIR = "/tmp/max_repost"

    def __init__(self, telethon_client, max_client: MaxClient, bot=None):
        """
        Initialize the autopost manager.
        
        Args:
            telethon_client: TelethonChannelClient instance
            max_client: MaxClient instance for sending to Max
            bot: Aiogram bot instance for sending notifications (optional)
        """
        self.telethon_client = telethon_client
        self.max_client = max_client
        self.bot = bot
        # tg_channel -> {max_chat_id, task, user_id, subscription}
        self.active_tasks: dict[str, dict] = {}
        # Album buffer: grouped_id -> {messages, timer_task, max_chat_id, user_id, tg_channel, subscription}
        self._album_buffer: dict[str, dict] = {}
        
        # Create temp directory for downloads
        os.makedirs(self.TEMP_DIR, exist_ok=True)
        
        # Track last low balance notification time per user to prevent spam
        self._low_balance_notified: dict[int, datetime] = {}
    
    async def _check_and_notify_low_balance(self, user_id: int) -> None:
        """
        Check user's balance and send low balance notifications if needed.
        
        To prevent spam, notifications are sent at most once per day per user.
        
        Args:
            user_id: Telegram user ID
        """
        # Skip if bot instance is not available
        if self.bot is None:
            return
        
        # Check if already notified today
        last_notify = self._low_balance_notified.get(user_id)
        if last_notify and (datetime.now() - last_notify) < timedelta(days=1):
            return  # Already notified today
        
        try:
            async with get_session() as session:
                balance_repo = UserBalanceRepository(session)
                new_balance = await balance_repo.get_balance(user_id)
            
            # Convert Decimal to float for comparison
            balance_float = float(new_balance)
            
            if balance_float <= 0:
                # Zero balance - autoposting will be paused by other logic
                message_text = (
                    "🚫 Баланс: 0₽. Автопостинг приостановлен.\n"
                    "Пополните баланс для возобновления."
                )
                
                keyboard = self._get_deposit_keyboard()
                
                await self.bot.send_message(
                    chat_id=user_id,
                    text=message_text,
                    reply_markup=keyboard,
                )
                
                self._low_balance_notified[user_id] = datetime.now()
                logger.info(f"Sent zero balance notification to user {user_id}")
                
            elif balance_float <= 10:
                # Low balance warning (1-10₽)
                estimated_posts = int(balance_float / 3)
                
                message_text = (
                    f"⚠️ Ваш баланс: {int(balance_float)}₽\n"
                    f"Осталось примерно {estimated_posts} постов.\n"
                    "Пополните баланс, чтобы автопостинг не остановился."
                )
                
                keyboard = self._get_deposit_keyboard()
                
                await self.bot.send_message(
                    chat_id=user_id,
                    text=message_text,
                    reply_markup=keyboard,
                )
                
                self._low_balance_notified[user_id] = datetime.now()
                logger.info(f"Sent low balance notification to user {user_id} (balance: {balance_float}₽)")
                
        except Exception as e:
            # Don't fail the post transfer if notification fails
            logger.error(f"Failed to send low balance notification to user {user_id}: {e}")
    
    def _get_deposit_keyboard(self) -> InlineKeyboardMarkup:
        """Get keyboard with deposit button for low balance notifications."""
        builder = InlineKeyboardBuilder()
        builder.button(text="💰 Пополнить", callback_data="balance_deposit")
        return builder.as_markup()
    
    def _should_skip_autopost(self, message: Message) -> tuple[bool, str]:
        """Check if message should be skipped for autopost.
        
        Unlike bulk transfer, autopost forwards ALL messages including short ones.
        Only skip service messages and empty messages.
        """
        # Skip service messages (channel actions, etc.)
        if message.action:
            return True, "service_message"
        
        # Skip empty messages (no text and no media)
        if not message.raw_text and not message.photo and not message.video \
           and not message.audio and not message.voice and not message.document:
            return True, "empty_message"
        
        return False, ""
    
    async def start_autopost(
        self,
        tg_channel: str,
        max_chat_id: int,
        user_id: int,
        subscription: object | None = None,
    ) -> bool:
        """
        Start autoposting for a channel.
        
        Args:
            tg_channel: Telegram channel username (with or without @)
            max_chat_id: Max channel chat_id
            user_id: Telegram user ID who owns this autopost
            subscription: Optional AutopostSubscription object for catch-up logic
            
        Returns:
            True if started successfully, False otherwise
        """
        # Normalize channel name
        if tg_channel.startswith('@'):
            tg_channel = tg_channel[1:]
        
        # Check if already active
        if tg_channel in self.active_tasks:
            logger.info(f"Autopost already active for {tg_channel}")
            return True
        
        try:
            client = await self.telethon_client._get_client()
            entity = await client.get_entity(tg_channel)
            
            # Catch-up logic: process missed posts before starting polling
            # Returns the updated last_post_id after catch-up
            initial_last_post_id = None
            try:
                initial_last_post_id = await self._catch_up_missed_posts(
                    client, entity, tg_channel, max_chat_id, user_id, subscription
                )
            except Exception as e:
                logger.error(f"Catch-up failed for {tg_channel}: {e}")
                # Continue with monitoring even if catch-up fails
            
            # Start polling task with the updated last_post_id from catch-up
            # This prevents duplicate processing of posts handled during catch-up
            task = asyncio.create_task(
                self._monitor_channel_polling(
                    tg_channel, max_chat_id, user_id, subscription, initial_last_post_id
                )
            )
            
            # Store task info
            self.active_tasks[tg_channel] = {
                "max_chat_id": max_chat_id,
                "user_id": user_id,
                "task": task,
                "subscription": subscription,
            }
            
            logger.info(f"Autopost polling started: {tg_channel} -> {max_chat_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start autopost for {tg_channel}: {e}")
            return False
    
    async def _monitor_channel_polling(
        self,
        tg_channel: str,
        max_chat_id: int,
        user_id: int,
        subscription: object | None,
        initial_last_post_id: int | None = None,
    ) -> None:
        """
        Monitor channel for new posts via polling.
        
        Polls the channel every POLL_INTERVAL seconds to check for new messages.
        More reliable than event handlers for channels where user is author.
        
        Args:
            tg_channel: Telegram channel username (without @)
            max_chat_id: Max channel chat_id
            user_id: Telegram user ID who owns this autopost
            subscription: Optional AutopostSubscription object
            initial_last_post_id: Optional initial last_post_id from catch-up
                                  (prevents duplicate processing on startup)
        """
        # Get initial last_post_id from catch-up result or subscription
        # initial_last_post_id is preferred as it's updated after catch-up processing
        if initial_last_post_id is not None:
            last_post_id = initial_last_post_id
        else:
            last_post_id = getattr(subscription, 'last_post_id', None) or 0
        
        subscription_id = getattr(subscription, 'id', None)
        
        logger.info(
            f"Autopost polling task started: @{tg_channel}, "
            f"last_post_id={last_post_id}, interval={self.POLL_INTERVAL}s"
        )
        
        # Track recently processed message IDs to prevent duplicates
        # This protects against edge cases where the same message might be processed twice
        processed_ids: set[int] = set()
        
        try:
            while True:
                try:
                    client = await self.telethon_client._get_client()
                    new_messages = []
                    
                    # Get messages newer than last_post_id
                    # iter_messages with min_id returns messages with id > min_id
                    try:
                        async for msg in client.iter_messages(
                            tg_channel,
                            min_id=last_post_id,
                            limit=50,
                        ):
                            # Strict check: msg.id must be greater than last_post_id
                            # and not in processed_ids (extra protection against duplicates)
                            if msg.id > last_post_id and msg.id not in processed_ids:
                                new_messages.append(msg)
                    except (ConnectionError, asyncio.TimeoutError, OSError) as e:
                        logger.warning(f"Connection lost in polling for @{tg_channel}, will retry: {e}")
                        await asyncio.sleep(self.ERROR_INTERVAL)
                        continue  # Don't stop polling, continue to next iteration
                    
                    if new_messages:
                        # Sort by ID ascending (oldest first) to process in order
                        new_messages.sort(key=lambda m: m.id)
                        
                        logger.info(
                            f"Autopost: found {len(new_messages)} new message(s) in @{tg_channel}"
                        )
                        
                        for msg in new_messages:
                            logger.info(
                                f"Autopost: processing post in @{tg_channel}, "
                                f"id={msg.id}, text={msg.text[:50] if msg.text else '[media]'!r}"
                            )
                            
                            # Check if message is part of an album
                            if msg.grouped_id:
                                # Add to album buffer - it will be processed when buffer is flushed
                                await self._buffer_album_message(
                                    msg, max_chat_id, user_id, tg_channel, subscription
                                )
                                # Update tracking but don't process individually
                                last_post_id = msg.id
                                processed_ids.add(msg.id)
                                
                                # Limit the size of processed_ids set to prevent memory growth
                                if len(processed_ids) > 1000:
                                    processed_ids = set(sorted(processed_ids)[-500:])
                                
                                if subscription_id:
                                    await self._update_last_post_id(subscription_id, last_post_id)
                                continue
                            
                            # Forward single (non-album) post
                            success = await self._forward_post(
                                msg, max_chat_id, user_id, tg_channel, subscription
                            )
                            
                            # Update last_post_id and track processed IDs on success
                            if success:
                                last_post_id = msg.id
                                processed_ids.add(msg.id)
                                
                                # Limit the size of processed_ids set to prevent memory growth
                                if len(processed_ids) > 1000:
                                    processed_ids = set(sorted(processed_ids)[-500:])
                                
                                if subscription_id:
                                    await self._update_last_post_id(subscription_id, last_post_id)
                    
                    # Wait before next poll
                    await asyncio.sleep(self.POLL_INTERVAL)
                    
                except asyncio.CancelledError:
                    logger.info(f"Autopost polling cancelled: @{tg_channel}")
                    raise
                except (ConnectionError, asyncio.TimeoutError, OSError) as e:
                    logger.warning(f"Connection lost in polling for @{tg_channel}, will retry: {e}")
                    await asyncio.sleep(self.ERROR_INTERVAL)
                    continue  # Don't stop polling, continue to next iteration
                except Exception as e:
                    logger.error(
                        f"Autopost polling error for @{tg_channel}: {e}",
                        exc_info=True
                    )
                    # Wait longer on error before retry
                    await asyncio.sleep(self.ERROR_INTERVAL)
                    
        except asyncio.CancelledError:
            logger.info(f"Autopost polling stopped: @{tg_channel}")
    
    async def _buffer_album_message(
        self,
        message: Message,
        max_chat_id: int,
        user_id: int,
        tg_channel: str,
        subscription: object | None,
    ) -> None:
        """
        Buffer an album message and start/reset the flush timer.
        
        Args:
            message: Telethon Message object (part of an album)
            max_chat_id: Max channel chat_id
            user_id: Telegram user ID
            tg_channel: Telegram channel username
            subscription: Optional AutopostSubscription object
        """
        grouped_id = str(message.grouped_id)
        
        if grouped_id not in self._album_buffer:
            # Create new buffer entry with timer task
            self._album_buffer[grouped_id] = {
                "messages": [message],
                "max_chat_id": max_chat_id,
                "user_id": user_id,
                "tg_channel": tg_channel,
                "subscription": subscription,
                "timer_task": asyncio.create_task(
                    self._flush_album_buffer_after_delay(grouped_id)
                ),
            }
            logger.info(
                f"Album buffer created for grouped_id={grouped_id}, "
                f"message_id={message.id}"
            )
        else:
            # Add to existing buffer
            self._album_buffer[grouped_id]["messages"].append(message)
            logger.info(
                f"Added message {message.id} to album buffer "
                f"grouped_id={grouped_id}, total parts={len(self._album_buffer[grouped_id]['messages'])}"
            )
    
    async def _flush_album_buffer_after_delay(self, grouped_id: str) -> None:
        """
        Wait for album buffer delay then flush the album.
        
        Args:
            grouped_id: The grouped_id of the album to flush
        """
        try:
            await asyncio.sleep(self.ALBUM_BUFFER_WAIT)
            await self._flush_album_buffer(grouped_id)
        except asyncio.CancelledError:
            # Timer was cancelled, buffer will be handled elsewhere
            pass
    
    async def _flush_album_buffer(self, grouped_id: str) -> None:
        """
        Flush an album buffer and forward all messages as a group.
        
        Args:
            grouped_id: The grouped_id of the album to flush
        """
        if grouped_id not in self._album_buffer:
            return
        
        buffer_data = self._album_buffer.pop(grouped_id)
        messages = buffer_data["messages"]
        max_chat_id = buffer_data["max_chat_id"]
        user_id = buffer_data["user_id"]
        tg_channel = buffer_data["tg_channel"]
        subscription = buffer_data["subscription"]
        
        # Cancel timer task if it's still running
        timer_task = buffer_data.get("timer_task")
        if timer_task and not timer_task.done():
            timer_task.cancel()
            try:
                await timer_task
            except asyncio.CancelledError:
                pass
        
        if not messages:
            return
        
        # Sort messages by ID to maintain order
        messages.sort(key=lambda m: m.id)
        
        logger.info(
            f"Flushing album buffer: grouped_id={grouped_id}, "
            f"{len(messages)} messages, channel=@{tg_channel}"
        )
        
        # Forward the album as a group
        await self._forward_album(
            messages, max_chat_id, user_id, tg_channel, subscription
        )
    
    async def _forward_album(
        self,
        messages: list[Message],
        max_chat_id: int,
        user_id: int,
        tg_channel: str,
        subscription: object | None,
    ) -> bool:
        """
        Forward an album (group of media messages) to Max.
        
        Args:
            messages: List of Telethon Message objects belonging to the album
            max_chat_id: Max channel chat_id
            user_id: Telegram user ID
            tg_channel: Telegram channel username
            subscription: Optional AutopostSubscription object
            
        Returns:
            True if forwarded successfully, False otherwise
        """
        if not messages:
            return True
        
        # Use the first message for text/caption and skip checks
        first_message = messages[0]
        
        # 1. Filter service and empty messages
        should_skip, skip_reason = self._should_skip_autopost(first_message)
        if should_skip:
            logger.info(f"Autopost: @{tg_channel} album skipped - {skip_reason}")
            return True
        
        # 2. Check if user is admin (admins have unlimited access)
        is_admin = user_id in settings.ADMIN_IDS
        
        # 3. If not admin - check and charge balance (charge once for the album)
        if not is_admin:
            async with get_session() as session:
                success, error = await charge_autopost_with_subscription(
                    session=session,
                    user_id=user_id,
                    tg_channel=tg_channel,
                    post_id=first_message.id
                )
                if not success:
                    if error == "insufficient_funds":
                        await self._notify_insufficient_funds(user_id, tg_channel)
                        await self.pause_subscription(user_id, tg_channel, "insufficient_funds")
                    return False
                
                # Check for low balance after successful charge
                await self._check_and_notify_low_balance(user_id)
        
        # 4. Forward the album
        try:
            await self._do_forward_album(messages, max_chat_id)
        except Exception as e:
            logger.error(f"Autopost: failed to forward album from @{tg_channel}: {e}")
            return False
        
        # 5. Log success
        logger.info(f"Autopost: @{tg_channel} album with {len(messages)} parts transferred")
        return True
    
    async def _do_forward_album(self, messages: list[Message], max_chat_id: int) -> None:
        """
        Execute the actual forwarding of an album to Max.
        
        Downloads all media files and sends them as a group.
        
        Args:
            messages: List of Telethon Message objects belonging to the album
            max_chat_id: Max channel chat_id
        """
        if not messages:
            return
        
        # Get text/caption from the first message (albums have caption only on first message)
        first_message = messages[0]
        text = first_message.raw_text or ""
        format_type = None
        
        if first_message.entities:
            text = convert_entities_to_html(text, first_message.entities)
            format_type = "html"
        
        # Collect all media attachments
        attachments = []
        
        for message in messages:
            try:
                if message.photo:
                    attachment = await self._download_and_prepare_photo(message)
                    if attachment:
                        attachments.append(attachment)
                elif message.video:
                    attachment = await self._download_and_prepare_video(message)
                    if attachment:
                        attachments.append(attachment)
                elif message.audio or message.voice:
                    attachment = await self._download_and_prepare_audio(message)
                    if attachment:
                        attachments.append(attachment)
                elif message.document:
                    attachment = await self._download_and_prepare_document(message)
                    if attachment:
                        attachments.append(attachment)
            except Exception as e:
                logger.error(f"Failed to prepare media from message {message.id}: {e}")
                continue
        
        if not attachments:
            # No media could be prepared, fall back to text-only
            if text:
                await self.max_client.send_message(
                    chat_id=max_chat_id,
                    text=text,
                    format=format_type,
                )
            return
        
        try:
            # Send all attachments as a group
            await self.max_client.send_message(
                chat_id=max_chat_id,
                text=text,
                attachments=attachments,
                format=format_type,
            )
        except Exception as e:
            logger.error(f"Failed to send album: {e}")
            # Fallback: try sending attachments one by one
            for attachment in attachments:
                try:
                    await self.max_client.send_message(
                        chat_id=max_chat_id,
                        text=text if attachment == attachments[0] else "",
                        attachments=[attachment],
                        format=format_type,
                    )
                except Exception as e2:
                    logger.error(f"Failed to send individual attachment: {e2}")
    
    async def _download_and_prepare_photo(self, message: Message) -> dict | None:
        """Download and prepare a photo attachment for sending."""
        buf = io.BytesIO()
        await message.download_media(file=buf)
        buf.seek(0)
        photo_bytes = buf.read()
        
        if not photo_bytes:
            logger.warning(f"Empty photo bytes for message {message.id}")
            return None
        
        try:
            token = await self.max_client.upload_image(photo_bytes)
            return {"type": "image", "payload": {"token": token}}
        except Exception as e:
            logger.error(f"Failed to upload photo from message {message.id}: {e}")
            return None
    
    async def _download_and_prepare_video(self, message: Message) -> dict | None:
        """Download and prepare a video attachment for sending."""
        path = None
        try:
            # Download to temp file
            path = await message.download_media(file=self.TEMP_DIR)
            if not path:
                logger.warning(f"Failed to download video for message {message.id}")
                return None
            
            # Upload to Max
            token = await self.max_client.upload_video(path)
            logger.info(f"Prepared video from album: {path}")
            return {"type": "video", "payload": {"token": token}}
        except Exception as e:
            logger.error(f"Failed to upload video from message {message.id}: {e}")
            return None
        finally:
            if path and os.path.exists(path):
                os.remove(path)
                logger.debug(f"Cleaned up: {path}")
    
    async def _download_and_prepare_audio(self, message: Message) -> dict | None:
        """Download and prepare an audio/voice attachment for sending."""
        path = None
        try:
            # Download to temp file
            path = await message.download_media(file=self.TEMP_DIR)
            if not path:
                logger.warning(f"Failed to download audio for message {message.id}")
                return None
            
            # Upload to Max
            token = await self.max_client.upload_audio(path)
            logger.info(f"Prepared audio from album: {path}")
            return {"type": "audio", "payload": {"token": token}}
        except Exception as e:
            logger.error(f"Failed to upload audio from message {message.id}: {e}")
            return None
        finally:
            if path and os.path.exists(path):
                os.remove(path)
                logger.debug(f"Cleaned up: {path}")
    
    async def _download_and_prepare_document(self, message: Message) -> dict | None:
        """Download and prepare a document/file attachment for sending."""
        path = None
        try:
            # Download to temp file
            path = await message.download_media(file=self.TEMP_DIR)
            if not path:
                logger.warning(f"Failed to download document for message {message.id}")
                return None
            
            # Upload to Max
            token = await self.max_client.upload_file(path)
            logger.info(f"Prepared document from album: {path}")
            return {"type": "file", "payload": {"token": token}}
        except Exception as e:
            logger.error(f"Failed to upload file from message {message.id}: {e}")
            return None
        finally:
            if path and os.path.exists(path):
                os.remove(path)
                logger.debug(f"Cleaned up: {path}")
    
    async def _update_last_post_id(self, subscription_id: int, post_id: int) -> None:
        """Update last_post_id in database."""
        try:
            async with get_session() as session:
                repo = AutopostSubscriptionRepository(session)
                await repo.update_last_post_id(subscription_id, post_id)
        except Exception as e:
            logger.error(f"Failed to update last_post_id: {e}")
    
    async def _catch_up_missed_posts(
        self,
        client,
        entity,
        tg_channel: str,
        max_chat_id: int,
        user_id: int,
        subscription: object | None,
    ) -> int | None:
        """
        Catch up missed posts that were published while autopost was paused.
        
        Args:
            client: Telethon client
            entity: Telegram channel entity
            tg_channel: Telegram channel username (without @)
            max_chat_id: Max channel chat_id
            user_id: Telegram user ID
            subscription: AutopostSubscription object with last_post_id
            
        Returns:
            The last processed post_id (for passing to polling task to prevent duplicates),
            or None if no subscription/no posts processed
        """
        if subscription is None:
            logger.debug(f"No subscription provided for {tg_channel}, skipping catch-up")
            return None
        
        subscription_id = getattr(subscription, 'id', None)
        last_post_id = getattr(subscription, 'last_post_id', None)
        new_last_post_id = last_post_id
        
        if last_post_id is not None:
            # Get recent messages and filter missed ones
            missed_messages = []
            async for message in client.iter_messages(entity, limit=50):
                if message.id > last_post_id:
                    missed_messages.append(message)
            
            if not missed_messages:
                logger.info(f"No missed posts for @{tg_channel}")
                return last_post_id
            
            # Sort by ID ascending (oldest first) to process in order
            missed_messages.sort(key=lambda m: m.id)
            
            # Group messages by album (grouped_id)
            album_groups: dict[int, list[Message]] = {}
            single_messages: list[Message] = []
            
            for message in missed_messages:
                if message.grouped_id:
                    grouped_id = message.grouped_id
                    if grouped_id not in album_groups:
                        album_groups[grouped_id] = []
                    album_groups[grouped_id].append(message)
                else:
                    single_messages.append(message)
            
            # Process all messages in order
            transferred_count = 0
            is_admin = user_id in settings.ADMIN_IDS
            
            # Create a combined list with markers for processing order
            # We need to process messages in ID order, sending albums as groups
            all_messages = missed_messages.copy()
            processed_grouped_ids: set[int] = set()
            
            for message in all_messages:
                # Check if message is part of an album
                if message.grouped_id:
                    grouped_id = message.grouped_id
                    
                    # Skip if this album was already processed
                    if grouped_id in processed_grouped_ids:
                        # Still update last_post_id for album parts
                        new_last_post_id = message.id
                        if subscription_id:
                            async with get_session() as session:
                                repo = AutopostSubscriptionRepository(session)
                                await repo.update_last_post_id(subscription_id, message.id)
                        continue
                    
                    # Get all messages in this album
                    album_messages = album_groups.get(grouped_id, [message])
                    album_messages.sort(key=lambda m: m.id)
                    
                    # Check if should skip the album (check first message)
                    should_skip, skip_reason = self._should_skip_autopost(album_messages[0])
                    if should_skip:
                        logger.debug(f"Catch-up: skipping album with message {message.id} - {skip_reason}")
                        # Update last_post_id for all album parts
                        for album_msg in album_messages:
                            new_last_post_id = album_msg.id
                            if subscription_id:
                                async with get_session() as session:
                                    repo = AutopostSubscriptionRepository(session)
                                    await repo.update_last_post_id(subscription_id, album_msg.id)
                        processed_grouped_ids.add(grouped_id)
                        continue
                    
                    # Check balance if not admin (charge once per album)
                    if not is_admin:
                        async with get_session() as session:
                            success, error = await charge_autopost_with_subscription(
                                session=session,
                                user_id=user_id,
                                tg_channel=tg_channel,
                                post_id=album_messages[0].id
                            )
                            if not success:
                                if error == "insufficient_funds":
                                    await self._notify_insufficient_funds(user_id, tg_channel)
                                    await self.pause_subscription(user_id, tg_channel, "insufficient_funds")
                                logger.warning(f"Catch-up: failed to charge for album {album_messages[0].id}, stopping")
                                # Update last_post_id before stopping
                                for album_msg in album_messages:
                                    new_last_post_id = album_msg.id
                                return new_last_post_id
                            
                            # Check for low balance after successful charge
                            await self._check_and_notify_low_balance(user_id)
                    
                    # Forward the album
                    try:
                        await self._do_forward_album(album_messages, max_chat_id)
                        transferred_count += 1
                        
                        # Update last_post_id for all album parts
                        for album_msg in album_messages:
                            new_last_post_id = album_msg.id
                            if subscription_id:
                                async with get_session() as session:
                                    repo = AutopostSubscriptionRepository(session)
                                    await repo.update_last_post_id(subscription_id, album_msg.id)
                    except Exception as e:
                        logger.error(f"Catch-up: failed to forward album with message {message.id}: {e}")
                        # Continue processing other messages
                    
                    processed_grouped_ids.add(grouped_id)
                    continue
                
                # Process single message
                # Check if should skip
                should_skip, skip_reason = self._should_skip_autopost(message)
                if should_skip:
                    logger.debug(f"Catch-up: skipping message {message.id} - {skip_reason}")
                    new_last_post_id = message.id
                    if subscription_id:
                        async with get_session() as session:
                            repo = AutopostSubscriptionRepository(session)
                            await repo.update_last_post_id(subscription_id, message.id)
                    continue
                
                # Check balance if not admin
                if not is_admin:
                    async with get_session() as session:
                        success, error = await charge_autopost_with_subscription(
                            session=session,
                            user_id=user_id,
                            tg_channel=tg_channel,
                            post_id=message.id
                        )
                        if not success:
                            if error == "insufficient_funds":
                                await self._notify_insufficient_funds(user_id, tg_channel)
                                await self.pause_subscription(user_id, tg_channel, "insufficient_funds")
                            logger.warning(f"Catch-up: failed to charge for message {message.id}, stopping")
                            return new_last_post_id
                        
                        # Check for low balance after successful charge
                        await self._check_and_notify_low_balance(user_id)
                
                # Forward the post
                try:
                    await self._do_forward(message, max_chat_id)
                    transferred_count += 1
                    new_last_post_id = message.id
                    
                    # Update last_post_id in database
                    if subscription_id:
                        async with get_session() as session:
                            repo = AutopostSubscriptionRepository(session)
                            await repo.update_last_post_id(subscription_id, message.id)
                except Exception as e:
                    logger.error(f"Catch-up: failed to forward message {message.id}: {e}")
                    continue
            
            logger.info(f"Catch-up: transferred {transferred_count} missed posts for @{tg_channel}")
            return new_last_post_id
        else:
            # First start: get the latest message ID and set it as last_post_id
            latest_message = None
            async for message in client.iter_messages(entity, limit=1):
                latest_message = message
                break
            
            if latest_message and subscription_id:
                async with get_session() as session:
                    repo = AutopostSubscriptionRepository(session)
                    await repo.update_last_post_id(subscription_id, latest_message.id)
                logger.info(f"First start: setting last_post_id to {latest_message.id} for @{tg_channel}")
                return latest_message.id
            else:
                logger.info(f"First start: no messages found in @{tg_channel}")
                return None
    
    async def stop_autopost(self, tg_channel: str) -> bool:
        """
        Stop autoposting for a channel.
        
        Args:
            tg_channel: Telegram channel username (with or without @)
            
        Returns:
            True if stopped successfully, False otherwise
        """
        # Normalize channel name
        if tg_channel.startswith('@'):
            tg_channel = tg_channel[1:]
        
        if tg_channel not in self.active_tasks:
            logger.info(f"Autopost not active for {tg_channel}")
            return False
        
        try:
            task_info = self.active_tasks.pop(tg_channel)
            task = task_info.get("task")
            
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            
            # Flush any pending albums for this channel
            await self._flush_pending_albums_for_channel(tg_channel)
            
            logger.info(f"Autopost stopped: {tg_channel}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop autopost for {tg_channel}: {e}")
            return False
    
    async def _flush_pending_albums_for_channel(self, tg_channel: str) -> None:
        """
        Flush any pending album buffers for a specific channel.
        
        Args:
            tg_channel: Telegram channel username
        """
        grouped_ids_to_flush = []
        
        for grouped_id, buffer_data in self._album_buffer.items():
            if buffer_data["tg_channel"] == tg_channel:
                grouped_ids_to_flush.append(grouped_id)
        
        for grouped_id in grouped_ids_to_flush:
            logger.info(f"Flushing pending album buffer for channel @{tg_channel}: grouped_id={grouped_id}")
            await self._flush_album_buffer(grouped_id)
    
    async def _forward_post(
        self, 
        message: Message, 
        max_chat_id: int, 
        user_id: int, 
        tg_channel: str,
        subscription: object | None = None,
    ) -> bool:
        """
        Forward a single post to Max with balance check.
        
        Args:
            message: Telethon Message object
            max_chat_id: Max channel chat_id
            user_id: Telegram user ID
            tg_channel: Telegram channel username
            subscription: Optional AutopostSubscription object to update last_post_id
            
        Returns:
            True if forwarded successfully, False otherwise
        """
        # Log new message received
        logger.info(f"Autopost: new message in @{tg_channel}, id={message.id}")
        
        # 1. Filter service and empty messages
        should_skip, skip_reason = self._should_skip_autopost(message)
        if should_skip:
            logger.info(f"Autopost: @{tg_channel} post #{message.id} skipped - {skip_reason}")
            return True  # Return True so we don't retry
        
        # 2. Check if user is admin (admins have unlimited access)
        is_admin = user_id in settings.ADMIN_IDS
        
        # 3. If not admin - check and charge balance
        if not is_admin:
            async with get_session() as session:
                success, error = await charge_autopost_with_subscription(
                    session=session,
                    user_id=user_id,
                    tg_channel=tg_channel,
                    post_id=message.id
                )
                if not success:
                    # Insufficient funds - pause autoposting
                    if error == "insufficient_funds":
                        await self._notify_insufficient_funds(user_id, tg_channel)
                        await self.pause_subscription(user_id, tg_channel, "insufficient_funds")
                    return False
                
                # Check for low balance after successful charge
                await self._check_and_notify_low_balance(user_id)
        
        # 4. Forward the post
        try:
            await self._do_forward(message, max_chat_id)
        except Exception as e:
            logger.error(f"Autopost: failed to forward post #{message.id}: {e}")
            return False
        
        # 5. Log success
        logger.info(f"Autopost: @{tg_channel} post #{message.id} transferred")
        return True
    
    async def _do_forward(self, message: Message, max_chat_id: int) -> None:
        """
        Execute the actual forwarding of a post to Max.
        
        Args:
            message: Telethon Message object
            max_chat_id: Max channel chat_id
        """
        # Get text with formatting
        text = message.raw_text or ""
        format_type = None
        
        if message.entities:
            text = convert_entities_to_html(text, message.entities)
            format_type = "html"
        
        # Handle media
        if message.photo:
            await self._forward_photo(message, max_chat_id, text, format_type)
        elif message.video:
            await self._forward_video(message, max_chat_id, text, format_type)
        elif message.audio or message.voice:
            await self._forward_audio(message, max_chat_id, text, format_type)
        elif message.document:
            await self._forward_document(message, max_chat_id, text, format_type)
        elif text:
            # Text-only message
            await self.max_client.send_message(
                chat_id=max_chat_id,
                text=text,
                format=format_type,
            )
    
    async def _forward_photo(
        self,
        message: Message,
        max_chat_id: int,
        text: str,
        format_type: str | None,
    ) -> None:
        """Forward a photo post."""
        buf = io.BytesIO()
        await message.download_media(file=buf)
        buf.seek(0)
        photo_bytes = buf.read()
        
        if not photo_bytes:
            logger.warning(f"Empty photo bytes for message {message.id}")
            if text:
                await self.max_client.send_message(
                    chat_id=max_chat_id,
                    text=text,
                    format=format_type,
                )
            return
        
        try:
            token = await self.max_client.upload_image(photo_bytes)
            attachment = {"type": "image", "payload": {"token": token}}
            
            await self.max_client.send_message(
                chat_id=max_chat_id,
                text=text,
                attachments=[attachment],
                format=format_type,
            )
        except Exception as e:
            logger.error(f"Failed to upload/forward photo: {e}")
            # Fallback: send text only
            if text:
                await self.max_client.send_message(
                    chat_id=max_chat_id,
                    text=text,
                    format=format_type,
                )
    
    async def _forward_video(
        self,
        message: Message,
        max_chat_id: int,
        text: str,
        format_type: str | None,
    ) -> None:
        """Forward a video post."""
        path = None
        try:
            # Download to temp file
            path = await message.download_media(file=self.TEMP_DIR)
            if not path:
                logger.warning(f"Failed to download video for message {message.id}")
                if text:
                    await self.max_client.send_message(
                        chat_id=max_chat_id,
                        text=text,
                        format=format_type,
                    )
                return
            
            # Upload to Max
            token = await self.max_client.upload_video(path)
            attachment = {"type": "video", "payload": {"token": token}}
            
            await self.max_client.send_message(
                chat_id=max_chat_id,
                text=text,
                attachments=[attachment],
                format=format_type,
            )
            logger.info(f"Sent video to Max: {path}")
        except Exception as e:
            logger.error(f"Failed to upload/forward video: {e}")
            if text:
                await self.max_client.send_message(
                    chat_id=max_chat_id,
                    text=text,
                    format=format_type,
                )
        finally:
            if path and os.path.exists(path):
                os.remove(path)
                logger.debug(f"Cleaned up: {path}")
    
    async def _forward_audio(
        self,
        message: Message,
        max_chat_id: int,
        text: str,
        format_type: str | None,
    ) -> None:
        """Forward an audio/voice post."""
        path = None
        try:
            # Download to temp file
            path = await message.download_media(file=self.TEMP_DIR)
            if not path:
                logger.warning(f"Failed to download audio for message {message.id}")
                if text:
                    await self.max_client.send_message(
                        chat_id=max_chat_id,
                        text=text,
                        format=format_type,
                    )
                return
            
            # Upload to Max
            token = await self.max_client.upload_audio(path)
            attachment = {"type": "audio", "payload": {"token": token}}
            
            await self.max_client.send_message(
                chat_id=max_chat_id,
                text=text,
                attachments=[attachment],
                format=format_type,
            )
            logger.info(f"Sent audio to Max: {path}")
        except Exception as e:
            logger.error(f"Failed to upload/forward audio: {e}")
            if text:
                await self.max_client.send_message(
                    chat_id=max_chat_id,
                    text=text,
                    format=format_type,
                )
        finally:
            if path and os.path.exists(path):
                os.remove(path)
                logger.debug(f"Cleaned up: {path}")
    
    async def _forward_document(
        self,
        message: Message,
        max_chat_id: int,
        text: str,
        format_type: str | None,
    ) -> None:
        """Forward a document/file post."""
        path = None
        try:
            # Download to temp file
            path = await message.download_media(file=self.TEMP_DIR)
            if not path:
                logger.warning(f"Failed to download document for message {message.id}")
                if text:
                    await self.max_client.send_message(
                        chat_id=max_chat_id,
                        text=text,
                        format=format_type,
                    )
                return
            
            # Upload to Max
            token = await self.max_client.upload_file(path)
            attachment = {"type": "file", "payload": {"token": token}}
            
            await self.max_client.send_message(
                chat_id=max_chat_id,
                text=text,
                attachments=[attachment],
                format=format_type,
            )
            logger.info(f"Sent document to Max: {path}")
        except Exception as e:
            logger.error(f"Failed to upload/forward file: {e}")
            if text:
                await self.max_client.send_message(
                    chat_id=max_chat_id,
                    text=text,
                    format=format_type,
                )
        finally:
            if path and os.path.exists(path):
                os.remove(path)
                logger.debug(f"Cleaned up: {path}")
    
    async def _notify_insufficient_funds(self, user_id: int, channel: str) -> None:
        """
        Send notification about insufficient funds to user.
        
        Args:
            user_id: Telegram user ID
            channel: Telegram channel username
        """
        # Get current balance
        async with get_session() as session:
            balance = await get_balance(session, user_id)
        
        message_text = (
            f"⚠️ <b>Автопостинг приостановлен</b>\n\n"
            f"Канал: @{channel}\n"
            f"Причина: недостаточно средств\n\n"
            f"Текущий баланс: {balance} постов\n"
            f"Стоимость автопостинга: 3₽ (3 поста) за пост\n\n"
            f"Пополните баланс, чтобы возобновить автопостинг:\n"
            f"/balance — проверить баланс\n"
            f"/buy — купить посты"
        )
        
        # Send message via bot if available
        if self.bot is not None:
            try:
                await self.bot.send_message(
                    chat_id=user_id,
                    text=message_text,
                    parse_mode="HTML"
                )
                logger.info(f"Sent insufficient funds notification to user {user_id}")
            except Exception as e:
                logger.error(f"Failed to send notification to user {user_id}: {e}")
        else:
            logger.warning(f"Cannot notify user {user_id}: bot instance not set")
    
    async def pause_subscription(
        self, 
        user_id: int, 
        tg_channel: str, 
        reason: str
    ) -> None:
        """
        Pause autoposting for a channel.
        
        Args:
            user_id: Telegram user ID
            tg_channel: Telegram channel username
            reason: Pause reason (e.g., "insufficient_funds")
        """
        # Normalize channel name
        if tg_channel.startswith('@'):
            tg_channel = tg_channel[1:]
        
        # Stop active task
        if tg_channel in self.active_tasks:
            await self.stop_autopost(tg_channel)
        
        # Update database
        async with get_session() as session:
            repo = AutopostSubscriptionRepository(session)
            subscription = await repo.get_by_channel(user_id, tg_channel)
            if subscription:
                await repo.pause_subscription(subscription.id, reason)
                logger.info(
                    f"Paused autopost subscription for user {user_id} "
                    f"channel @{tg_channel}, reason: {reason}"
                )
    
    async def resume_subscription(
        self, 
        user_id: int, 
        tg_channel: str
    ) -> bool:
        """
        Resume autoposting for a channel.
        
        Args:
            user_id: Telegram user ID
            tg_channel: Telegram channel username
            
        Returns:
            True if resumed successfully, False otherwise
        """
        # Normalize channel name
        if tg_channel.startswith('@'):
            tg_channel = tg_channel[1:]
        
        # Update database
        async with get_session() as session:
            repo = AutopostSubscriptionRepository(session)
            subscription = await repo.get_by_channel(user_id, tg_channel)
            if subscription:
                await repo.resume_subscription(subscription.id)
                logger.info(
                    f"Resumed autopost subscription for user {user_id} "
                    f"channel @{tg_channel}"
                )
                # Note: actual autopost restart should be done via start_autopost
                # with proper max_chat_id from subscription
                return True
        return False
    
    async def start_monitoring(self, subscription) -> bool:
        """
        Start monitoring a subscription.
        
        Wrapper method that accepts a subscription object and starts
        autoposting for it.
        
        Args:
            subscription: AutopostSubscription model instance
            
        Returns:
            True if started successfully, False otherwise
        """
        return await self.start_autopost(
            tg_channel=subscription.tg_channel,
            max_chat_id=subscription.max_chat_id,
            user_id=subscription.user_id,
            subscription=subscription,
        )
    
    async def stop_monitoring(self, subscription_id: int) -> bool:
        """
        Stop monitoring by subscription ID.
        
        Finds the subscription in the database and stops autoposting.
        
        Args:
            subscription_id: Subscription ID from database
            
        Returns:
            True if stopped successfully, False otherwise
        """
        async with get_session() as session:
            repo = AutopostSubscriptionRepository(session)
            # Get subscription to find channel name
            from bot.database.models import AutopostSubscription
            from sqlalchemy import select
            
            stmt = select(AutopostSubscription).where(
                AutopostSubscription.id == subscription_id
            )
            result = await session.execute(stmt)
            subscription = result.scalars().first()
            
            if subscription:
                return await self.stop_autopost(subscription.tg_channel)
        
        return False
    
    async def stop_all(self) -> None:
        """
        Stop all active autopost tasks.
        
        Used during graceful shutdown to stop all polling tasks.
        """
        if not self.active_tasks:
            logger.debug("No active autopost tasks to stop")
            return
        
        logger.info(f"Stopping {len(self.active_tasks)} autopost tasks...")
        
        # Get all tasks to cancel
        tasks_to_cancel = []
        for tg_channel, task_info in list(self.active_tasks.items()):
            task = task_info.get("task")
            if task and not task.done():
                task.cancel()
                tasks_to_cancel.append(task)
        
        # Wait for all tasks to complete (with timeout)
        if tasks_to_cancel:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks_to_cancel, return_exceptions=True),
                    timeout=10.0
                )
            except asyncio.TimeoutError:
                logger.warning("Some autopost tasks did not stop within timeout")
        
        # Flush any pending albums
        for grouped_id in list(self._album_buffer.keys()):
            await self._flush_album_buffer(grouped_id)
        
        self.active_tasks.clear()
        logger.info("All autopost tasks stopped")
    
    def get_active_channels(self) -> list[dict]:
        """
        Get list of active autoposting channels.
        
        Returns:
            List of dicts with tg_channel, max_chat_id, user_id
        """
        return [
            {
                "tg_channel": ch,
                "max_chat_id": info["max_chat_id"],
                "user_id": info["user_id"],
            }
            for ch, info in self.active_tasks.items()
        ]
    
    def is_active(self, tg_channel: str) -> bool:
        """
        Check if autoposting is active for a channel.
        
        Args:
            tg_channel: Telegram channel username
            
        Returns:
            True if active, False otherwise
        """
        if tg_channel.startswith('@'):
            tg_channel = tg_channel[1:]
        return tg_channel in self.active_tasks
    
    def get_user_active_channels(self, user_id: int) -> list[dict]:
        """
        Get active autoposting channels for a specific user.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            List of dicts with tg_channel, max_chat_id
        """
        return [
            {
                "tg_channel": ch,
                "max_chat_id": info["max_chat_id"],
            }
            for ch, info in self.active_tasks.items()
            if info["user_id"] == user_id
        ]
