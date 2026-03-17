"""Inline keyboards for post transfer flow."""

from datetime import datetime
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def saved_max_channels_keyboard(
    bindings: list,
    show_delete: bool = False,
) -> InlineKeyboardMarkup:
    """
    Create keyboard for selecting saved Max channels.

    Args:
        bindings: List of MaxChannelBinding objects
        show_delete: Whether to show delete buttons

    Returns:
        Inline keyboard with saved channels and add new button
    """
    builder = InlineKeyboardBuilder()
    
    for binding in bindings:
        # Format display name
        if binding.max_channel_name:
            display_name = binding.max_channel_name[:30]
        else:
            display_name = f"ID: {binding.max_chat_id}"
        
        # Format last used time
        if binding.last_used_at:
            if isinstance(binding.last_used_at, datetime):
                last_used = binding.last_used_at.strftime("%d.%m.%Y")
            else:
                last_used = str(binding.last_used_at)[:10]
        else:
            last_used = "неизвестно"
        
        # Main button to select this channel
        builder.button(
            text=f"📺 {display_name} ({last_used})",
            callback_data=f"transfer_select_saved_max:{binding.id}",
        )
        
        # Delete button if requested
        if show_delete:
            builder.button(
                text="🗑 Удалить",
                callback_data=f"transfer_delete_saved_max:{binding.id}",
            )
    
    # Add new channel button
    builder.button(text="➕ Добавить новый канал", callback_data="transfer_add_new_max")
    builder.button(text="↩️ Назад", callback_data="nav_goto_menu")
    
    # Adjust: 2 columns if showing delete buttons, 1 column otherwise
    if show_delete:
        builder.adjust(2, repeat=True)
    else:
        builder.adjust(1)
    
    return builder.as_markup()


def confirm_delete_binding_keyboard(binding_id: int) -> InlineKeyboardMarkup:
    """
    Create keyboard for confirming deletion of a binding.

    Args:
        binding_id: Binding ID to delete

    Returns:
        Inline keyboard with confirm/cancel buttons
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, удалить", callback_data=f"transfer_confirm_delete_binding:{binding_id}")
    builder.button(text="❌ Отмена", callback_data="transfer_cancel_delete_binding")
    builder.adjust(2)
    return builder.as_markup()


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


def select_count_keyboard(all_count: int) -> InlineKeyboardMarkup:
    """
    Create keyboard for selecting number of posts to transfer.

    Args:
        all_count: Total number of posts available

    Returns:
        Inline keyboard with post count options
    """
    builder = InlineKeyboardBuilder()
    
    # All posts button
    builder.button(text=f"📄 Все посты ({all_count})", callback_data="transfer_count:all")
    
    # Last 100 (show only if more than 100 posts)
    if all_count > 100:
        builder.button(text="📄 Последние 100", callback_data="transfer_count:100")
    
    # Last 50 (show only if more than 50 posts)
    if all_count > 50:
        builder.button(text="📄 Последние 50", callback_data="transfer_count:50")
    
    builder.button(text="✏️ Своё количество", callback_data="transfer_count:custom")
    builder.button(text="↩️ Назад", callback_data="transfer_count:back")

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


def verified_channels_keyboard(channels: list) -> InlineKeyboardMarkup:
    """
    Create keyboard for selecting from verified channels.

    Args:
        channels: List of VerifiedChannel objects

    Returns:
        Inline keyboard with channel buttons and add new button
    """
    builder = InlineKeyboardBuilder()
    
    for channel in channels:
        display_name = channel.tg_channel[:30] if len(channel.tg_channel) <= 30 else channel.tg_channel[:27] + "..."
        builder.button(
            text=f"📢 @{display_name}",
            callback_data=f"select_verified_channel:{channel.tg_channel}",
        )
    
    # Add new channel button
    builder.button(text="➕ Добавить новый канал", callback_data="start_setup_transfer")
    builder.button(text="↩️ Назад", callback_data="nav_goto_menu")
    
    builder.adjust(1)
    return builder.as_markup()


def verify_code_keyboard() -> InlineKeyboardMarkup:
    """
    Create keyboard for channel ownership verification.

    Returns:
        Inline keyboard with check, new code, and back buttons
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Проверить", callback_data="verify_check")
    builder.button(text="🔄 Новый код", callback_data="verify_new_code")
    builder.button(text="↩️ Назад", callback_data="verify_back")
    builder.adjust(1)
    return builder.as_markup()
