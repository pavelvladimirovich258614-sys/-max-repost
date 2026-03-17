"""Main inline keyboards for bot navigation."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def start_keyboard() -> InlineKeyboardMarkup:
    """
    Create start screen keyboard with main actions.

    For first-time users - 3 main entry points.

    Returns:
        Inline keyboard with 3 buttons
    """
    builder = InlineKeyboardBuilder()

    builder.button(text="📥 Настроить перенос", callback_data="start_setup_transfer")
    builder.button(text="🔄 Настроить автопостинг", callback_data="start_setup_autopost")
    builder.button(text="✅ Проверить подписку", callback_data="start_check_sub")

    builder.adjust(1)
    return builder.as_markup()


def menu_keyboard() -> InlineKeyboardMarkup:
    """
    Create main menu keyboard.

    Full menu for returning users with all options.

    Returns:
        Inline keyboard with 7 buttons
    """
    builder = InlineKeyboardBuilder()

    builder.button(text="📥 Настроить перенос", callback_data="menu_new_transfer")
    builder.button(text="🔄 Настроить автопостинг", callback_data="menu_new_autopost")
    builder.button(text="📢 Мои каналы (перенос)", callback_data="menu_my_channels")
    builder.button(text="⚡ Автопостинг", callback_data="menu_manage_autopost")
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
