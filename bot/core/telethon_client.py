"""Telethon client for reading Telegram channel history.

Telethon uses MTProto API (unlike aiogram's Bot API), which allows
reading full channel history via GetHistoryRequest - something Bot API cannot do.

IMPORTANT: GetHistoryRequest requires a USER session, not bot token.
Run scripts/auth_telethon.py once to authorize and create the session file.
"""

import asyncio
import os
import re
import socks
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument, MessageMediaWebPage
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from loguru import logger


def parse_proxy_url(proxy_url: str) -> tuple | None:
    """
    Parse SOCKS proxy URL into Telethon format.

    Args:
        proxy_url: Proxy URL (e.g., 'socks5://127.0.0.1:1080')

    Returns:
        Tuple (proxy_type, addr, port) or None if invalid format
    """
    if not proxy_url:
        return None

    # Parse URL format: socks5://host:port or socks4://host:port
    match = re.match(r'socks([45])://([^:]+):(\d+)', proxy_url)
    if not match:
        logger.warning(f"Invalid proxy URL format: {proxy_url}")
        return None

    socks_version, host, port = match.groups()
    proxy_type = socks.SOCKS5 if socks_version == '5' else socks.SOCKS4

    return (proxy_type, host, int(port))


# Constants
MAX_RETRIES = 5


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

    def __init__(self, api_id: int, api_hash: str, phone: str, session_string: str = "", proxy_url: str = ""):
        """
        Initialize Telethon client with user credentials.

        Args:
            api_id: Telegram API ID from https://my.telegram.org
            api_hash: Telegram API Hash from https://my.telegram.org
            phone: Phone number for user session (e.g., +7XXXXXXXXXX)
            session_string: StringSession string (exported via scripts/export_session.py)
            proxy_url: SOCKS proxy URL (e.g., 'socks5://127.0.0.1:1080')
        """
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.session_string = session_string
        self._proxy = parse_proxy_url(proxy_url) if proxy_url else None
        self._client: Optional[TelegramClient] = None
        self._session_path = Path(SESSION_FILE + ".session")
        self._keepalive_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

        if self._proxy:
            logger.info(f"Telethon will use proxy: {proxy_url}")

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
            # Use StringSession if provided, otherwise fallback to SQLite file
            if self.session_string:
                logger.info("Using StringSession (in-memory) for Telethon")
                session = StringSession(self.session_string)
            else:
                # Fallback to SQLite session file
                if not self._is_session_exists():
                    logger.error(
                        f"Telethon session file not found: {self._session_path}\n"
                        f"Run 'python scripts/export_session.py' to export existing session, OR\n"
                        f"Run 'python scripts/auth_telethon.py' to authorize."
                    )
                    raise RuntimeError(
                        "No Telethon session found. "
                        "Run 'python scripts/export_session.py' to export, OR "
                        "'python scripts/auth_telethon.py' to authorize."
                    )
                logger.info("Using SQLite session file for Telethon (legacy)")
                session = SESSION_FILE

            self._client = TelegramClient(
                session=session,
                api_id=self.api_id,
                api_hash=self.api_hash,
                proxy=self._proxy,
            )

            # Start the client (connect + auth + start update loop)
            # This is REQUIRED for receiving NewMessage events
            try:
                await self._client.start(phone=self.phone)
                logger.info(f"Telethon client started with user session: {self.phone}")
            except FloodWaitError as e:
                logger.error(f"FloodWaitError on start(): {e.seconds}s, aborting Telethon start")
                logger.warning("Bot will continue without Telethon - autopost may not work")
                raise RuntimeError(f"Telethon FloodWaitError: {e.seconds}s")
            
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

    def _is_database_locked_error(self, e: Exception) -> bool:
        """Check if exception is a database locked error."""
        error_msg = str(e).lower()
        if "database is locked" in error_msg:
            return True
        if hasattr(e, 'args') and any("database is locked" in str(arg).lower() for arg in e.args):
            return True
        return False

    async def _call_with_retry(self, func, *args, **kwargs):
        """Call a Telethon method with retry logic and locking."""
        for attempt in range(MAX_RETRIES):
            try:
                async with self._lock:
                    return await func(*args, **kwargs)
            except FloodWaitError as e:
                if e.seconds > 60:
                    logger.warning(f"FloodWait: {e.seconds}s > 60s, skipping operation")
                    return None
                logger.info(f"FloodWait: waiting {e.seconds}s before retry")
                await asyncio.sleep(e.seconds)
                continue
            except sqlite3.OperationalError as e:
                if self._is_database_locked_error(e):
                    wait_time = 2 ** (attempt + 1)  # 2, 4, 8, 16, 32 seconds
                    logger.warning(f"Telethon database locked, retrying in {wait_time}s... (attempt {attempt+1})")
                    await asyncio.sleep(wait_time)
                    continue
                raise
            except (ConnectionError, asyncio.TimeoutError, OSError) as e:
                logger.warning(f"Telethon connection lost, reconnecting... attempt {attempt+1}")
                try:
                    async with self._lock:
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
                    try:
                        async with self._lock:
                            await self._client.get_me()
                        logger.debug("Keepalive ping sent")
                    except FloodWaitError as e:
                        if e.seconds > 60:
                            logger.warning(f"Keepalive: FloodWait {e.seconds}s > 60s, skipping ping")
                        else:
                            logger.info(f"Keepalive: FloodWait {e.seconds}s, waiting")
                            await asyncio.sleep(e.seconds)
            except sqlite3.OperationalError as e:
                if self._is_database_locked_error(e):
                    logger.warning("Keepalive skipped due to database lock")
                    continue
                logger.warning(f"Keepalive failed: {e}")
            except Exception as e:
                logger.warning(f"Keepalive failed: {e}")
                try:
                    async with self._lock:
                        await self._client.connect()
                except Exception:
                    pass

    async def get_channel_description(self, channel: str) -> str:
        """
        Get channel description (about) via Telethon.

        Args:
            channel: Channel username (with or without @), channel ID, or invite hash

        Returns:
            Channel description string or empty string if not available

        Raises:
            Exception: If channel cannot be accessed
        """
        await self._get_client()

        try:
            entity = await self._call_with_retry(self._client.get_entity, channel)
            full = await self._call_with_retry(self._client, GetFullChannelRequest(entity))
            return full.full_chat.about or ""
        except Exception as e:
            logger.error(f"Error getting channel description for {channel}: {e}")
            raise

    async def count_channel_posts(self, channel_identifier: str) -> int:
        """
        Count total posts in a Telegram channel.

        Supports public channels (@username), private channels (by ID or invite),
        and numeric channel IDs (-100...).

        Args:
            channel_identifier: Channel username (with or without @), numeric ID,
                               or invite hash (+XXXXX)

        Returns:
            Total number of posts in the channel

        Raises:
            Exception: If channel cannot be accessed
        """
        await self._get_client()

        try:
            # get_messages with limit=0 returns only total count
            # This is efficient - doesn't fetch actual messages
            result = await self._call_with_retry(self._client.get_messages, channel_identifier, limit=0)
            total = result.total
            logger.info(f"Channel {channel_identifier}: {total} posts")
            return total

        except Exception as e:
            logger.error(f"Error counting posts in {channel_identifier}: {e}")
            raise

    async def get_channel_posts(
        self,
        channel_identifier: str,
        limit: Optional[int] = None,
        reverse: bool = True,
    ) -> list[PostInfo]:
        """
        Get posts from a Telegram channel.

        Supports public channels (@username), private channels (by ID or invite),
        and numeric channel IDs (-100...).

        Args:
            channel_identifier: Channel username (with or without @), numeric ID,
                               or invite hash (+XXXXX)
            limit: Maximum number of posts to fetch. None = all posts
            reverse: If True, fetch oldest first (for transfer order).
                     If False, fetch newest first.

        Returns:
            List of PostInfo objects

        Raises:
            Exception: If channel cannot be accessed
        """
        await self._get_client()

        posts = []

        try:
            # iter_messages is memory-efficient for large channels
            # Wrap iteration in lock to prevent database locked errors
            async with self._lock:
                iterator = self._client.iter_messages(
                    channel_identifier,
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

            logger.info(f"Fetched {len(posts)} posts from {channel_identifier}")
            return posts

        except Exception as e:
            logger.error(f"Error fetching posts from {channel_identifier}: {e}")
            raise

    async def get_entity(self, channel: str | int):
        """
        Get entity (channel/chat/user) by username or ID.

        Args:
            channel: Channel username (with or without @), numeric ID, or invite hash

        Returns:
            Entity object or None if FloodWait > 60s
        """
        await self._get_client()
        result = await self._call_with_retry(self._client.get_entity, channel)
        if result is None:
            logger.warning(f"get_entity({channel}) returned None due to FloodWait")
        return result

    async def get_full_channel(self, entity):
        """
        Get full channel info via GetFullChannelRequest.
        
        Args:
            entity: Channel entity
            
        Returns:
            Full channel info
        """
        await self._get_client()
        return await self._call_with_retry(self._client, GetFullChannelRequest(entity))

    async def iter_messages(self, *args, **kwargs):
        """
        Get async generator for iterating over messages.
        This returns a wrapped iterator that uses the lock.
        
        Returns:
            Async generator over messages
        """
        await self._get_client()
        
        async def _wrapped_iterator():
            async with self._lock:
                async for message in self._client.iter_messages(*args, **kwargs):
                    yield message
        
        return _wrapped_iterator()


# Singleton instance getter
_client_instance: Optional[TelethonChannelClient] = None


def get_telethon_client(api_id: int, api_hash: str, phone: str, session_string: str = "", proxy_url: str = "") -> TelethonChannelClient:
    """
    Get singleton Telethon client instance.

    Args:
        api_id: Telegram API ID
        api_hash: Telegram API Hash
        phone: Phone number for user session
        session_string: StringSession string (exported via scripts/export_session.py)
        proxy_url: SOCKS proxy URL (e.g., 'socks5://127.0.0.1:1080')

    Returns:
        TelethonChannelClient instance
    """
    global _client_instance
    # Pass session_string and proxy_url to constructor
    if _client_instance is None:
        _client_instance = TelethonChannelClient(api_id, api_hash, phone, session_string, proxy_url)
    return _client_instance
