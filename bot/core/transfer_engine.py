"""Core Engine for transferring posts from Telegram to Max 1:1.

This module handles the complete transfer workflow:
- Fetching posts from Telegram via Telethon
- Downloading media (photos, videos, documents)
- Uploading media to Max via API
- Posting content to Max channels
- Progress tracking and error handling
"""

import asyncio
import html
import io
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger
from telethon.tl.types import (
    Message,
    MessageEntityBold,
    MessageEntityItalic,
    MessageEntityCode,
    MessageEntityPre,
    MessageEntityTextUrl,
    MessageEntityUrl,
    MessageEntityStrike,
    MessageEntityUnderline,
    MessageMediaPhoto,
    MessageMediaDocument,
    MessageMediaWebPage,
    MessageMediaGeo,
    MessageMediaContact,
    MessageMediaPoll,
    MessageMediaDice,
    MessageMediaGame,
    MessageMediaInvoice,
    MessageMediaUnsupported,
)
from telethon.tl.types import (
    Document,
    DocumentAttributeFilename,
    DocumentAttributeAudio,
    DocumentAttributeVideo,
)

from bot.max_api.client import MaxClient, MaxAPIError, RateLimitError
from bot.database.repositories.transferred_post import TransferredPostRepository


def split_text(text: str, max_length: int = 4000) -> list[str]:
    """
    Split text into chunks of max_length characters.
    
    Tries to split at newlines first, then at spaces, then hard split.
    
    Args:
        text: Text to split
        max_length: Maximum length of each chunk
        
    Returns:
        List of text chunks
    """
    if len(text) <= max_length:
        return [text]
    
    chunks = []
    remaining = text
    
    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break
        
        # Try to find a newline to split at
        split_pos = remaining.rfind('\n', 0, max_length + 1)
        
        # If no newline, try to find a space
        if split_pos == -1:
            split_pos = remaining.rfind(' ', 0, max_length + 1)
        
        # If no space either, hard split at max_length
        if split_pos == -1 or split_pos == 0:
            split_pos = max_length
        
        chunks.append(remaining[:split_pos])
        remaining = remaining[split_pos:].lstrip()
    
    return chunks


# =============================================================================
# HTML Conversion (Max API format)
# =============================================================================

def convert_entities_to_html(raw_text: str, entities: list) -> str:
    """
    Convert Telegram entities to HTML for Max API.
    
    Telethon entities use UTF-16 offset/length. This function handles
    Unicode (emojis) correctly by working with UTF-16 encoded text.
    
    Args:
        raw_text: Clean text without markdown (message.raw_text)
        entities: List of Telegram message entities
        
    Returns:
        HTML-formatted text for Max API
    """
    if not entities:
        return html.escape(raw_text)
    
    # Sort entities by offset (reverse order for insertion)
    sorted_entities = sorted(entities, key=lambda e: e.offset, reverse=True)
    
    # Work with UTF-16 encoding for correct emoji handling
    # Telethon uses UTF-16 code units for offset/length
    text_utf16 = raw_text.encode('utf-16-le')
    result = raw_text
    
    for entity in sorted_entities:
        offset = entity.offset
        length = entity.length
        
        # Convert UTF-16 offset/length to Python string indices
        # This handles emojis correctly (emojis are 2 UTF-16 code units)
        prefix = text_utf16[:offset * 2].decode('utf-16-le', errors='replace')
        entity_text = text_utf16[offset * 2:(offset + length) * 2].decode('utf-16-le', errors='replace')
        
        # Escape HTML in entity text
        entity_text_escaped = html.escape(entity_text)
        
        # Determine HTML tag
        tag = None
        close_tag = None
        
        if isinstance(entity, MessageEntityBold):
            tag, close_tag = "<b>", "</b>"
        elif isinstance(entity, MessageEntityItalic):
            tag, close_tag = "<i>", "</i>"
        elif isinstance(entity, MessageEntityCode):
            tag, close_tag = "<code>", "</code>"
        elif isinstance(entity, MessageEntityPre):
            tag, close_tag = "<pre>", "</pre>"
        elif isinstance(entity, MessageEntityTextUrl):
            url = html.escape(entity.url)
            tag = f'<a href="{url}">'
            close_tag = "</a>"
        elif isinstance(entity, MessageEntityUrl):
            # Max auto-links URLs, no tag needed
            continue
        elif isinstance(entity, MessageEntityStrike):
            tag, close_tag = "<s>", "</s>"
        elif isinstance(entity, MessageEntityUnderline):
            tag, close_tag = "<u>", "</u>"
        else:
            # Unknown entity type - skip
            continue
        
        # Replace the entity text with HTML-wrapped version
        # Find position in current result string
        result_prefix = result[:len(prefix)]
        result_suffix = result[len(prefix) + len(entity_text):]
        
        # Verify we're replacing the right text
        if result[len(prefix):len(prefix) + len(entity_text)] == entity_text:
            result = result_prefix + tag + entity_text_escaped + close_tag + result_suffix
        else:
            # Fallback: text might have changed due to previous replacements
            # Try to find and replace exact match
            search_start = max(0, len(prefix) - 10)
            search_end = min(len(result), len(prefix) + len(entity_text) + 10)
            search_area = result[search_start:search_end]
            
            if entity_text in search_area:
                idx = search_area.index(entity_text)
                actual_pos = search_start + idx
                result = (
                    result[:actual_pos] + 
                    tag + entity_text_escaped + close_tag + 
                    result[actual_pos + len(entity_text):]
                )
    
    # Escape any remaining HTML in text outside entities
    # We need to re-escape but preserve our inserted tags
    # Simple approach: split by our tags, escape non-tag parts
    return result


