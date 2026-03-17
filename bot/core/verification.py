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


async def verify_channel_ownership(telethon_client, channel: str, code: str) -> bool:
    """
    Verify channel ownership by checking if code is in channel description.
    
    Args:
        telethon_client: Telethon client instance with get_channel_description method
        channel: Channel username (with or without @) or channel ID
        code: Verification code to look for
        
    Returns:
        True if code is found in channel description, False otherwise
    """
    try:
        description = await telethon_client.get_channel_description(channel)
        is_found = code in description
        logger.info(f"Verification check for {channel}: code_found={is_found}")
        return is_found
    except Exception as e:
        logger.error(f"Failed to verify channel ownership for {channel}: {e}")
        return False
