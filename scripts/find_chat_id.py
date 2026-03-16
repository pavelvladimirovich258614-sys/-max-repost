"""Comprehensive script to find Max chat_id by all possible methods.

This script tries multiple approaches:
1. GET /chats - direct list
2. GET /chats/{chatId} - try with invite hash
3. GET /updates - recent events
4. GET /me - bot info
5. POST /messages with invite link (test)
"""

import asyncio
import os
import sys
from typing import Any

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


class Colors:
    """ANSI colors for terminal output."""
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'


def print_header(text: str):
    print(f"\n{Colors.HEADER}{'='*70}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{text}{Colors.ENDC}")
    print(f"{Colors.HEADER}{'='*70}{Colors.ENDC}")


def print_success(text: str):
    print(f"{Colors.OKGREEN}✓ {text}{Colors.ENDC}")


def print_error(text: str):
    print(f"{Colors.FAIL}✗ {text}{Colors.ENDC}")


def print_warning(text: str):
    print(f"{Colors.WARNING}⚠ {text}{Colors.ENDC}")


def print_info(text: str):
    print(f"{Colors.OKBLUE}ℹ {text}{Colors.ENDC}")


async def make_request(
    session: aiohttp.ClientSession,
    method: str,
    endpoint: str,
    **kwargs
) -> tuple[int, Any]:
    """Make HTTP request and return status + data."""
    url = f"{BASE_URL}{endpoint}"
    headers = kwargs.pop('headers', {})
    headers['Authorization'] = ACCESS_TOKEN
    
    try:
        async with session.request(method, url, headers=headers, **kwargs) as resp:
            status = resp.status
            try:
                data = await resp.json()
            except:
                data = await resp.text()
            return status, data
    except Exception as e:
        return -1, str(e)


async def method_1_chats_list(session: aiohttp.ClientSession):
    """Method 1: GET /chats - list all accessible chats."""
    print_header("METHOD 1: GET /chats")
    print_info("Fetching list of all chats accessible to the bot...")
    
    status, data = await make_request(session, "GET", "/chats")
    print(f"Status: {status}")
    print(f"Response: {data}")
    
    if status == 200:
        chats = data.get("chats", data.get("items", [])) if isinstance(data, dict) else []
        if chats:
            print_success(f"Found {len(chats)} chats")
            for chat in chats:
                cid = chat.get('id')
                name = chat.get('name', 'Unknown')
                ctype = chat.get('type', 'unknown')
                print(f"  • ID: {cid} | Name: {name} | Type: {ctype}")
            return chats
        else:
            print_warning("No chats found in response")
    else:
        print_error(f"Request failed: {data}")
    return []


async def method_2_chats_by_id(session: aiohttp.ClientSession, chat_id: str):
    """Method 2: GET /chats/{chatId} - try to get specific chat by ID/hash."""
    print_header(f"METHOD 2: GET /chats/{chat_id}")
    print_info(f"Trying to fetch chat by ID/hash: {chat_id}")
    
    status, data = await make_request(session, "GET", f"/chats/{chat_id}")
    print(f"Status: {status}")
    print(f"Response: {data}")
    
    if status == 200:
        print_success("Chat found!")
        return data
    else:
        print_error(f"Chat not found: {data}")
    return None


async def method_3_updates(session: aiohttp.ClientSession):
    """Method 3: GET /updates - recent events including bot additions."""
    print_header("METHOD 3: GET /updates")
    print_info("Fetching recent updates/events...")
    print_info("If bot was recently added to channel, it should appear here")
    
    status, data = await make_request(
        session, "GET", "/updates",
        params={"limit": 100, "timeout": 5}
    )
    print(f"Status: {status}")
    print(f"Response: {data}")
    
    chat_ids = []
    if status == 200:
        # Try different possible response structures
        updates = (
            data.get("updates", []) if isinstance(data, dict) else
            data if isinstance(data, list) else
            []
        )
        
        print_info(f"Found {len(updates)} updates")
        
        for update in updates:
            print(f"\n  Update: {update}")
            
            # Try to extract chat_id from various structures
            # Possible structures:
            # - update.message.chat.id
            # - update.chat.id
            # - update.channel.id
            # - update.event.chat.id
            
            chat = None
            if isinstance(update, dict):
                message = update.get("message", {})
                chat = (
                    message.get("chat") or
                    update.get("chat") or
                    update.get("channel") or
                    update.get("event", {}).get("chat")
                )
            
            if isinstance(chat, dict):
                cid = chat.get("id")
                title = chat.get("title") or chat.get("name", "Unknown")
                if cid:
                    chat_ids.append((cid, title))
                    print_success(f"EXTRACTED CHAT ID: {cid} ({title})")
        
        if not chat_ids:
            print_warning("No chat IDs found in updates")
            print_info("Try removing bot from channel and adding it back, then run again")
    else:
        print_error(f"Request failed: {data}")
    
    return chat_ids


