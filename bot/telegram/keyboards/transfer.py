"""Inline keyboards for post transfer flow."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def back_keyboard() -> InlineKeyboardMarkup:
    """
    Create keyboard with back button.

    Returns:
        Inline keyboard with single back button
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="↩️ Назад", callback_data="nav_goto_menu")
    return builder.as_markup()


def back_to_start_keyboard() -> InlineKeyboardMarkup:
    """
    Create keyboard with back to menu button.

    Returns:
        Inline keyboard with single button
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="↩️ В меню", callback_data="nav_goto_menu")
    return builder.as_markup()


def select_count_keyboard(all_count: int, has_all: bool = True) -> InlineKeyboardMarkup:
    """
    Create keyboard for selecting number of posts to transfer.

    Args:
        all_count: Total number of posts available
        has_all: Whether to show "All" button (for large counts)

    Returns:
        Inline keyboard with post count options
    """
    builder = InlineKeyboardBuilder()

    if has_all and all_count > 100:
        builder.button(text=f"📄 Все ({all_count} шт.)", callback_data="transfer_count_all")
    builder.button(text="📄 Последние 100", callback_data="transfer_count_100")
    builder.button(text="📄 Последние 50", callback_data="transfer_count_50")
    builder.button(text="✏️ Своё количество", callback_data="transfer_count_custom")
    builder.button(text="↩️ Назад", callback_data="transfer_cancel")

    builder.adjust(1)
    return builder.as_markup()


def transfer_complete_keyboard() -> InlineKeyboardMarkup:
    """
    Create keyboard for transfer completion.

    Returns:
        Inline keyboard with 'Done' button
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="⏭ Готово → меню", callback_data="nav_goto_menu")
    return builder.as_markup()
