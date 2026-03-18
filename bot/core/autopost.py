"""Autoposting manager for automatic forwarding of new posts from TG to Max."""

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
    
    async def start_autopost(
        self,
        tg_channel: str,
        max_chat_id: int,
        user_id: int,
    ) -> bool:
        """
        Start autoposting for a channel.
        
        Args:
            tg_channel: Telegram channel username (with or without @)
            max_chat_id: Max channel chat_id
            user_id: Telegram user ID who owns this autopost
            
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
            
            # Create event handler with closure for user_id and tg_channel
            @client.on(events.NewMessage(chats=entity))
            async def handler(event):
                """Handle new messages in the channel."""
                try:
                    await self._forward_post(
                        event.message, 
                        max_chat_id, 
                        user_id, 
                        tg_channel
                    )
                except Exception as e:
                    logger.error(f"Autopost error for {tg_channel}: {e}")
            
            # Store task info
            self.active_tasks[tg_channel] = {
                "max_chat_id": max_chat_id,
                "user_id": user_id,
                "handler": handler,
                "entity": entity,
            }
            
            logger.info(f"Autopost started: {tg_channel} -> {max_chat_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start autopost for {tg_channel}: {e}")
            return False
    
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
        tg_channel: str
    ) -> None:
        """
        Forward a single post to Max with balance check.
        
        Args:
            message: Telethon Message object
            max_chat_id: Max channel chat_id
            user_id: Telegram user ID
            tg_channel: Telegram channel username
        """
        # 1. Check if user is admin (admins have unlimited access)
        is_admin = user_id in settings.ADMIN_IDS
        
        # 2. If not admin - check and charge balance
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
        
        # 3. Forward the post (existing logic)
        await self._do_forward(message, max_chat_id)
        
        logger.info(
            f"Autopost: @{tg_channel} post #{message.id} -> Max, "
            f"charged 3₽ (admin: {is_admin})"
        )
    
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
