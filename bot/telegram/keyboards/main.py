"""Main inline keyboards for bot navigation."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def start_keyboard() -> InlineKeyboardMarkup:
    """
    Create start screen keyboard with main actions.

    Returns:
        Inline keyboard with 3 buttons
    """
    builder = InlineKeyboardBuilder()

    builder.button(text="✅ Проверить подписку", callback_data="start_check_sub")
    builder.button(text="📥 Настроить перенос", callback_data="start_setup_transfer")
    builder.button(text="🔄 Настроить автопостинг", callback_data="start_setup_autopost")

    builder.adjust(1)
    return builder.as_markup()


def menu_keyboard() -> InlineKeyboardMarkup:
    """
    Create main menu keyboard.

    Returns:
        Inline keyboard with 5 buttons
    """
    builder = InlineKeyboardBuilder()

    builder.button(text="📢 Мои каналы", callback_data="menu_channels")
    builder.button(text="💎 Проверить баланс", callback_data="menu_balance")
    builder.button(text="🎁 Бонусные посты", callback_data="menu_bonus")
    builder.button(text="🎟 Активировать промокод", callback_data="menu_promo")
    builder.button(text="ℹ️ Помощь", callback_data="menu_help")

    builder.adjust(1)
    return builder.as_markup()


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    """
    Create keyboard with back to menu button.

    Returns:
        Inline keyboard with single back button
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="↩️ В меню", callback_data="nav_goto_menu")
    return builder.as_markup()
