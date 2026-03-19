"""Telethon client for reading Telegram channel history.

Telethon uses MTProto API (unlike aiogram's Bot API), which allows
reading full channel history via GetHistoryRequest - something Bot API cannot do.

IMPORTANT: GetHistoryRequest requires a USER session, not bot token.
Run scripts/auth_telethon.py once to authorize and create the session file.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument, MessageMediaWebPage
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.errors import SessionPasswordNeededError
from loguru import logger


# Constants
MAX_RETRIES = 3


# Session file path
SESSION_FILE = "user_session"


@dataclass
class PostInfo:
    """Simplified post data for transfer."""

    id: int
    text: Optional[str]
    has_media: bool
    media_type: Optional[str]  # 'photo', 'document', 'video', 'webpage', etc.
    date: int  # unix timestamp


class TelethonChannelClient:
    """
    Telethon-based client for reading Telegram channel history.

    Uses USER session (phone-based auth) because GetHistoryRequest
    is not available for bot accounts.

    Session is saved to file after first authorization via scripts/auth_telethon.py
    """

    def __init__(self, api_id: int, api_hash: str, phone: str):
        """
        Initialize Telethon client with user credentials.

        Args:
            api_id: Telegram API ID from https://my.telegram.org
            api_hash: Telegram API Hash from https://my.telegram.org
            phone: Phone number for user session (e.g., +7XXXXXXXXXX)
        """
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self._client: Optional[TelegramClient] = None
        self._session_path = Path(SESSION_FILE + ".session")
        self._keepalive_task: Optional[asyncio.Task] = None

    def _is_session_exists(self) -> bool:
        """Check if session file exists."""
        return self._session_path.exists()

    async def _get_client(self) -> TelegramClient:
        """
        Get or create Telethon client instance.

        Returns:
            TelegramClient instance

        Raises:
            RuntimeError: If session file doesn't exist (needs auth via script)
        """
        if self._client is None:
            # Check if session file exists
            if not self._is_session_exists():
                logger.error(
                    f"Telethon session file not found: {self._session_path}\n"
                    f"Run 'python scripts/auth_telethon.py' to authorize."
                )
                raise RuntimeError(
                    "No Telethon session found. "
                    "Run 'python scripts/auth_telethon.py' to authorize."
                )

            # Create client with existing session file
            self._client = TelegramClient(
                session=SESSION_FILE,
                api_id=self.api_id,
                api_hash=self.api_hash,
            )

            # Start the client (connect + auth + start update loop)
            # This is REQUIRED for receiving NewMessage events
            await self._client.start(phone=self.phone)
            logger.info(f"Telethon client started with user session: {self.phone}")
            
            # Start keepalive task to maintain connection
            if self._keepalive_task is None or self._keepalive_task.done():
                self._keepalive_task = asyncio.create_task(self._keepalive_loop())
                logger.info("Telethon keepalive task started")
            
        return self._client
    
    async def run_until_disconnected(self) -> None:
        """
        Run the client until disconnected.
        
        This keeps the client alive and listening for events.
        Must be run in parallel with aiogram polling in the same event loop.
        """
        if self._client is None:
            logger.warning("Cannot run_until_disconnected: client not initialized")
            return
        
        try:
            logger.info("Telethon client event loop started (run_until_disconnected)")
            await self._client.run_until_disconnected()
        except Exception as e:
            logger.error(f"Telethon client event loop error: {e}", exc_info=True)
        finally:
            logger.warning("Telethon client event loop ended")

    async def close(self) -> None:
        """Close the Telethon client."""
        # Cancel keepalive task
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
            logger.info("Telethon keepalive task stopped")
        
        if self._client:
            await self._client.disconnect()
            self._client = None
            logger.info("Telethon client disconnected")

    async def _call_with_retry(self, method, *args, **kwargs):
        """Call a Telethon method with retry logic for connection errors.
        
        Args:
            method: The async method to call
            *args, **kwargs: Arguments to pass to the method
            
        Returns:
            The result of the method call
            
        Raises:
            ConnectionError: If all retries fail
        """
        for attempt in range(MAX_RETRIES):
            try:
                return await method(*args, **kwargs)
            except (ConnectionError, asyncio.TimeoutError, OSError) as e:
                logger.warning(f"Telethon connection lost, reconnecting... attempt {attempt+1}")
                try:
                    await self._client.connect()
                except Exception:
                    pass
                await asyncio.sleep(2 ** attempt)
        raise ConnectionError(f"Failed after {MAX_RETRIES} attempts")

    async def _keepalive_loop(self):
        """Keep connection alive by periodic get_me() calls."""
        while True:
            try:
                await asyncio.sleep(300)  # 5 minutes
                if self._client and self._client.is_connected():
                    await self._client.get_me()
                    logger.debug("Keepalive ping sent")
            except Exception as e:
                logger.warning(f"Keepalive failed: {e}")
                try:
                    await self._client.connect()
                except Exception:
                    pass

    async def get_channel_description(self, channel: str) -> str:
        """
        Get channel description (about) via Telethon.

        Args:
            channel: Channel username (with or without @) or channel ID

        Returns:
            Channel description string or empty string if not available

        Raises:
            Exception: If channel cannot be accessed
        """
        client = await self._get_client()

        # Normalize channel identifier
        if channel.startswith('@'):
            channel = channel[1:]

        try:
            entity = await self._call_with_retry(client.get_entity, channel)
            full = await self._call_with_retry(client, GetFullChannelRequest(entity))
            return full.full_chat.about or ""
        except Exception as e:
            logger.error(f"Error getting channel description for {channel}: {e}")
            raise

    async def count_channel_posts(self, channel_username: str) -> int:
        """
        Count total posts in a Telegram channel.

        Args:
            channel_username: Channel username (with or without @)

        Returns:
            Total number of posts in the channel

        Raises:
            Exception: If channel cannot be accessed
        """
        client = await self._get_client()

        # Normalize username
        if channel_username.startswith('@'):
            channel_username = channel_username[1:]

        try:
            # get_messages with limit=0 returns only total count
            # This is efficient - doesn't fetch actual messages
            result = await self._call_with_retry(client.get_messages, channel_username, limit=0)
            total = result.total
            logger.info(f"Channel @{channel_username}: {total} posts")
            return total

        except Exception as e:
            logger.error(f"Error counting posts in @{channel_username}: {e}")
            raise

    async def get_channel_posts(
        self,
        channel_username: str,
        limit: Optional[int] = None,
        reverse: bool = True,
    ) -> list[PostInfo]:
        """
        Get posts from a Telegram channel.

        Args:
            channel_username: Channel username (with or without @)
            limit: Maximum number of posts to fetch. None = all posts
            reverse: If True, fetch oldest first (for transfer order).
                     If False, fetch newest first.

        Returns:
            List of PostInfo objects

        Raises:
            Exception: If channel cannot be accessed
        """
        client = await self._get_client()

        # Normalize username
        if channel_username.startswith('@'):
            channel_username = channel_username[1:]

        posts = []

        try:
            # iter_messages is memory-efficient for large channels
            # Use _call_with_retry to handle connection errors on initial call
            iterator = client.iter_messages(
                channel_username,
                limit=limit,
                reverse=reverse,  # oldest first for transfer
            )
            async for message in iterator:
                # Determine media type
                media_type = None
                has_media = False

                if message.media:
                    has_media = True
                    if isinstance(message.media, MessageMediaPhoto):
                        media_type = 'photo'
                    elif isinstance(message.media, MessageMediaDocument):
                        media_type = 'document'
                        # Check if it's a video
                        if message.media.document:
                            for attr in message.media.document.attributes:
                                if hasattr(attr, 'video'):
                                    media_type = 'video'
                    elif isinstance(message.media, MessageMediaWebPage):
                        media_type = 'webpage'
                    else:
                        media_type = 'unknown'

                post = PostInfo(
                    id=message.id,
                    text=message.text or None,
                    has_media=has_media,
                    media_type=media_type,
                    date=int(message.date.timestamp()) if message.date else None,
                )
                posts.append(post)

            logger.info(f"Fetched {len(posts)} posts from @{channel_username}")
            return posts

        except Exception as e:
            logger.error(f"Error fetching posts from @{channel_username}: {e}")
            raise


# Singleton instance getter
_client_instance: Optional[TelethonChannelClient] = None


def get_telethon_client(api_id: int, api_hash: str, phone: str) -> TelethonChannelClient:
    """
    Get singleton Telethon client instance.

    Args:
        api_id: Telegram API ID
        api_hash: Telegram API Hash
        phone: Phone number for user session

    Returns:
        TelethonChannelClient instance
    """
    global _client_instance
    if _client_instance is None:
        _client_instance = TelethonChannelClient(api_id, api_hash, phone)
    return _client_instance
