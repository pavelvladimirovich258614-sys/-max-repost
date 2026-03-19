"""Channel ownership verification utilities."""

import secrets
import string
from loguru import logger


def generate_verification_code() -> str:
    """
    Generate a random verification code.
    
    Format: max-xxxx where xxxx is 4 random characters (lowercase + digits)
    
    Returns:
        Verification code string
    """
    chars = string.ascii_lowercase + string.digits
    random_part = ''.join(secrets.choice(chars) for _ in range(4))
    return f"max-{random_part}"


async def verify_channel_ownership(telethon_client, channel: str, code: str, bot=None, channel_id: str = None) -> bool:
    """
    Verify channel ownership by checking if code is in channel description.
    
    Args:
        telethon_client: Telethon client instance with get_channel_description method
        channel: Channel username (with or without @) or channel ID
        code: Verification code to look for
        bot: Optional aiogram Bot instance for fallback via Bot API
        channel_id: Optional numeric channel ID for Bot API fallback
        
    Returns:
        True if code is found in channel description, False otherwise
    """
    # Try Telethon first
    try:
        description = await telethon_client.get_channel_description(channel)
        is_found = code in description
        logger.info(f"Verification check for {channel}: code_found={is_found}")
        return is_found
    except Exception as e:
        logger.warning(f"Telethon verification failed for {channel}: {e}")
    
    # Fallback to Bot API if bot is provided and channel_id is available
    if bot and channel_id:
        try:
            logger.info(f"Trying Bot API fallback for verification: {channel}")
            chat = await bot.get_chat(channel_id)
            description = chat.description or ""
            is_found = code in description
            logger.info(f"Bot API verification check for {channel}: code_found={is_found}")
            return is_found
        except Exception as e:
            logger.error(f"Bot API fallback also failed for {channel}: {e}")
    
    return False
