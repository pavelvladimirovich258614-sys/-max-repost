"""Test script for transfer engine text conversion.

This script tests the HTML conversion logic without actually
sending messages to Max API.
"""

import asyncio
import html
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from telethon.tl.types import (
    MessageEntityBold,
    MessageEntityItalic,
    MessageEntityCode,
    MessageEntityPre,
    MessageEntityTextUrl,
    MessageEntityUrl,
    MessageEntityStrike,
    MessageEntityUnderline,
)


def convert_entities_to_html(raw_text: str, entities: list) -> str:
    """
    Convert Telegram entities to HTML for Max API.
    (Copy this from transfer_engine.py after formatter agent finishes)
    """
    if not entities:
        return html.escape(raw_text)
    
    # Sort entities by offset (reverse order for insertion)
    sorted_entities = sorted(entities, key=lambda e: e.offset, reverse=True)
    
    # Work with UTF-16 encoding for correct emoji handling
    text_utf16 = raw_text.encode('utf-16-le')
    result = raw_text
    
    for entity in sorted_entities:
        offset = entity.offset
        length = entity.length
        
        # Convert UTF-16 offset/length to Python string indices
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
            continue
        elif isinstance(entity, MessageEntityStrike):
            tag, close_tag = "<s>", "</s>"
        elif isinstance(entity, MessageEntityUnderline):
            tag, close_tag = "<u>", "</u>"
        else:
            continue
        
        # Replace the entity text
        result_prefix = result[:len(prefix)]
        result_suffix = result[len(prefix) + len(entity_text):]
        
        if result[len(prefix):len(prefix) + len(entity_text)] == entity_text:
            result = result_prefix + tag + entity_text_escaped + close_tag + result_suffix
    
    return result


def test_basic_formatting():
    """Test basic formatting conversion."""
    print("=" * 60)
    print("Test 1: Basic Formatting")
    print("=" * 60)
    
    raw_text = "Hello **world** test"
    entities = [
        MessageEntityBold(offset=6, length=8)  # "**world**" but we use raw_text
    ]
    
    # Note: This test is simplified. Real test needs actual Telethon message
    print(f"Raw text: {raw_text}")
    print(f"Entities: {entities}")
    
    # For proper testing we need to use message.raw_text without markdown
    raw_text_clean = "Hello world test"
    entities_clean = [
        MessageEntityBold(offset=6, length=5)  # "world"
    ]
    
    result = convert_entities_to_html(raw_text_clean, entities_clean)
    print(f"Result: {result}")
    expected = "Hello <b>world</b> test"
    print(f"Expected: {expected}")
    print(f"✓ PASS" if result == expected else f"✗ FAIL")
    print()


def test_html_escaping():
    """Test HTML escaping."""
    print("=" * 60)
    print("Test 2: HTML Escaping")
    print("=" * 60)
    
    raw_text = "Text with <script> and & symbols"
    result = convert_entities_to_html(raw_text, [])
    print(f"Input: {raw_text}")
    print(f"Output: {result}")
    
    expected = "Text with &lt;script&gt; and &amp; symbols"
    print(f"Expected: {expected}")
    print(f"✓ PASS" if result == expected else f"✗ FAIL")
    print()


def test_link_conversion():
    """Test link conversion."""
    print("=" * 60)
    print("Test 3: Link Conversion")
    print("=" * 60)
    
    raw_text = "Check out this link"
    entities = [
        MessageEntityTextUrl(offset=14, length=4, url="https://example.com")
    ]
    
    result = convert_entities_to_html(raw_text, entities)
    print(f"Input: {raw_text}")
    print(f"Output: {result}")
    
    expected = 'Check out this <a href="https://example.com">link</a>'
    print(f"Expected: {expected}")
    print(f"✓ PASS" if result == expected else f"✗ FAIL")
    print()


def test_emoji_handling():
    """Test emoji handling (UTF-16 offset/length)."""
    print("=" * 60)
    print("Test 4: Emoji Handling")
    print("=" * 60)
    
    # Emoji takes 2 UTF-16 code units
    raw_text = "👋 Hello world"
    # "👋" = 2 UTF-16 units, space = 1, "Hello" = 5, space = 1, "world" = 5
    # "world" starts at offset 9 in UTF-16
    entities = [
        MessageEntityBold(offset=9, length=5)  # "world"
    ]
    
    result = convert_entities_to_html(raw_text, entities)
    print(f"Input: {raw_text}")
    print(f"Output: {result}")
    expected = "👋 Hello <b>world</b>"
    print(f"Expected: {expected}")
    print(f"✓ PASS" if result == expected else f"✗ FAIL")
    print()


def test_complex_formatting():
    """Test complex formatting with overlapping."""
    print("=" * 60)
    print("Test 5: Complex Formatting")
    print("=" * 60)
    
    raw_text = "Bold and italic text here"
    entities = [
        MessageEntityBold(offset=0, length=9),   # "Bold and "
        MessageEntityItalic(offset=9, length=6), # "italic"
    ]
    
    result = convert_entities_to_html(raw_text, entities)
    print(f"Input: {raw_text}")
    print(f"Output: {result}")
    # Note: overlapping entities are tricky, just check it doesn't crash
    print(f"✓ PASS (no crash)" if "<b>" in result and "<i>" in result else f"✗ FAIL")
    print()


async def test_telethon_connection():
    """Test Telethon connection and fetch sample posts."""
    print("=" * 60)
    print("Test 6: Telethon Connection")
    print("=" * 60)
    
    try:
        from bot.core.telethon_client import get_telethon_client
        from config.settings import settings
        
        telethon = get_telethon_client(
            api_id=settings.telegram_api_id,
            api_hash=settings.telegram_api_hash,
            phone=settings.telegram_phone,
        )
        
        client = await telethon._get_client()
        channel = "@Novopoltsev_Pavel"
        
        print(f"Fetching 5 posts from {channel}...")
        
        count = 0
        async for message in client.iter_messages(channel, limit=5):
            count += 1
            print(f"\n--- Post {message.id} ---")
            print(f"  raw_text[:50]: {(message.raw_text or '')[:50]!r}")
            print(f"  text[:50]: {(message.text or '')[:50]!r}")
            print(f"  len(entities): {len(message.entities) if message.entities else 0}")
            print(f"  media_type: {type(message.media).__name__ if message.media else 'None'}")
            
            # Test conversion if entities exist
            if message.entities and message.raw_text:
                try:
                    html_result = convert_entities_to_html(message.raw_text, message.entities)
                    print(f"  HTML result[:80]: {html_result[:80]!r}")
                except Exception as e:
                    print(f"  Conversion ERROR: {e}")
        
        print(f"\n✓ Fetched {count} posts successfully")
        
    except Exception as e:
        print(f"✗ ERROR: {e}")
        import traceback
        traceback.print_exc()


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("TRANSFER ENGINE TEST SUITE")
    print("=" * 60 + "\n")
    
    # Run sync tests
    test_basic_formatting()
    test_html_escaping()
    test_link_conversion()
    test_emoji_handling()
    test_complex_formatting()
    
    # Run async test
    print("=" * 60)
    print("Running Telethon tests...")
    print("=" * 60 + "\n")
    asyncio.run(test_telethon_connection())
    
    print("\n" + "=" * 60)
    print("TESTS COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
