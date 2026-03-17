"""Inline keyboards for post transfer flow."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def detect_channel_keyboard() -> InlineKeyboardMarkup:
    """
    Create keyboard for channel ID detection options.

    Returns:
        Inline keyboard with auto-detect, manual entry, and back buttons
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="🔍 Определить ID автоматически", callback_data="transfer_detect_auto")
    builder.button(text="✏️ Ввести chat_id вручную", callback_data="transfer_enter_chat_id")
    builder.button(text="↩️ Назад", callback_data="nav_goto_menu")
    builder.adjust(1)
    return builder.as_markup()


def confirm_channel_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """
    Create keyboard for confirming detected channel.

    Args:
        chat_id: The detected channel chat_id

    Returns:
        Inline keyboard with yes/no buttons
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да", callback_data=f"transfer_confirm_channel:{chat_id}")
    builder.button(text="❌ Нет", callback_data="transfer_reject_channel")
    builder.adjust(2)
    return builder.as_markup()


def retry_detect_keyboard() -> InlineKeyboardMarkup:
    """
    Create keyboard for retrying channel detection after failure.

    Returns:
        Inline keyboard with retry and manual entry buttons
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Попробовать снова", callback_data="transfer_detect_auto")
    builder.button(text="✏️ Ввести вручную", callback_data="transfer_enter_chat_id")
    builder.adjust(1)
    return builder.as_markup()


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
