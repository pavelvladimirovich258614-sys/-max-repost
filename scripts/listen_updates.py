"""Listen for Max API updates in real-time (long polling).

This script continuously polls the /updates endpoint to catch events like:
- bot_added: When bot is added to a channel
- bot_removed: When bot is removed from a channel
- bot_started: When user starts conversation with bot
- message_created: When a message is sent in a channel where bot is member
- message_callback: When user clicks a button
- chat_title_changed: When channel title changes

Usage:
    python scripts/listen_updates.py

While this is running:
    1. Remove bot from Max channel
    2. Add bot back to channel
    3. Send a message in the channel
    4. Watch the events appear here!
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

# Load .env from project root
project_root = Path(__file__).parent.parent
load_dotenv(project_root / ".env")

TOKEN = os.getenv("MAX_ACCESS_TOKEN")
BASE = "https://platform-api.max.ru"

if not TOKEN:
    print("ERROR: MAX_ACCESS_TOKEN not found in .env")
    sys.exit(1)


async def listen():
    """Listen for updates from Max API using long polling."""
    headers = {"Authorization": TOKEN}
    marker = None

    print("=" * 70)
    print("MAX API REAL-TIME UPDATE LISTENER")
    print("=" * 70)
    print(f"Token: {TOKEN[:20]}...")
    print(f"Base URL: {BASE}")
    print("=" * 70)
    print("\nListening for updates... (Press Ctrl+C to stop)")
    print("\nNow go to Max and:")
    print("  1. Remove bot from channel")
    print("  2. Add bot back to channel")
    print("  3. Write a message in channel")
    print("=" * 70)
    print()

    async with aiohttp.ClientSession() as session:
        while True:
            params = {
                "timeout": 30,
                "limit": 100,
                "types": "bot_added,bot_removed,bot_started,message_created,message_callback,chat_title_changed,user_added,user_removed"
            }
            if marker:
                params["marker"] = marker

            try:
                async with session.get(
                    f"{BASE}/updates",
                    headers=headers,
                    params=params
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        print(f"\n[ERROR] HTTP {resp.status}: {error_text[:200]}")
                        await asyncio.sleep(5)
                        continue

                    data = await resp.json()
                    marker = data.get("marker")
                    updates = data.get("updates", [])

                    if updates:
                        for u in updates:
                            print(f"\n{'='*70}")
                            update_type = u.get("update_type", "unknown")
                            timestamp = u.get("timestamp", "unknown")
                            print(f"📨 UPDATE TYPE: {update_type}")
                            print(f"🕐 TIMESTAMP: {timestamp}")
                            print(f"\nFull payload:")
                            print(json.dumps(u, indent=2, ensure_ascii=False))

                            # Try to extract chat_id
                            chat = None
                            if "chat" in u:
                                chat = u["chat"]
                            elif "message" in u and isinstance(u["message"], dict):
                                chat = u["message"].get("chat")
                            elif "recipient" in u:
                                chat = u["recipient"]

                            if chat and isinstance(chat, dict):
                                chat_id = chat.get("id")
                                chat_title = chat.get("title") or chat.get("name", "Unknown")
                                chat_type = chat.get("type", "unknown")
                                print(f"\n✅ EXTRACTED CHAT INFO:")
                                print(f"   Chat ID: {chat_id}")
                                print(f"   Chat Title: {chat_title}")
                                print(f"   Chat Type: {chat_type}")

                            print(f"{'='*70}")
                    else:
                        # No updates, print a dot to show we're still alive
                        print(".", end="", flush=True)

            except asyncio.CancelledError:
                print("\n\n[STOPPED] Listener cancelled")
                break
            except KeyboardInterrupt:
                print("\n\n[STOPPED] Keyboard interrupt")
                break
            except Exception as e:
                print(f"\n[ERROR] {e}")
                await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(listen())
    except KeyboardInterrupt:
        print("\n\nGoodbye!")
        sys.exit(0)
