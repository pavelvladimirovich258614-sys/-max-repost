"""Telegram bot handlers."""

from bot.telegram.handlers.start import start_router
from bot.telegram.handlers.admin import admin_router

__all__ = ["start_router", "admin_router"]