# =============================================================================
# Result Models
# =============================================================================


@dataclass
class TransferError:
    """Details about a failed post transfer."""

    post_id: int
    error_message: str
    error_type: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class TransferResult:
    """Result of a transfer operation."""

    total: int  # Total posts to transfer
    success: int  # Successfully transferred
    failed: int  # Failed to transfer
    skipped: int  # Skipped (unsupported types, empty messages)
    duplicates: int = 0  # Already transferred (skipped to prevent duplicates)
    errors: list[TransferError] = field(default_factory=list)

    @property
    def progress_percent(self) -> int:
        """Calculate completion percentage."""
        if self.total == 0:
            return 0
        completed = self.success + self.failed + self.skipped + self.duplicates
        return int((completed / self.total) * 100)


# =============================================================================
# Progress Callback
# =============================================================================


ProgressCallback = Callable[[int, int, int, int, int], Any]
"""
Progress callback signature.

Args:
    current: Current post number (1-indexed)
    total: Total posts to transfer
    success: Count of successful transfers so far
    failed: Count of failed transfers so far
    skipped: Count of skipped posts so far

Returns:
    Optional awaitable (for async UI updates)
"""


# =============================================================================
# Media Type Detection
# =============================================================================


class MediaType:
    """Media type constants for Max upload."""

    TEXT = "text"       # Text-only post (no media)
    PHOTO = "photo"
    VIDEO = "video"
    AUDIO = "audio"
    FILE = "file"
    WEBPAGE = "webpage"  # Link previews - skip
    UNSUPPORTED = "unsupported"


def detect_media_type(message: Message) -> str:
    """
    Detect the media type of a Telegram message.

    Args:
        message: Telethon Message object

    Returns:
        MediaType constant string
    """
    if not message.media:
        return MediaType.TEXT  # Text only, no media

    if isinstance(message.media, MessageMediaPhoto):
        return MediaType.PHOTO

    if isinstance(message.media, MessageMediaDocument):
        doc = message.media.document
        if not isinstance(doc, Document):
            return MediaType.FILE

        # Check attributes for more specific type
        for attr in doc.attributes:
            if isinstance(attr, DocumentAttributeVideo):
                return MediaType.VIDEO
            if isinstance(attr, DocumentAttributeAudio):
                # Check if it's a voice message (voice=True)
                if getattr(attr, 'voice', False):
                    return MediaType.AUDIO  # Voice messages sent as audio
                return MediaType.AUDIO
            if isinstance(attr, DocumentAttributeFilename):
                # Check extension
                filename = attr.file_name.lower()
                if filename.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
                    return MediaType.PHOTO
                if filename.endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm')):
                    return MediaType.VIDEO
                if filename.endswith(('.mp3', '.ogg', '.wav', '.flac', '.m4a', '.oga')):
                    return MediaType.AUDIO

        return MediaType.FILE

    # Unsupported types - will be skipped
    if isinstance(message.media, (
        MessageMediaWebPage,
        MessageMediaGeo,
        MessageMediaContact,
        MessageMediaPoll,
        MessageMediaDice,
        MessageMediaGame,
        MessageMediaInvoice,
        MessageMediaUnsupported,
    )):
        return MediaType.UNSUPPORTED

    return MediaType.UNSUPPORTED


