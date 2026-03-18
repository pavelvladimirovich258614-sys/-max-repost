"""Main inline keyboards for bot navigation."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def start_keyboard() -> InlineKeyboardMarkup:
    """
    Create start screen keyboard with main actions.

    For first-time users - 5 main entry points.

    Returns:
        Inline keyboard with 5 buttons
    """
    builder = InlineKeyboardBuilder()

    builder.button(text="📥 Перенос постов", callback_data="start_setup_transfer")
    builder.button(text="⚡ Автопостинг", callback_data="start_setup_autopost")
    builder.button(text="💰 Баланс", callback_data="menu_balance")
    builder.button(text="📢 Мои каналы", callback_data="menu_my_channels")
    builder.button(text="🏠 Главное меню", callback_data="nav_goto_menu")

    builder.adjust(1)
    return builder.as_markup()


def menu_keyboard() -> InlineKeyboardMarkup:
    """
    Create main menu keyboard.

    Full menu for returning users with all options.

    Returns:
        Inline keyboard with 5 buttons
    """
    builder = InlineKeyboardBuilder()

    builder.button(text="📥 Перенос постов", callback_data="menu_new_transfer")
    builder.button(text="⚡ Автопостинг", callback_data="menu_manage_autopost")
    builder.button(text="💰 Баланс", callback_data="menu_balance")
    builder.button(text="📢 Мои каналы", callback_data="menu_my_channels")
    builder.button(text="❓ Инструкция", callback_data="menu_help")

    builder.adjust(1)
    return builder.as_markup()


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    """
    Create keyboard with back to menu button.

    Returns:
        Inline keyboard with single back button
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
    return builder.as_markup()


def balance_keyboard() -> InlineKeyboardMarkup:
    """
    Create keyboard for balance screen.

    Returns:
        Inline keyboard with balance action buttons
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 Пополнить", callback_data="balance_deposit")
    builder.button(text="📋 История", callback_data="balance_history")
    builder.button(text="🏠 Меню", callback_data="nav_goto_menu")
    builder.adjust(2, 1)
    return builder.as_markup()
