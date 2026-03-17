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
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger
from telethon.errors import FloodWaitError
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
    DocumentAttributeAnimated,
    DocumentAttributeSticker,
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
    # DIAGNOSTIC: Log what media we received
    if message.media:
        logger.info(f"detect_media_type: message.id={message.id}, media_type={type(message.media).__name__}")
    
    if not message.media:
        return MediaType.TEXT  # Text only, no media

    if isinstance(message.media, MessageMediaPhoto):
        logger.info(f"detect_media_type: detected PHOTO for message {message.id}")
        return MediaType.PHOTO

    if isinstance(message.media, MessageMediaDocument):
        doc = message.media.document
        if not isinstance(doc, Document):
            return MediaType.FILE

        # Check attributes for more specific type
        for attr in doc.attributes:
            if isinstance(attr, DocumentAttributeVideo):
                logger.info(f"detect_media_type: detected VIDEO for message {message.id}")
                return MediaType.VIDEO
            if isinstance(attr, DocumentAttributeAudio):
                # Check if it's a voice message (voice=True)
                is_voice = getattr(attr, 'voice', False)
                logger.info(f"detect_media_type: detected AUDIO (voice={is_voice}) for message {message.id}")
                return MediaType.AUDIO
            if isinstance(attr, DocumentAttributeFilename):
                # Check extension
                filename = attr.file_name.lower()
                if filename.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
                    logger.info(f"detect_media_type: detected PHOTO (by filename) for message {message.id}")
                    return MediaType.PHOTO
                if filename.endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm')):
                    logger.info(f"detect_media_type: detected VIDEO (by filename) for message {message.id}")
                    return MediaType.VIDEO
                if filename.endswith(('.mp3', '.ogg', '.wav', '.flac', '.m4a', '.oga')):
                    logger.info(f"detect_media_type: detected AUDIO (by filename) for message {message.id}")
                    return MediaType.AUDIO

        logger.info(f"detect_media_type: detected FILE (generic document) for message {message.id}")
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
        logger.info(f"detect_media_type: detected UNSUPPORTED ({type(message.media).__name__}) for message {message.id}")
        return MediaType.UNSUPPORTED

    logger.info(f"detect_media_type: detected UNSUPPORTED (unknown type: {type(message.media).__name__}) for message {message.id}")
    return MediaType.UNSUPPORTED


