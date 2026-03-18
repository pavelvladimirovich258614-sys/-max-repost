"""Content filter for detecting spam/advertising in posts."""

import re
from telethon.tl.types import Message


def should_skip_post(message: Message) -> tuple[bool, str]:
    """
    Check if post should be skipped as spam/junk/advertising.
    
    Returns:
        Tuple of (should_skip, reason)
    """
    text = message.raw_text or ""
    text_lower = text.lower()
    
    # 1. Skip very short text-only messages (no media)
    has_media = bool(
        message.photo or message.video or message.audio or 
        message.voice or message.document
    )
    if not has_media and len(text.strip()) < 5:
        return True, "too_short"
    
    # 2. Check for advertising markers
    ad_markers = [
        "реклама", "#ad", "#реклама", "промокод", "скидка до",
        "перейди по ссылке", "подпишись на канал", "заказать рекламу",
        "рекламный пост", "спонсор", "партнерский пост", "affiliate",
    ]
    
    for marker in ad_markers:
        if marker in text_lower:
            return True, f"advertising_marker: {marker}"
    
    # 3. Check for too many links (more than 5 = likely spam)
    url_pattern = r'https?://[^\s]+|t\.me/[^\s]+|@\w+'
    urls = re.findall(url_pattern, text)
    if len(urls) > 5:
        return True, f"too_many_links: {len(urls)}"
    
    # 4. Check for forwarded content from other channels (repost)
    if message.forward and message.forward.channel_id:
        return True, "forwarded_from_channel"
    
    return False, ""
