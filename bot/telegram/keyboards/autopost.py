"""Inline keyboards for auto-posting setup flow."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def check_admin_keyboard() -> InlineKeyboardMarkup:
    """
    Create keyboard with 'Check' and 'Back' buttons.

    Used when user needs to add bot as admin and verify.

    Returns:
        Inline keyboard with 2 buttons in one row
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Проверить", callback_data="autopost_check_admin")
    builder.button(text="↩️ Назад", callback_data="nav_goto_menu")
    builder.adjust(2)
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


def autopost_complete_keyboard() -> InlineKeyboardMarkup:
    """
    Create keyboard for successful autopost setup completion.

    Returns:
        Inline keyboard with 'Done' button
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="⏭ Готово → меню", callback_data="nav_goto_menu")
    return builder.as_markup()
