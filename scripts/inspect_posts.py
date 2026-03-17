import asyncio
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from bot.core.telethon_client import TelethonChannelClient
from config.settings import settings

def safe_repr(text, max_len=100):
    """Safely represent text for console output."""
    if text is None:
        return "None"
    preview = text[:max_len] if len(text) > max_len else text
    # Encode to ASCII with replacement for unprintable chars
    safe = preview.encode('ascii', 'replace').decode('ascii')
    return repr(safe)

async def main():
    client = TelethonChannelClient(
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
        phone=settings.telegram_phone
    )
    tc = await client._get_client()
    
    channel = await tc.get_entity("@Novopoltsev_Pavel")
    
    # Получаем первые 10 постов (oldest first)
    messages = await tc.get_messages(channel, limit=10, reverse=True)
    
    for msg in messages:
        print(f"\n{'='*60}")
        print(f"Post ID: {msg.id}")
        print(f"Date: {msg.date}")
        print(f"raw_text: {safe_repr(msg.raw_text)}")
        print(f"message (attr): {safe_repr(msg.message)}")
        print(f"media: {type(msg.media).__name__ if msg.media else 'None'}")
        print(f"action: {type(msg.action).__name__ if msg.action else 'None'}")
        print(f"photo: {bool(msg.photo)}")
        print(f"document: {bool(msg.document)}")
        print(f"grouped_id: {msg.grouped_id}")
        
    await tc.disconnect()

asyncio.run(main())
