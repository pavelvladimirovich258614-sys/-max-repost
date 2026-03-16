"""Core Engine for transferring posts from Telegram to Max 1:1.

This module handles the complete transfer workflow:
- Fetching posts from Telegram via Telethon
- Downloading media (photos, videos, documents)
- Uploading media to Max via API
- Posting content to Max channels
- Progress tracking and error handling
"""

import asyncio
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


# =============================================================================
# Markup Conversion (Max API format)
# =============================================================================

def entities_to_max_markup(entities: list) -> list[dict]:
    """
    Convert Telegram entities to Max API markup format.
    
    Max API uses markup array with offset/length instead of markdown symbols.
    This avoids text corruption from overlapping entities.
    
    Args:
        entities: List of Telegram message entities from Telethon
        
    Returns:
        List of Max markup objects: [{"type": "strong", "from": 0, "length": 5}, ...]
    """
    if not entities:
        return []
    
    markup = []
    for entity in entities:
        item = {"from": entity.offset, "length": entity.length}
        
        if isinstance(entity, MessageEntityBold):
            item["type"] = "strong"
        elif isinstance(entity, MessageEntityItalic):
            item["type"] = "emphasized"
        elif isinstance(entity, MessageEntityCode):
            item["type"] = "monospaced"
        elif isinstance(entity, MessageEntityPre):
            item["type"] = "monospaced"
        elif isinstance(entity, MessageEntityTextUrl):
            item["type"] = "link"
            item["url"] = entity.url
        elif isinstance(entity, MessageEntityUrl):
            item["type"] = "link"
            # URL is the text itself, extracted at send time
            item["url"] = None  # Will be filled from text
        elif isinstance(entity, MessageEntityStrike):
            item["type"] = "strikethrough"
        elif isinstance(entity, MessageEntityUnderline):
            # Max doesn't have underline, use emphasized as fallback
            item["type"] = "emphasized"
        else:
            # Unknown entity type - skip
            continue
        
        markup.append(item)
    
    # Fill in URLs for MessageEntityUrl type
    # This needs to be done after we have the text
    return markup


