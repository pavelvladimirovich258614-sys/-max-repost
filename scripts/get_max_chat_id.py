"""Get Max chat_id via Updates API (Long Polling approach).

When bot is added to a channel, Max API sends an update event.
This script fetches recent updates to find the chat_id.
"""

import asyncio
import os
import sys

import aiohttp
from dotenv import load_dotenv

# Load .env from project root
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(project_root, ".env"))

ACCESS_TOKEN = os.getenv("MAX_ACCESS_TOKEN")
BASE_URL = "https://platform-api.max.ru"

if not ACCESS_TOKEN:
    print("ERROR: MAX_ACCESS_TOKEN not found in .env")
    sys.exit(1)


async def get_updates(session: aiohttp.ClientSession, limit: int = 100, timeout: int = 5):
    """Get updates from Max API (like Telegram bot's getUpdates)."""
    headers = {"Authorization": ACCESS_TOKEN}
    params = {"limit": limit, "timeout": timeout}
    
    try:
        async with session.get(
            f"{BASE_URL}/updates",
            headers=headers,
            params=params
        ) as resp:
            print(f"\n{'='*60}")
            print(f"GET {BASE_URL}/updates")
            print(f"Status: {resp.status}")
            
            if resp.status == 200:
                data = await resp.json()
                print(f"Response: {data}")
                return data
            else:
                text = await resp.text()
                print(f"Error response: {text}")
                return None
    except Exception as e:
        print(f"Error getting updates: {e}")
        return None


async def get_chats(session: aiohttp.ClientSession):
    """Get chats list from Max API."""
    headers = {"Authorization": ACCESS_TOKEN}
    
    try:
        async with session.get(
            f"{BASE_URL}/chats",
            headers=headers
        ) as resp:
            print(f"\n{'='*60}")
            print(f"GET {BASE_URL}/chats")
            print(f"Status: {resp.status}")
            
            if resp.status == 200:
                data = await resp.json()
                print(f"Response: {data}")
                return data
            else:
                text = await resp.text()
                print(f"Error response: {text}")
                return None
    except Exception as e:
        print(f"Error getting chats: {e}")
        return None


async def get_me(session: aiohttp.ClientSession):
    """Get bot info from Max API."""
    headers = {"Authorization": ACCESS_TOKEN}
    
    try:
        async with session.get(
            f"{BASE_URL}/me",
            headers=headers
        ) as resp:
            print(f"\n{'='*60}")
            print(f"GET {BASE_URL}/me")
            print(f"Status: {resp.status}")
            
            if resp.status == 200:
                data = await resp.json()
                print(f"Response: {data}")
                return data
            else:
                text = await resp.text()
                print(f"Error response: {text}")
                return None
    except Exception as e:
        print(f"Error getting me: {e}")
        return None


async def main():
    """Main function to fetch updates and find chat_id."""
    print(f"Max API Chat ID Finder")
    print(f"Token: {ACCESS_TOKEN[:20]}...")
    
    async with aiohttp.ClientSession() as session:
        # 1. Get bot info
        print("\n" + "="*60)
        print("STEP 1: Getting bot info...")
        me = await get_me(session)
        
        if me:
            print(f"\n✓ Bot found: {me.get('name', 'Unknown')}")
        
        # 2. Get chats list
        print("\n" + "="*60)
        print("STEP 2: Getting chats list...")
        chats = await get_chats(session)
        
        if chats:
            chats_list = chats.get("chats", chats.get("items", []))
            print(f"\n✓ Found {len(chats_list)} chats")
            for chat in chats_list:
                print(f"  - ID: {chat.get('id')}, Name: {chat.get('name')}, Type: {chat.get('type')}")
        
        # 3. Get updates (events when bot was added to channels)
        print("\n" + "="*60)
        print("STEP 3: Getting updates (events)...")
        updates = await get_updates(session)
        
        if updates:
            # Try different possible structures
            updates_list = (
                updates.get("updates", []) or
                updates.get("events", []) or
                updates.get("items", []) or
                updates.get("result", [])
            )
            
            print(f"\n✓ Found {len(updates_list)} updates")
            
            chat_ids_found = set()
            for update in updates_list:
                print(f"\n  Update: {update}")
                
                # Try to find chat_id in various places
                # Structure might be: update.message.chat.id or update.chat.id etc.
                message = update.get("message", {})
                chat = message.get("chat") or update.get("chat") or update.get("channel")
                
                if chat:
                    chat_id = chat.get("id")
                    chat_title = chat.get("title") or chat.get("name", "Unknown")
                    if chat_id:
                        chat_ids_found.add((chat_id, chat_title))
                        print(f"  ✓✓✓ FOUND CHAT ID: {chat_id} ({chat_title})")
            
            if chat_ids_found:
                print("\n" + "="*60)
                print("SUMMARY: Found chat IDs:")
                for cid, title in chat_ids_found:
                    print(f"  {cid} - {title}")
            else:
                print("\n✗ No chat IDs found in updates")
                print("\nPossible reasons:")
                print("  - Bot was added to channel before updates were fetched")
                print("  - Updates expired (Max API might have short retention)")
                print("  - Try removing and re-adding bot to channel, then run again")


if __name__ == "__main__":
    asyncio.run(main())
