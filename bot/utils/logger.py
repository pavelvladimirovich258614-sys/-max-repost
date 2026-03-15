"""Logger configuration using Loguru."""

from loguru import logger
import sys
from pathlib import Path


def init_logger(log_level: str = "INFO") -> None:
    """
    Initialize Loguru logger with console and file handlers.

    Args:
        log_level: Logging level (default: INFO)
    """
    # Remove default handler
    logger.remove()

    # Console handler
    logger.add(
        sys.stderr,
        level=log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        colorize=True,
    )

    # File handler with rotation
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    logger.add(
        logs_dir / "bot_{time}.log",
        level=log_level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        rotation="1 day",
        retention="7 days",
        compression="zip",
        enqueue=True,
    )