async def method_4_bot_info(session: aiohttp.ClientSession):
    """Method 4: GET /me - bot information."""
    print_header("METHOD 4: GET /me")
    print_info("Fetching bot information...")
    
    status, data = await make_request(session, "GET", "/me")
    print(f"Status: {status}")
    print(f"Response: {data}")
    
    if status == 200:
        print_success(f"Bot: {data.get('name', 'Unknown')}")
        print_info(f"Bot ID: {data.get('id')}")
    else:
        print_error(f"Request failed: {data}")
    
    return data if status == 200 else None


async def method_5_send_by_invite(session: aiohttp.ClientSession, invite_hash: str):
    """Method 5: Try alternative parameters for sending."""
    print_header(f"METHOD 5: Alternative send parameters")
    print_warning("This is a test - will likely fail but shows all options tried")
    
    # Try different parameter names
    params_variants = [
        {"chat_id": invite_hash},
        {"channel_id": invite_hash},
        {"chat": invite_hash},
        {"peer": invite_hash},
        {"link": f"https://max.ru/join/{invite_hash}"},
    ]
    
    for params in params_variants:
        param_name = list(params.keys())[0]
        print_info(f"Trying with {param_name}={invite_hash[:20]}...")
        
        status, data = await make_request(
            session, "POST", "/messages",
            params=params,
            json={"text": "Test message"}
        )
        print(f"  Status: {status}")
        if status != 200:
            print(f"  Response: {str(data)[:200]}")


async def method_6_webhook_info(session: aiohttp.ClientSession):
    """Method 6: Check webhook settings (might contain chat info)."""
    print_header("METHOD 6: Webhook info")
    print_info("Trying /webhook endpoints...")
    
    # Try various webhook-related endpoints
    endpoints = [
        "/webhook",
        "/webhooks",
        "/getWebhookInfo",
        "/getWebhook",
    ]
    
    for endpoint in endpoints:
        print_info(f"Trying {endpoint}...")
        status, data = await make_request(session, "GET", endpoint)
        print(f"  Status: {status}")
        if status == 200:
            print(f"  Response: {data}")


async def main():
    """Run all methods to find chat_id."""
    print_header("MAX API CHAT ID FINDER")
    print_info(f"Token: {ACCESS_TOKEN[:20]}...")
    print_info(f"Base URL: {BASE_URL}")
    
    async with aiohttp.ClientSession() as session:
        # Method 4: Bot info (always works, good for auth check)
        bot_info = await method_4_bot_info(session)
        
        if not bot_info:
            print_error("Cannot connect to Max API. Check your token.")
            return
        
        # Method 1: Chats list
        chats = await method_1_chats_list(session)
        
        # Method 3: Updates
        chat_ids_from_updates = await method_3_updates(session)
        
        # Method 2: If user provided invite hash, try it
        invite_hash = "KzH6f71jyBYu4qYh2xCavXPHhlSatLqABA1dddhLAGM"
        print_info(f"\nTrying known invite hash: {invite_hash[:20]}...")
        chat_by_id = await method_2_chats_by_id(session, invite_hash)
        
        # Method 5: Alternative send (informational)
        await method_5_send_by_invite(session, invite_hash)
        
        # Method 6: Webhook (informational)
        await method_6_webhook_info(session)
        
        # Summary
        print_header("SUMMARY")
        
        all_chat_ids = set()
        
        if chats:
            for chat in chats:
                cid = chat.get('id')
                name = chat.get('name', 'Unknown')
                if cid:
                    all_chat_ids.add((str(cid), name, "/chats"))
        
        for cid, name in chat_ids_from_updates:
            all_chat_ids.add((str(cid), name, "/updates"))
        
        if all_chat_ids:
            print_success("Found chat IDs:")
            for cid, name, source in sorted(all_chat_ids):
                print(f"  • {cid} - {name} (from {source})")
        else:
            print_error("No chat IDs found!")
            print_warning("\nPossible solutions:")
            print("  1. Remove bot from channel and add it back")
            print("  2. Wait a few minutes and try again")
            print("  3. Check if bot has 'Write posts' permission in channel")
            print("  4. Try creating a test message through Max web app")
            print("  5. Contact Max API support")


if __name__ == "__main__":
    asyncio.run(main())