def finalize_markup(text: str, markup: list[dict]) -> list[dict]:
    """
    Finalize markup by filling in URL values for MessageEntityUrl types.
    
    Args:
        text: The message text
        markup: Partially built markup array
        
    Returns:
        Final markup array with all URLs filled in
    """
    result = []
    for item in markup:
        if item.get("url") is None and item.get("type") == "link":
            # This was a MessageEntityUrl, extract URL from text
            start = item["from"]
            length = item["length"]
            item["url"] = text[start:start + length]
        result.append(item)
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
    skipped: int  # Skipped (unsupported types)
    errors: list[TransferError] = field(default_factory=list)

    @property
    def progress_percent(self) -> int:
        """Calculate completion percentage."""
        if self.total == 0:
            return 0
        completed = self.success + self.failed + self.skipped
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
                return MediaType.AUDIO
            if isinstance(attr, DocumentAttributeFilename):
                # Check extension
                filename = attr.file_name.lower()
                if filename.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
                    return MediaType.PHOTO
                if filename.endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm')):
                    return MediaType.VIDEO
                if filename.endswith(('.mp3', '.ogg', '.wav', '.flac', '.m4a')):
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
    # Skip empty messages
    if not message.text and not message.media:
        return True, "empty message"

    # Skip service messages
    if message.action:
        return True, "service message"

    # Check unsupported media types
    media_type = detect_media_type(message)
    if media_type == MediaType.UNSUPPORTED:
        if isinstance(message.media, MessageMediaWebPage):
            return True, "link preview"
        return True, f"unsupported media type: {type(message.media).__name__}"

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
    ):
        """
        Initialize the transfer engine.

        Args:
            telethon_client: TelethonChannelClient instance
            max_api_client: MaxClient instance
            db_session: Optional SQLAlchemy session for tracking
        """
        self.telethon = telethon_client
        self.max_client = max_api_client
        self.db_session = db_session
        self._abort_flag = False

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

                # Check if we should skip this message
                should_skip, skip_reason = should_skip_message(message)
                if should_skip:
                    result.skipped += 1
                    logger.info(f"Skipping post {message.id}: reason={skip_reason}, text_preview='{(message.text or '')[:50]}...'")
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
                    else:
                        # Single post
                        await self._transfer_single_post(
                            message,
                            max_channel_id,
                        )
                        result.success += 1
                        logger.info(f"Transferred post {message.id}")

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

        Args:
            message: Telethon Message object
            max_channel_id: Max channel ID

        Raises:
            MaxAPIError: If posting fails
        """
        media_type = detect_media_type(message)
        text = message.text or ""
        
        # Convert Telegram entities to Max markup format
        markup = None
        if message.entities:
            raw_markup = entities_to_max_markup(message.entities)
            markup = finalize_markup(text, raw_markup)

        # Handle posts based on media type
        match media_type:
            case MediaType.TEXT:
                # Text-only post - send without attachments
                await self.max_client.send_message(
                    chat_id=max_channel_id,
                    text=text,
                    markup=markup,
                )
            case MediaType.PHOTO:
                await self._transfer_photo(message, max_channel_id, text, markup)
            case MediaType.VIDEO:
                await self._transfer_video(message, max_channel_id, text, markup)
            case MediaType.AUDIO:
                await self._transfer_audio(message, max_channel_id, text, markup)
            case MediaType.FILE:
                await self._transfer_file(message, max_channel_id, text, markup)
            case MediaType.UNSUPPORTED:
                # Unsupported - send as text only
                await self.max_client.send_message(
                    chat_id=max_channel_id,
                    text=text,
                    markup=markup,
                )
            case _:
                # Fallback - send as text only
                await self.max_client.send_message(
                    chat_id=max_channel_id,
                    text=text,
                    markup=markup,
                )

    async def _transfer_photo(
        self,
        message: Message,
        max_channel_id: str | int | int,
        text: str,
        markup: list[dict] | None = None,
    ) -> None:
        """Transfer a photo post."""
        # Download photo to BytesIO - Telethon returns bytes when passed BytesIO
        buf = io.BytesIO()
        await message.download_media(file=buf)
        buf.seek(0)
        photo_bytes = buf.read()

        if not photo_bytes or len(photo_bytes) == 0:
            logger.warning(f"Empty photo bytes for message {message.id}, skipping")
            raise MaxAPIError("Failed to download photo: empty bytes")

        logger.info(f"Downloaded photo: {len(photo_bytes)} bytes, first 4: {photo_bytes[:4]}")

        # Upload to Max
        token = await self.max_client.upload_image(photo_bytes)

        # Send message with attachment in new Max API format
        attachment = {"type": "image", "payload": {"token": token}}
        await self.max_client.send_message(
            chat_id=max_channel_id,
            text=text,
            attachments=[attachment] if token else None,
            markup=markup,
        )

    async def _transfer_video(
        self,
        message: Message,
        max_channel_id: str | int | int,
        text: str,
        markup: list[dict] | None = None,
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

        logger.info(f"Downloaded video: {len(video_bytes)} bytes, first 4: {video_bytes[:4]}")

        # Upload to Max
        token = await self.max_client.upload_video(video_bytes)

        # Send message with attachment in new Max API format
        attachment = {"type": "video", "payload": {"token": token}}
        await self.max_client.send_message(
            chat_id=max_channel_id,
            text=text,
            attachments=[attachment] if token else None,
            markup=markup,
        )

    async def _transfer_audio(
        self,
        message: Message,
        max_channel_id: str | int | int,
        text: str,
        markup: list[dict] | None = None,
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

        logger.info(f"Downloaded audio: {len(audio_bytes)} bytes, first 4: {audio_bytes[:4]}")

        # Upload to Max
        token = await self.max_client.upload_audio(audio_bytes)

        # Send message with attachment in new Max API format
        attachment = {"type": "audio", "payload": {"token": token}}
        await self.max_client.send_message(
            chat_id=max_channel_id,
            text=text,
            attachments=[attachment] if token else None,
            markup=markup,
        )

    async def _transfer_file(
        self,
        message: Message,
        max_channel_id: str | int | int,
        text: str,
        markup: list[dict] | None = None,
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

        logger.info(f"Downloaded file: {len(file_bytes)} bytes, first 4: {file_bytes[:4]}")

        # Upload to Max
        token = await self.max_client.upload_file(file_bytes)

        # Send message with attachment in new Max API format
        attachment = {"type": "file", "payload": {"token": token}}
        await self.max_client.send_message(
            chat_id=max_channel_id,
            text=text,
            attachments=[attachment] if token else None,
            markup=markup,
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
                completed = result.success + result.failed + result.skipped
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