def should_skip_message(message: Message, post_index: int = 0) -> tuple[bool, str]:
    """
    Check if a message should be skipped during transfer.

    Args:
        message: Telethon Message object
        post_index: Post ID for logging

    Returns:
        Tuple of (should_skip, reason)
    """
    import re
    from telethon.tl.types import (
        DocumentAttributeAnimated,
        DocumentAttributeSticker,
    )
    
    # Regex to detect text consisting only of emojis (and whitespace)
    # Covers: Miscellaneous Symbols, Dingbats, Emoticons, Transport/Map, Symbols, etc.
    EMOJI_ONLY_PATTERN = re.compile(
        r'^[\s\u2600-\u26FF\u2700-\u27BF\U0001F300-\U0001F5FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\U0001F700-\U0001F77F\U0001F780-\U0001F7FF\U0001F800-\U0001F8FF\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002702-\U000027B0\U000024C2-\U0001F251]+$',
        re.UNICODE
    )

    # === TEXT DETECTION ===
    # Check all possible text fields
    has_text = bool(
        message.text or 
        message.raw_text or 
        message.message
    )
    text_preview = (message.raw_text or message.text or message.message or "")[:50]
    
    # === MEDIA DETECTION (expanded) ===
    has_media = False
    media_type = "none"
    
    if message.media:
        if isinstance(message.media, MessageMediaPhoto):
            has_media = True
            media_type = "photo"
        elif isinstance(message.media, MessageMediaDocument):
            has_media = True
            doc = message.media.document
            if doc:
                # Determine document subtype
                for attr in doc.attributes:
                    if isinstance(attr, DocumentAttributeAudio):
                        if attr.voice:
                            media_type = "voice"
                        else:
                            media_type = "audio"
                        break
                    elif isinstance(attr, DocumentAttributeVideo):
                        if getattr(attr, 'round_message', False):
                            media_type = "video_note"
                        else:
                            media_type = "video"
                        break
                    elif isinstance(attr, DocumentAttributeAnimated):
                        media_type = "gif"
                        break
                    elif isinstance(attr, DocumentAttributeSticker):
                        media_type = "sticker"
                        break
                else:
                    media_type = "document"
            else:
                media_type = "document"
        elif isinstance(message.media, MessageMediaWebPage):
            # Webpage - not media, but text may exist
            has_media = False
            media_type = "webpage"
        elif isinstance(message.media, MessageMediaContact):
            has_media = True
            media_type = "contact"
        elif isinstance(message.media, MessageMediaPoll):
            has_media = False  # Skip polls
            media_type = "poll"
        else:
            # Other types (geo, etc.) - treat as media but may be unsupported
            has_media = True
            media_type = f"other:{type(message.media).__name__}"

    # === LOGGING: Show what we detected ===
    logger.info(
        f"Post {post_index} detection: "
        f"has_text={has_text}, has_media={has_media}, media_type={media_type}, "
        f"text_preview='{text_preview}...'"
    )

    # Skip empty messages (no text AND no media)
    if not has_text and not has_media:
        logger.info(f"Post {post_index}: SKIP CONDITION TRIGGERED - empty message (has_text={has_text}, has_media={has_media})")
        return True, "empty message (no text, no media)"
    
    # After the check, log media-only posts
    if has_media and not has_text:
        logger.info(f"Post {message.id}: media-only {media_type}, will transfer with empty text")

    # Skip service messages
    if message.action:
        logger.info(f"Post {post_index}: SKIP CONDITION TRIGGERED - service message: {type(message.action).__name__}")
        return True, f"service message: {type(message.action).__name__}"

    # === SIZE CHECKS for media documents ===
    if message.media and isinstance(message.media, MessageMediaDocument):
        doc = message.media.document
        if doc and hasattr(doc, 'size') and doc.size:
            size_mb = doc.size / (1024 * 1024)
            
            # Determine media type from attributes
            is_video = False
            is_audio = False
            is_video_note = False
            is_sticker = False
            is_gif = False
            
            for attr in doc.attributes:
                if isinstance(attr, DocumentAttributeVideo):
                    if getattr(attr, 'round_message', False):
                        is_video_note = True
                    else:
                        is_video = True
                elif isinstance(attr, DocumentAttributeAudio):
                    is_audio = True
                elif isinstance(attr, DocumentAttributeSticker):
                    is_sticker = True
                elif isinstance(attr, DocumentAttributeAnimated):
                    is_gif = True
            
            # Skip video notes
            if is_video_note:
                logger.info(f"Post {post_index}: SKIP - video_note not supported")
                return True, "video_note not supported"
            
            # Skip stickers
            if is_sticker:
                logger.info(f"Post {post_index}: SKIP - sticker not supported")
                return True, "sticker not supported"
            
            # Skip GIFs
            if is_gif:
                logger.info(f"Post {post_index}: SKIP - gif not supported")
                return True, "gif not supported"
            
            # Check video size limit (100 MB)
            if is_video and size_mb > 100:
                logger.info(f"Post {post_index}: SKIP - video too large: {size_mb:.1f}MB")
                return True, f"video too large: {size_mb:.1f}MB"
            
            # Check audio size limit (100 MB)
            if is_audio and size_mb > 100:
                logger.info(f"Post {post_index}: SKIP - audio too large: {size_mb:.1f}MB")
                return True, f"audio too large: {size_mb:.1f}MB"
            
            # Check generic file size limit (50 MB) - only for non-video, non-audio files
            if not is_video and not is_audio and size_mb > 50:
                logger.info(f"Post {post_index}: SKIP - file too large: {size_mb:.1f}MB")
                return True, f"file too large: {size_mb:.1f}MB"

    # === CONTENT FILTER: Text-only messages without media ===
    # Only filter if there is NO media (photos, videos, audio, etc. pass through)
    if not has_media and has_text:
        raw_text = message.raw_text or message.text or message.message or ""
        text_len = len(raw_text.strip())
        
        # Skip short text-only messages (likely chat chatter)
        if text_len < 50:
            preview = raw_text[:30].replace('\n', ' ')
            logger.info(f"Post {post_index}: SKIP - short text ({text_len} chars): '{preview}'")
            return True, f"short text: {text_len} chars"
        
        # Skip emoji-only text (no actual content)
        if EMOJI_ONLY_PATTERN.match(raw_text.strip()):
            logger.info(f"Post {post_index}: SKIP - text only emojis")
            return True, "text only emojis"

    # Check unsupported media types
    detected_type = detect_media_type(message)
    if detected_type == MediaType.UNSUPPORTED:
        skip_reason = "link preview" if isinstance(message.media, MessageMediaWebPage) else f"unsupported media: {type(message.media).__name__}"
        logger.info(f"Post {post_index}: SKIP CONDITION TRIGGERED - {skip_reason}")
        return True, skip_reason

    logger.info(f"Post {post_index}: WILL NOT SKIP - has_text={has_text}, has_media={has_media}, detected_type={detected_type}")
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
        
        # Track progress toward target (initialized here for visibility in final logs)
        transferred_count = 0
        junk_scanned = 0  # Safety: count junk messages scanned
        
        # Track start time for timeout
        start_time = time.time()
        TIMEOUT_SECONDS = 7200  # 2 hours

        try:
            # Get Telethon client
            client = await self.telethon._get_client()

            # Normalize channel name
            if tg_channel.startswith('@'):
                tg_channel = tg_channel[1:]

            # Determine target count
            target_count = None if count == "all" else int(count)
            
            # No iter_limit - scan until we find enough posts or hit safety limit
            # Safety limit junk_scanned=1000 protects from infinite loops
            iter_limit = None

            # Fetch messages (oldest first for correct order in Max)
            logger.info(f"Starting transfer: @{tg_channel} -> {max_channel_id}, target={count}")

            # Track albums (grouped_id) to avoid duplicate uploads
            processed_group_ids = set()
            MAX_JUNK_SCAN = 1000  # Safety limit: stop if scanned too much junk

            async for message in client.iter_messages(
                tg_channel,
                limit=iter_limit,  # No limit - scan all if needed
                reverse=True,  # Oldest first
            ):
                # Check for timeout (2 hours)
                elapsed = time.time() - start_time
                if elapsed > TIMEOUT_SECONDS:
                    logger.warning(f"Transfer timeout after 2 hours, stopping. Elapsed: {elapsed:.0f}s")
                    break
                
                if self._abort_flag:
                    logger.info("Transfer aborted by flag")
                    break
                
                # Safety check: too much junk scanned
                if target_count and junk_scanned > MAX_JUNK_SCAN:
                    logger.warning(f"Safety stop: scanned {junk_scanned} junk messages, stopping")
                    break
                
                # Check if we reached target
                if target_count and transferred_count >= target_count:
                    logger.info(f"Reached target of {target_count} posts, stopping")
                    break

                # === FILTER: Skip junk messages (service, empty) - don't count toward target ===
                # Quick check without full logging for performance
                is_junk = (
                    message.action is not None or  # Service messages
                    (not message.text and not message.raw_text and not message.media)  # Empty
                )
                if is_junk:
                    junk_scanned += 1
                    # Log first 10 junk messages in detail for debugging
                    if junk_scanned <= 10:
                        action_name = type(message.action).__name__ if message.action else None
                        logger.info(f"Junk #{junk_scanned}: id={message.id}, action={action_name}, text={bool(message.text)}, media={bool(message.media)}")
                    elif junk_scanned == 11:
                        logger.info("Junk logging suppressed after 10 messages, continuing count...")
                    continue

                # Now this is a real post - count it
                result.total += 1

                # === DIAGNOSTIC: Log all post details BEFORE skip check ===
                logger.info(
                    f"Post {message.id} raw data: "
                    f"id={message.id}, "
                    f"text={bool(message.text)}, "
                    f"raw_text={bool(message.raw_text)}, "
                    f"message={bool(message.message)}, "
                    f"media={type(message.media).__name__ if message.media else None}, "
                    f"document={bool(getattr(message, 'document', None))}, "
                    f"audio={bool(getattr(message, 'audio', None))}, "
                    f"voice={bool(getattr(message, 'voice', None))}, "
                    f"video={bool(getattr(message, 'video', None))}, "
                    f"photo={bool(message.photo) if hasattr(message, 'photo') else None}, "
                    f"file={bool(getattr(message, 'file', None))}, "
                    f"action={type(message.action).__name__ if message.action else None}, "
                    f"forward={bool(message.forward)}, "
                    f"grouped_id={message.grouped_id}"
                )
                if message.media:
                    logger.info(f"Post {message.id} media details: {message.media}")

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
                        # This counts toward target (user already paid for it)
                        transferred_count += 1
                        await self._notify_progress(
                            progress_callback, result, message.id, "already_transferred"
                        )
                        continue

                # Check if we should skip this message (unsupported media, etc.)
                should_skip, skip_reason = should_skip_message(message, post_index=message.id)
                if should_skip:
                    result.skipped += 1
                    logger.info(f"Skipping post {message.id}: reason={skip_reason}")
                    # This counts toward target (we tried but couldn't transfer)
                    transferred_count += 1
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
                    transferred_count += 1
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
                        transferred_count += 1
                        logger.info(f"Transferred album {message.grouped_id} (progress: {transferred_count}/{target_count if target_count else 'all'})")
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
                        transferred_count += 1
                        logger.info(f"Transferred post {message.id} (progress: {transferred_count}/{target_count if target_count else 'all'})")
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

                    # === Rate limiting between posts ===
                    # Base pause: 2 seconds (safe for 30 rps)
                    delay = 2.0
                    
                    # Media pause: +3 seconds if post has media (download + upload + send = 3 requests)
                    has_media = bool(message.media)
                    if has_media:
                        delay += 3.0
                    
                    # Long pause: 10 seconds every 50 posts (cool down from both APIs)
                    if transferred_count > 0 and transferred_count % 50 == 0:
                        delay = 10.0
                        logger.info(f"Rate limit pause: {int(delay)}s after post {transferred_count} (cooldown)")
                    else:
                        logger.info(f"Rate limit pause: {int(delay)}s after post {transferred_count}")
                    
                    await asyncio.sleep(delay)

                except FloodWaitError as e:
                    wait_time = e.seconds + 5
                    logger.warning(f"Telethon FloodWaitError: waiting {wait_time}s")
                    await asyncio.sleep(wait_time)
                    consecutive_errors += 1
                    # Don't count as failed, just retry this post
                    continue

                except MaxAPIError as e:
                    consecutive_errors += 1
                    result.failed += 1
                    transferred_count += 1  # Count toward target (we tried)
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
                    transferred_count += 1  # Count toward target (we tried)
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
                f"{result.failed} failed, {result.skipped} skipped, "
                f"{result.duplicates} duplicates, "
                f"junk_scanned={junk_scanned}"
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
        # SIZE CHECK - Before downloading
        if message.media and isinstance(message.media, MessageMediaDocument):
            doc = message.media.document
            if doc and hasattr(doc, 'size') and doc.size:
                size_mb = doc.size / (1024 * 1024)
                if size_mb > 100:
                    logger.warning(f"Video {message.id} exceeds size limit: {size_mb:.1f}MB > 100MB")
                    raise MaxAPIError(f"video too large: {size_mb:.1f}MB (max 100MB)")
        
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
        # SIZE CHECK - Before downloading
        if message.media and isinstance(message.media, MessageMediaDocument):
            doc = message.media.document
            if doc and hasattr(doc, 'size') and doc.size:
                size_mb = doc.size / (1024 * 1024)
                if size_mb > 100:
                    logger.warning(f"Audio {message.id} exceeds size limit: {size_mb:.1f}MB > 100MB")
                    raise MaxAPIError(f"audio too large: {size_mb:.1f}MB (max 100MB)")
        
        # STAGE 1 - Downloading
        logger.info(f"Downloading audio {message.id}...")
        try:
            buf = io.BytesIO()
            await message.download_media(file=buf)
            buf.seek(0)
            audio_bytes = buf.read()
            logger.info(f"Downloaded audio {message.id}: {len(audio_bytes)} bytes")
        except Exception as e:
            logger.error(f"Download failed for audio {message.id}: {e}", exc_info=True)
            raise

        if not audio_bytes or len(audio_bytes) == 0:
            logger.warning(f"Empty audio bytes for message {message.id}, skipping")
            raise MaxAPIError("Failed to download audio: empty bytes")

        # Warning for large files
        if len(audio_bytes) > 50 * 1024 * 1024:  # 50 MB
            logger.warning(f"Large audio file: {len(audio_bytes)} bytes, may take time...")

        # STAGE 2 - Upload
        logger.info(f"Uploading audio {message.id}, size={len(audio_bytes)} bytes")
        try:
            token = await self.max_client.upload_audio(audio_bytes)
            logger.info(f"Upload successful, token={token[:20]}...")
        except Exception as e:
            logger.error(f"Upload failed for audio {message.id}: {e}", exc_info=True)
            raise

        # STAGE 3 - Sending
        logger.info(f"Sending audio message to {max_channel_id}")
        try:
            # Send first chunk with audio, remaining chunks as separate messages
            first_chunk = text_chunks[0] if text_chunks else ""
            attachment = {"type": "audio", "payload": {"token": token}}

            await self.max_client.send_message(
                chat_id=max_channel_id,
                text=first_chunk,
                attachments=[attachment],
                format="html",
            )
            logger.info(f"Successfully sent audio message {message.id}")
        except Exception as e:
            logger.error(f"Send failed for audio {message.id}: {e}", exc_info=True)
            raise

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
        # SIZE CHECK - Before downloading
        if message.media and isinstance(message.media, MessageMediaDocument):
            doc = message.media.document
            if doc and hasattr(doc, 'size') and doc.size:
                size_mb = doc.size / (1024 * 1024)
                if size_mb > 50:
                    logger.warning(f"File {message.id} exceeds size limit: {size_mb:.1f}MB > 50MB")
                    raise MaxAPIError(f"file too large: {size_mb:.1f}MB (max 50MB)")
        
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
