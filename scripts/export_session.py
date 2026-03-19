#!/usr/bin/env python3
"""Export existing SQLite Telethon session to StringSession.

Run this script ONCE on the server to convert the existing session file
to a session string that can be used with StringSession (in-memory).

Usage:
    python scripts/export_session.py
    
Output:
    SESSION_STRING: <long_string_here>
    
Copy the string and add to .env:
    TELETHON_SESSION_STRING=<long_string_here>
"""

import asyncio
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telethon import TelegramClient
from telethon.sessions import StringSession
from config.settings import settings


async def export_session():
    """Export existing session to string."""
    session_file = "user_session"
    
    if not os.path.exists(f"{session_file}.session"):
        print(f"Error: Session file '{session_file}.session' not found!")
        print("Make sure you have authorized Telethon first.")
        sys.exit(1)
    
    print("Loading existing session...")
    
    # Load existing SQLite session
    client = TelegramClient(
        session_file,  # This loads user_session.session
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    
    await client.connect()
    
    if not await client.is_user_authorized():
        print("Error: Session exists but user is not authorized!")
        await client.disconnect()
        sys.exit(1)
    
    # Export to StringSession
    session_string = client.session.save()
    
    await client.disconnect()
    
    print("\n" + "="*60)
    print("SUCCESS! Copy this line to your .env file:")
    print("="*60)
    print(f"\nTELETHON_SESSION_STRING={session_string}\n")
    print("="*60)
    print("\nThen restart the bot.")
    print("You can delete user_session.session after confirming it works.")


if __name__ == "__main__":
    asyncio.run(export_session())
