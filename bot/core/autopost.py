"""Autoposting manager for automatic forwarding of new posts from TG to Max."""

from __future__ import annotations

import asyncio
import io
from decimal import Decimal
from typing import Callable

from loguru import logger
from telethon import events
from telethon.tl.types import Message

from bot.core.transfer_engine import convert_entities_to_html
from bot.max_api.client import MaxClient
from bot.database.balance import get_balance, charge_autopost_with_subscription
from bot.database.repositories.autopost_subscription import AutopostSubscriptionRepository
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
    
    Uses Telethon event handlers to listen for new messages in TG channels
    and automatically forwards them to Max channels.
    """
    
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
        self.active_tasks: dict[str, dict] = {}  # tg_channel -> {max_chat_id, handler, user_id}
    
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
            
            # Catch-up logic: process missed posts before setting up event handler
            try:
                await self._catch_up_missed_posts(
                    client, entity, tg_channel, max_chat_id, user_id, subscription
                )
            except Exception as e:
                logger.error(f"Catch-up failed for {tg_channel}: {e}")
                # Continue with monitoring even if catch-up fails
            
            # Create event handler with closure for user_id and tg_channel
            @client.on(events.NewMessage(chats=entity))
            async def handler(event):
                """Handle new messages in the channel."""
                logger.info(f"Event received: {type(event).__name__} from channel_id={event.chat_id if hasattr(event, 'chat_id') else 'unknown'}")
                try:
                    await self._forward_post(
                        event.message, 
                        max_chat_id, 
                        user_id, 
                        tg_channel,
                        subscription
                    )
                except Exception as e:
                    logger.error(f"Autopost error for {tg_channel}: {e}", exc_info=True)
            
            logger.info(f"Registered NewMessage handler for channel: {tg_channel} (entity_id={entity.id})")
            
            # Store task info
            self.active_tasks[tg_channel] = {
                "max_chat_id": max_chat_id,
                "user_id": user_id,
                "handler": handler,
                "entity": entity,
                "subscription": subscription,
            }
            
            logger.info(f"Autopost started: {tg_channel} -> {max_chat_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start autopost for {tg_channel}: {e}")
            return False
    
    async def _catch_up_missed_posts(
        self,
        client,
        entity,
        tg_channel: str,
        max_chat_id: int,
        user_id: int,
        subscription: object | None,
    ) -> None:
        """
        Catch up missed posts that were published while autopost was paused.
        
        Args:
            client: Telethon client
            entity: Telegram channel entity
            tg_channel: Telegram channel username (without @)
            max_chat_id: Max channel chat_id
            user_id: Telegram user ID
            subscription: AutopostSubscription object with last_post_id
        """
        if subscription is None:
            logger.debug(f"No subscription provided for {tg_channel}, skipping catch-up")
            return
        
        subscription_id = getattr(subscription, 'id', None)
        last_post_id = getattr(subscription, 'last_post_id', None)
        
        if last_post_id is not None:
            # Get recent messages and filter missed ones
            missed_messages = []
            async for message in client.iter_messages(entity, limit=50):
                if message.id > last_post_id:
                    missed_messages.append(message)
            
            if not missed_messages:
                logger.info(f"No missed posts for @{tg_channel}")
                return
            
            # Sort by ID ascending (oldest first) to process in order
            missed_messages.sort(key=lambda m: m.id)
            
            transferred_count = 0
            is_admin = user_id in settings.ADMIN_IDS
            
            for message in missed_messages:
                # Check if should skip
                should_skip, skip_reason = self._should_skip_autopost(message)
                if should_skip:
                    logger.debug(f"Catch-up: skipping message {message.id} - {skip_reason}")
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
                            break
                
                # Forward the post
                try:
                    await self._do_forward(message, max_chat_id)
                    transferred_count += 1
                    
                    # Update last_post_id in database
                    if subscription_id:
                        async with get_session() as session:
                            repo = AutopostSubscriptionRepository(session)
                            await repo.update_last_post_id(subscription_id, message.id)
                except Exception as e:
                    logger.error(f"Catch-up: failed to forward message {message.id}: {e}")
                    continue
            
            logger.info(f"Catch-up: transferred {transferred_count} missed posts for @{tg_channel}")
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
            else:
                logger.info(f"First start: no messages found in @{tg_channel}")
    
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
            task = self.active_tasks.pop(tg_channel)
            client = await self.telethon_client._get_client()
            client.remove_event_handler(task["handler"])
            
            logger.info(f"Autopost stopped: {tg_channel}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop autopost for {tg_channel}: {e}")
            return False
    
    async def _forward_post(
        self, 
        message: Message, 
        max_chat_id: int, 
        user_id: int, 
        tg_channel: str,
        subscription: object | None = None,
    ) -> None:
        """
        Forward a single post to Max with balance check.
        
        Args:
            message: Telethon Message object
            max_chat_id: Max channel chat_id
            user_id: Telegram user ID
            tg_channel: Telegram channel username
            subscription: Optional AutopostSubscription object to update last_post_id
        """
        # Log new message received
        logger.info(f"Autopost: new message in @{tg_channel}, id={message.id}")
        
        # 1. Filter service and empty messages
        should_skip, skip_reason = self._should_skip_autopost(message)
        if should_skip:
            logger.info(f"Autopost: @{tg_channel} post #{message.id} skipped - {skip_reason}")
            return
        
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
                    return
        
        # 4. Forward the post
        await self._do_forward(message, max_chat_id)
        
        # 5. Update last_post_id in database
        if subscription:
            subscription_id = getattr(subscription, 'id', None)
            if subscription_id:
                async with get_session() as session:
                    repo = AutopostSubscriptionRepository(session)
                    await repo.update_last_post_id(subscription_id, message.id)
        
        # 6. Log success
        logger.info(f"Autopost: @{tg_channel} post #{message.id} transferred")
    
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
        buf = io.BytesIO()
        await message.download_media(file=buf)
        buf.seek(0)
        video_bytes = buf.read()
        
        if not video_bytes:
            logger.warning(f"Empty video bytes for message {message.id}")
            if text:
                await self.max_client.send_message(
                    chat_id=max_chat_id,
                    text=text,
                    format=format_type,
                )
            return
        
        try:
            token = await self.max_client.upload_video(video_bytes)
            attachment = {"type": "video", "payload": {"token": token}}
            
            await self.max_client.send_message(
                chat_id=max_chat_id,
                text=text,
                attachments=[attachment],
                format=format_type,
            )
        except Exception as e:
            logger.error(f"Failed to upload/forward video: {e}")
            if text:
                await self.max_client.send_message(
                    chat_id=max_chat_id,
                    text=text,
                    format=format_type,
                )
    
    async def _forward_audio(
        self,
        message: Message,
        max_chat_id: int,
        text: str,
        format_type: str | None,
    ) -> None:
        """Forward an audio/voice post."""
        buf = io.BytesIO()
        await message.download_media(file=buf)
        buf.seek(0)
        audio_bytes = buf.read()
        
        if not audio_bytes:
            logger.warning(f"Empty audio bytes for message {message.id}")
            if text:
                await self.max_client.send_message(
                    chat_id=max_chat_id,
                    text=text,
                    format=format_type,
                )
            return
        
        try:
            token = await self.max_client.upload_audio(audio_bytes)
            attachment = {"type": "audio", "payload": {"token": token}}
            
            await self.max_client.send_message(
                chat_id=max_chat_id,
                text=text,
                attachments=[attachment],
                format=format_type,
            )
        except Exception as e:
            logger.error(f"Failed to upload/forward audio: {e}")
            if text:
                await self.max_client.send_message(
                    chat_id=max_chat_id,
                    text=text,
                    format=format_type,
                )
    
    async def _forward_document(
        self,
        message: Message,
        max_chat_id: int,
        text: str,
        format_type: str | None,
    ) -> None:
        """Forward a document/file post."""
        buf = io.BytesIO()
        await message.download_media(file=buf)
        buf.seek(0)
        file_bytes = buf.read()
        
        if not file_bytes:
            logger.warning(f"Empty file bytes for message {message.id}")
            if text:
                await self.max_client.send_message(
                    chat_id=max_chat_id,
                    text=text,
                    format=format_type,
                )
            return
        
        try:
            token = await self.max_client.upload_file(file_bytes)
            attachment = {"type": "file", "payload": {"token": token}}
            
            await self.max_client.send_message(
                chat_id=max_chat_id,
                text=text,
                attachments=[attachment],
                format=format_type,
            )
        except Exception as e:
            logger.error(f"Failed to upload/forward file: {e}")
            if text:
                await self.max_client.send_message(
                    chat_id=max_chat_id,
                    text=text,
                    format=format_type,
                )
    
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
