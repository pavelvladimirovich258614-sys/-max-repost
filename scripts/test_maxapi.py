"""Test maxapi library for Max API interaction.

This script tests the third-party maxapi library to see if it provides
better access to chat information than direct HTTP calls.
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

# Load .env from project root
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(project_root, ".env"))

ACCESS_TOKEN = os.getenv("MAX_ACCESS_TOKEN")

if not ACCESS_TOKEN:
    print("ERROR: MAX_ACCESS_TOKEN not found in .env")
    sys.exit(1)


def test_maxapi_import():
    """Test if maxapi library is available."""
    try:
        import maxapi
        print(f"✓ maxapi library found: {maxapi.__file__}")
        return maxapi
    except ImportError as e:
        print(f"✗ maxapi library not found: {e}")
        print("\nTo install: pip install maxapi")
        return None


async def test_with_maxapi():
    """Test maxapi library methods."""
    maxapi = test_maxapi_import()
    if not maxapi:
        return
    
    print(f"\n{'='*60}")
    print("Testing maxapi library...")
    print(f"Token: {ACCESS_TOKEN[:20]}...")
    
    try:
        # Try to initialize Bot
        bot = maxapi.Bot(ACCESS_TOKEN)
        print(f"✓ Bot initialized: {bot}")
        
        # Try get_me
        print("\n--- get_me() ---")
        try:
            me = await bot.get_me()
            print(f"✓ get_me result: {me}")
        except Exception as e:
            print(f"✗ get_me failed: {e}")
        
        # Try get_chats
        print("\n--- get_chats() ---")
        try:
            chats = await bot.get_chats()
            print(f"✓ get_chats result: {chats}")
            
            # Try to iterate over chats
            if hasattr(chats, '__iter__'):
                for chat in chats:
                    print(f"  Chat: {chat}")
                    # Try to get chat attributes
                    if hasattr(chat, 'id'):
                        print(f"    ID: {chat.id}")
                    if hasattr(chat, 'title'):
                        print(f"    Title: {chat.title}")
                    if hasattr(chat, 'name'):
                        print(f"    Name: {chat.name}")
        except Exception as e:
            print(f"✗ get_chats failed: {e}")
        
        # Try other methods that might exist
        print("\n--- Exploring available methods ---")
        methods = [m for m in dir(bot) if not m.startswith('_')]
        print(f"Available methods: {methods}")
        
        # Try methods that might give us chat info
        for method_name in ['get_updates', 'updates', 'getDialogs', 'get_dialogs']:
            if hasattr(bot, method_name):
                print(f"\n--- Trying {method_name}() ---")
                try:
                    method = getattr(bot, method_name)
                    result = await method()
                    print(f"✓ {method_name} result: {result}")
                except Exception as e:
                    print(f"✗ {method_name} failed: {e}")
        
        # Try direct HTTP through maxapi if available
        if hasattr(bot, 'session') or hasattr(bot, '_session'):
            print("\n--- Checking internal session ---")
            session = getattr(bot, 'session', None) or getattr(bot, '_session', None)
            print(f"Session: {session}")
            
    except Exception as e:
        print(f"✗ Error with maxapi: {e}")
        import traceback
        traceback.print_exc()


async def test_with_aiohttp():
    """Fallback: test with direct aiohttp calls."""
    import aiohttp
    
    print(f"\n{'='*60}")
    print("Testing with direct aiohttp (fallback)...")
    
    BASE_URL = "https://platform-api.max.ru"
    headers = {"Authorization": ACCESS_TOKEN}
    
    async with aiohttp.ClientSession() as session:
        # Test /me
        print("\n--- GET /me ---")
        try:
            async with session.get(f"{BASE_URL}/me", headers=headers) as resp:
                print(f"Status: {resp.status}")
                data = await resp.json()
                print(f"Response: {data}")
        except Exception as e:
            print(f"Error: {e}")
        
        # Test /chats
        print("\n--- GET /chats ---")
        try:
            async with session.get(f"{BASE_URL}/chats", headers=headers) as resp:
                print(f"Status: {resp.status}")
                data = await resp.json()
                print(f"Response: {data}")
        except Exception as e:
            print(f"Error: {e}")
        
        # Test /updates
        print("\n--- GET /updates ---")
        try:
            async with session.get(
                f"{BASE_URL}/updates", 
                headers=headers,
                params={"limit": 100}
            ) as resp:
                print(f"Status: {resp.status}")
                data = await resp.json()
                print(f"Response: {data}")
        except Exception as e:
            print(f"Error: {e}")


async def main():
    """Main function."""
    print("Max API Library Test")
    print("="*60)
    
    # Try maxapi first
    await test_with_maxapi()
    
    # Then try direct HTTP
    await test_with_aiohttp()


if __name__ == "__main__":
    asyncio.run(main())
