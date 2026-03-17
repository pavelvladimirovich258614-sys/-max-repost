"""Autoposting manager for automatic forwarding of new posts from TG to Max."""

import asyncio
import io
from typing import Callable

from loguru import logger
from telethon import events
from telethon.tl.types import Message

from bot.core.transfer_engine import convert_entities_to_html
from bot.max_api.client import MaxClient


class AutopostManager:
    """
    Manages autoposting for all active channels.
    
    Uses Telethon event handlers to listen for new messages in TG channels
    and automatically forwards them to Max channels.
    """
    
    def __init__(self, telethon_client, max_client: MaxClient):
        """
        Initialize the autopost manager.
        
        Args:
            telethon_client: TelethonChannelClient instance
            max_client: MaxClient instance for sending to Max
        """
        self.telethon_client = telethon_client
        self.max_client = max_client
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
            
            # Create event handler
            @client.on(events.NewMessage(chats=entity))
            async def handler(event):
                """Handle new messages in the channel."""
                try:
                    await self._forward_post(event.message, max_chat_id)
                    logger.info(
                        f"Autopost: forwarded post {event.message.id} "
                        f"from {tg_channel} to {max_chat_id}"
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
    
    async def _forward_post(self, message: Message, max_chat_id: int) -> None:
        """
        Forward a single post to Max.
        
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
