#!/usr/bin/env python3
"""
Telethon user session authorization script.

Run this ONCE to authorize a Telegram user account and create session file.
After authorization, the bot can use the saved session for reading channel history.

Usage:
    python scripts/auth_telethon.py

The script will:
1. Ask for phone number (or use from .env)
2. Send a code to Telegram
3. Ask for the code received in Telegram
4. (Optional) Ask for 2FA password if enabled
5. Save session to user_session.session file
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from telethon import TelegramClient, errors
from loguru import logger


# Session file path
SESSION_FILE = "user_session"


async def authorize():
    """Authorize Telethon user session interactively."""

    # Load settings from environment
    from dotenv import load_dotenv
    load_dotenv(project_root / ".env")

    api_id = int(__import__("os").getenv("TELEGRAM_API_ID"))
    api_hash = __import__("os").getenv("TELEGRAM_API_HASH")
    phone = __import__("os").getenv("TELEGRAM_PHONE")

    if not all([api_id, api_hash]):
        logger.error(
            "TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env file.\n"
            "Get them from https://my.telegram.org"
        )
        sys.exit(1)

    print(f"📱 Telethon Authorization")
    print(f"=" * 40)
    print(f"API ID: {api_id}")
    print(f"API Hash: {api_hash[:10]}...")
    print(f"Session file: {SESSION_FILE}.session")
    print(f"=" * 40)
    print()

    # Check if session already exists
    session_file = Path(SESSION_FILE + ".session")
    if session_file.exists():
        overwrite = input(f"⚠️  Session file already exists. Overwrite? (y/N): ").strip().lower()
        if overwrite != 'y':
            print("❌ Authorization cancelled.")
            sys.exit(0)
        session_file.unlink()
        print(f"✅ Deleted existing session file.")
        print()

    # Ask for phone if not in .env
    if not phone:
        phone = input("📞 Enter your phone number (with +, e.g. +79991234567): ").strip()
        if not phone:
            logger.error("Phone number is required.")
            sys.exit(1)

    print(f"📱 Phone: {phone}")
    print()

    # Create client
    client = TelegramClient(SESSION_FILE, api_id, api_hash)

    try:
        # Connect
        await client.connect()

        # Check if already authorized (shouldn't happen since we deleted session)
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"✅ Already authorized as: {me.first_name} (@{me.username})")
            await client.disconnect()
            return

        # Send code request
        print("📨 Sending code request to Telegram...")
        await client.send_code_request(phone)
        print("✅ Code sent!")

        # Ask for code
        print()
        code = input("🔑 Enter the code you received in Telegram: ").strip()

        try:
            # Sign in with code
            await client.sign_in(phone, code)

        except errors.SessionPasswordNeededError:
            # 2FA enabled
            print()
            print("🔐 Two-factor authentication (2FA) is enabled.")
            password = input("🔑 Enter your 2FA password: ").strip()
            await client.sign_in(password=password)

        # Get user info
        me = await client.get_me()

        print()
        print("=" * 40)
        print(f"✅ Authorization successful!")
        print(f"   Name: {me.first_name} {me.last_name or ''}")
        print(f"   Username: @{me.username}")
        print(f"   Phone: {me.phone}")
        print(f"   Session saved to: {SESSION_FILE}.session")
        print("=" * 40)
        print()
        print("🚀 You can now run the bot - it will use this session automatically.")

    except Exception as e:
        logger.error(f"Authorization failed: {e}")
        print(f"❌ Error: {e}")
        print("💡 Try deleting the session file and run again.")

    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(authorize())
    except KeyboardInterrupt:
        print("\n❌ Authorization cancelled.")
        sys.exit(0)