def should_skip_message(message: Message) -> tuple[bool, str]:
    """
    Check if a message should be skipped during transfer.

    Args:
        message: Telethon Message object

    Returns:
        Tuple of (should_skip, reason)
    """
    # Skip empty messages (no text AND no media)
    has_text = bool(message.raw_text and message.raw_text.strip())
    has_media = message.media is not None
    if not has_text and not has_media:
        return True, "empty message (no text, no media)"
    
    # After the check, log media-only posts
    if has_media and not has_text:
        logger.info(f"Post {message.id}: media-only, will transfer with empty text")

    # Skip service messages
    if message.action:
        return True, f"service message: {type(message.action).__name__}"

    # Check unsupported media types
    media_type = detect_media_type(message)
    if media_type == MediaType.UNSUPPORTED:
        if isinstance(message.media, MessageMediaWebPage):
            return True, "link preview"
        return True, f"unsupported media: {type(message.media).__name__}"

    return False, ""


# =============================================================================
# Transfer Engine
# =============================================================================


class TransferEngine:
    """
    Core engine for transferring posts from Telegram to Max.

    Features:
    - Fetches posts from Telegram via Telethon
    - Downloads and uploads media
    - Handles different content types (text, photo, video, audio, files)
    - Progress tracking via callbacks
    - Robust error handling with consecutive error detection
    - Rate limiting to avoid API bans
    """

    # Maximum consecutive errors before aborting
    MAX_CONSECUTIVE_ERRORS = 5

    # Delay between posts (seconds)
    POST_DELAY = 1.5

    # Retry delay for rate limits (seconds)
    RATE_LIMIT_RETRY_DELAY = 5.0

    def __init__(
        self,
        telethon_client,
        max_api_client: MaxClient,
        db_session=None,
        user_id: int | None = None,
        tg_channel: str | None = None,
        max_channel_id: str | None = None,
    ):
        """
        Initialize the transfer engine.

        Args:
            telethon_client: TelethonChannelClient instance
            max_api_client: MaxClient instance
            db_session: Optional SQLAlchemy session for tracking
            user_id: Telegram user ID for duplicate tracking
            tg_channel: Telegram channel username for duplicate tracking
            max_channel_id: Max channel ID for duplicate tracking
        """
        self.telethon = telethon_client
        self.max_client = max_api_client
        self.db_session = db_session
        self.user_id = user_id
        self.tg_channel = tg_channel
        self.max_channel_id = str(max_channel_id) if max_channel_id else None
        self._transferred_repo: TransferredPostRepository | None = None
        self._abort_flag = False
        
        # Initialize repository if db_session is provided
        if db_session:
            self._transferred_repo = TransferredPostRepository(db_session)

    async def transfer_posts(
        self,
        tg_channel: str,
        max_channel_id: str | int | int,
        count: int | str,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> TransferResult:
        """
        Transfer posts from Telegram channel to Max channel.

        Args:
            tg_channel: Telegram channel username (with or without @)
            max_channel_id: Max channel ID for posting
            count: Number of posts to transfer, or "all" for all posts
            progress_callback: Optional callback for progress updates

        Returns:
            TransferResult with statistics and errors
        """
        result = TransferResult(total=0, success=0, failed=0, skipped=0)
        consecutive_errors = 0

        try:
            # Get Telethon client
            client = await self.telethon._get_client()

            # Normalize channel name
            if tg_channel.startswith('@'):
                tg_channel = tg_channel[1:]

            # Determine limit
            limit = None if count == "all" else int(count)

            # Fetch messages (oldest first for correct order in Max)
            logger.info(f"Starting transfer: @{tg_channel} -> {max_channel_id}, count={count}")

            # Track albums (grouped_id) to avoid duplicate uploads
            processed_group_ids = set()

            async for message in client.iter_messages(
                tg_channel,
                limit=limit,
                reverse=True,  # Oldest first
            ):
                if self._abort_flag:
                    logger.info("Transfer aborted by flag")
                    break

                result.total += 1

                # Check if post was already transferred (duplicate protection)
                if self._transferred_repo and self.user_id:
                    max_chat_id_str = str(max_channel_id)
                    is_duplicate = await self._transferred_repo.is_post_transferred(
                        tg_channel=tg_channel,
                        max_chat_id=max_chat_id_str,
                        tg_message_id=message.id,
                    )
                    if is_duplicate:
                        result.duplicates += 1
                        logger.info(f"Skipping post {message.id}: reason=already_transferred")
                        await self._notify_progress(
                            progress_callback, result, message.id, "already_transferred"
                        )
                        continue

                # Check if we should skip this message
                should_skip, skip_reason = should_skip_message(message)
                if should_skip:
                    result.skipped += 1
                    has_text = bool(message.raw_text and message.raw_text.strip())
                    has_media = message.media is not None
                    media_type = type(message.media).__name__ if message.media else "none"
                    logger.info(
                        f"Skipping post {message.id}: "
                        f"reason={skip_reason}, "
                        f"has_text={has_text}, "
                        f"has_media={has_media}, "
                        f"media_type={media_type}"
                    )
                    await self._notify_progress(
                        progress_callback, result, message.id, skip_reason
                    )
                    consecutive_errors = 0  # Reset on skip
                    continue

                # Handle albums (grouped messages)
                if message.grouped_id and message.grouped_id in processed_group_ids:
                    # Already processed as part of an album
                    result.skipped += 1
                    logger.info(f"Skipping post {message.id}: reason=already_processed_in_album")
                    continue

                # Transfer the post
                try:
                    if message.grouped_id:
                        # Album - collect all messages in the group
                        await self._transfer_album(
                            client,
                            message,
                            tg_channel,
                            max_channel_id,
                            processed_group_ids,
                        )
                        # Album counts as one transfer operation
                        result.success += 1
                        logger.info(f"Transferred album {message.grouped_id}")
                        # Record transfer for duplicate protection
                        if self._transferred_repo and self.user_id:
                            await self._transferred_repo.record_transfer(
                                user_id=self.user_id,
                                tg_channel=tg_channel,
                                max_chat_id=str(max_channel_id),
                                tg_message_id=message.id,
                            )
                    else:
                        # Single post
                        await self._transfer_single_post(
                            message,
                            max_channel_id,
                        )
                        result.success += 1
                        logger.info(f"Transferred post {message.id}")
                        # Record transfer for duplicate protection
                        if self._transferred_repo and self.user_id:
                            await self._transferred_repo.record_transfer(
                                user_id=self.user_id,
                                tg_channel=tg_channel,
                                max_chat_id=str(max_channel_id),
                                tg_message_id=message.id,
                            )

                    # Reset consecutive errors on success
                    consecutive_errors = 0

                    # Rate limiting delay
                    await asyncio.sleep(self.POST_DELAY)

                except MaxAPIError as e:
                    consecutive_errors += 1
                    result.failed += 1
                    error = TransferError(
                        post_id=message.id,
                        error_message=str(e),
                        error_type=type(e).__name__,
                    )
                    result.errors.append(error)

                    if isinstance(e, RateLimitError):
                        logger.warning(f"Rate limited on post {message.id}, waiting {self.RATE_LIMIT_RETRY_DELAY}s")
                        await asyncio.sleep(self.RATE_LIMIT_RETRY_DELAY)
                    else:
                        logger.error(f"API error transferring post {message.id}: {e}")

                    # Check if we should abort
                    if consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                        logger.error(f"Too many consecutive errors ({consecutive_errors}), aborting")
                        error = TransferError(
                            post_id=0,
                            error_message=f"Aborted after {consecutive_errors} consecutive errors",
                            error_type="MaxConsecutiveErrors",
                        )
                        result.errors.append(error)
                        break

                except Exception as e:
                    consecutive_errors += 1
                    result.failed += 1
                    error = TransferError(
                        post_id=message.id,
                        error_message=str(e),
                        error_type=type(e).__name__,
                    )
                    result.errors.append(error)
                    logger.error(f"Unexpected error transferring post {message.id}: {e}")

                    if consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                        logger.error(f"Too many consecutive errors ({consecutive_errors}), aborting")
                        break

                # Notify progress
                await self._notify_progress(
                    progress_callback, result, message.id, None
                )

            logger.info(
                f"Transfer complete: {result.success} success, "
                f"{result.failed} failed, {result.skipped} skipped"
            )

        except Exception as e:
            logger.error(f"Transfer failed with exception: {e}")
            error = TransferError(
                post_id=0,
                error_message=f"Transfer failed: {str(e)}",
                error_type=type(e).__name__,
            )
            result.errors.append(error)

        return result

    async def _transfer_single_post(
        self,
        message: Message,
        max_channel_id: str | int | int,
    ) -> None:
        """
        Transfer a single post (not part of an album).
        """
        media_type = detect_media_type(message)
        raw_text = message.raw_text or ""
        
        # Convert entities to HTML for Max API
        if message.entities:
            text = convert_entities_to_html(raw_text, message.entities)
        else:
            text = html.escape(raw_text)
        
        # Check if text needs splitting
        text_chunks = split_text(text, max_length=4000)
        
        # Handle posts based on media type
        match media_type:
            case MediaType.TEXT:
                # Text-only post - send all chunks
                for i, chunk in enumerate(text_chunks):
                    await self.max_client.send_message(
                        chat_id=max_channel_id,
                        text=chunk,
                        format="html",
                    )
                    if i < len(text_chunks) - 1:
                        await asyncio.sleep(1)  # Rate limit between chunks
                    
            case MediaType.PHOTO:
                await self._transfer_photo(message, max_channel_id, text_chunks)
            case MediaType.VIDEO:
                await self._transfer_video(message, max_channel_id, text_chunks)
            case MediaType.AUDIO:
                await self._transfer_audio(message, max_channel_id, text_chunks)
            case MediaType.FILE:
                await self._transfer_file(message, max_channel_id, text_chunks)
            case MediaType.UNSUPPORTED:
                # Unsupported - send as text only
                for i, chunk in enumerate(text_chunks):
                    await self.max_client.send_message(
                        chat_id=max_channel_id,
                        text=chunk,
                        format="html",
                    )
                    if i < len(text_chunks) - 1:
                        await asyncio.sleep(1)
            case _:
                # Fallback - send as text only
                for i, chunk in enumerate(text_chunks):
                    await self.max_client.send_message(
                        chat_id=max_channel_id,
                        text=chunk,
                        format="html",
                    )
                    if i < len(text_chunks) - 1:
                        await asyncio.sleep(1)

    async def _transfer_photo(
        self,
        message: Message,
        max_channel_id: str | int | int,
        text_chunks: list[str],
    ) -> None:
        """Transfer a photo post."""
        # Download photo to BytesIO
        buf = io.BytesIO()
        await message.download_media(file=buf)
        buf.seek(0)
        photo_bytes = buf.read()

        if not photo_bytes or len(photo_bytes) == 0:
            logger.warning(f"Empty photo bytes for message {message.id}, skipping")
            raise MaxAPIError("Failed to download photo: empty bytes")

        logger.info(f"Downloaded photo: {len(photo_bytes)} bytes")

        # Upload to Max
        token = await self.max_client.upload_image(photo_bytes)

        # Send first chunk with photo, remaining chunks as separate messages
        first_chunk = text_chunks[0] if text_chunks else ""
        attachment = {"type": "image", "payload": {"token": token}}
        
        await self.max_client.send_message(
            chat_id=max_channel_id,
            text=first_chunk,
            attachments=[attachment],
            format="html",
        )
        
        # Send remaining text chunks
        for chunk in text_chunks[1:]:
            await asyncio.sleep(1)
            await self.max_client.send_message(
                chat_id=max_channel_id,
                text=chunk,
                format="html",
            )

    async def _transfer_video(
        self,
        message: Message,
        max_channel_id: str | int | int,
        text_chunks: list[str],
    ) -> None:
        """Transfer a video post."""
        # Download video to BytesIO
        buf = io.BytesIO()
        await message.download_media(file=buf)
        buf.seek(0)
        video_bytes = buf.read()

        if not video_bytes or len(video_bytes) == 0:
            logger.warning(f"Empty video bytes for message {message.id}, skipping")
            raise MaxAPIError("Failed to download video: empty bytes")

        logger.info(f"Downloaded video: {len(video_bytes)} bytes")

        # Upload to Max
        token = await self.max_client.upload_video(video_bytes)

        # Send first chunk with video, remaining chunks as separate messages
        first_chunk = text_chunks[0] if text_chunks else ""
        attachment = {"type": "video", "payload": {"token": token}}
        
        await self.max_client.send_message(
            chat_id=max_channel_id,
            text=first_chunk,
            attachments=[attachment],
            format="html",
        )
        
        # Send remaining text chunks
        for chunk in text_chunks[1:]:
            await asyncio.sleep(1)
            await self.max_client.send_message(
                chat_id=max_channel_id,
                text=chunk,
                format="html",
            )

    async def _transfer_audio(
        self,
        message: Message,
        max_channel_id: str | int | int,
        text_chunks: list[str],
    ) -> None:
        """Transfer an audio post."""
        # Download audio to BytesIO
        buf = io.BytesIO()
        await message.download_media(file=buf)
        buf.seek(0)
        audio_bytes = buf.read()

        if not audio_bytes or len(audio_bytes) == 0:
            logger.warning(f"Empty audio bytes for message {message.id}, skipping")
            raise MaxAPIError("Failed to download audio: empty bytes")

        logger.info(f"Downloaded audio: {len(audio_bytes)} bytes")

        # Upload to Max
        token = await self.max_client.upload_audio(audio_bytes)

        # Send first chunk with audio, remaining chunks as separate messages
        first_chunk = text_chunks[0] if text_chunks else ""
        attachment = {"type": "audio", "payload": {"token": token}}
        
        await self.max_client.send_message(
            chat_id=max_channel_id,
            text=first_chunk,
            attachments=[attachment],
            format="html",
        )
        
        # Send remaining text chunks
        for chunk in text_chunks[1:]:
            await asyncio.sleep(1)
            await self.max_client.send_message(
                chat_id=max_channel_id,
                text=chunk,
                format="html",
            )

    async def _transfer_file(
        self,
        message: Message,
        max_channel_id: str | int | int,
        text_chunks: list[str],
    ) -> None:
        """Transfer a file/document post."""
        # Download file to BytesIO
        buf = io.BytesIO()
        await message.download_media(file=buf)
        buf.seek(0)
        file_bytes = buf.read()

        if not file_bytes or len(file_bytes) == 0:
            logger.warning(f"Empty file bytes for message {message.id}, skipping")
            raise MaxAPIError("Failed to download file: empty bytes")

        logger.info(f"Downloaded file: {len(file_bytes)} bytes")

        # Upload to Max
        token = await self.max_client.upload_file(file_bytes)

        # Send first chunk with file, remaining chunks as separate messages
        first_chunk = text_chunks[0] if text_chunks else ""
        attachment = {"type": "file", "payload": {"token": token}}
        
        await self.max_client.send_message(
            chat_id=max_channel_id,
            text=first_chunk,
            attachments=[attachment],
            format="html",
        )
        
        # Send remaining text chunks
        for chunk in text_chunks[1:]:
            await asyncio.sleep(1)
            await self.max_client.send_message(
                chat_id=max_channel_id,
                text=chunk,
                format="html",
            )

    async def _transfer_album(
        self,
        client,
        first_message: Message,
        tg_channel: str,
        max_channel_id: str | int | int,
        processed_group_ids: set,
    ) -> None:
        """
        Transfer an album (grouped messages) as a single post.

        Args:
            client: Telethon client
            first_message: First message of the album
            tg_channel: Channel name
            max_channel_id: Max channel ID
            processed_group_ids: Set of processed group IDs

        Raises:
            MaxAPIError: If posting fails
        """
        group_id = first_message.grouped_id
        if not group_id:
            await self._transfer_single_post(first_message, max_channel_id)
            return

        # Mark as processed
        processed_group_ids.add(group_id)

        # Collect all messages in the album
        # Note: Telethon iter_messages doesn't support fetching by grouped_id directly
        # We'll upload the first message's media and send with its text
        # Full album support would require additional API calls

        # For now, transfer as single post with first media
        await self._transfer_single_post(first_message, max_channel_id)

        # TODO: Implement full album support with all media items
        # This would require:
        # 1. Fetching all messages with the same grouped_id
        # 2. Downloading all media files
        # 3. Uploading all to Max
        # 4. Sending as a single message with multiple attachments

    async def _notify_progress(
        self,
        callback: Optional[ProgressCallback],
        result: TransferResult,
        post_id: int,
        skip_reason: Optional[str],
    ) -> None:
        """
        Notify progress callback if provided.

        Args:
            callback: Progress callback function
            result: Current transfer result
            post_id: Current post ID
            skip_reason: Reason if skipped, None otherwise
        """
        if callback:
            try:
                completed = result.success + result.failed + result.skipped + result.duplicates
                await callback(
                    current=completed,
                    total=result.total,
                    success=result.success,
                    failed=result.failed,
                    skipped=result.skipped,
                )
            except Exception as e:
                logger.error(f"Progress callback error: {e}")

    def abort(self) -> None:
        """Signal the transfer to abort at the next iteration."""
        self._abort_flag = True
        logger.info("Abort flag set for transfer engine")
