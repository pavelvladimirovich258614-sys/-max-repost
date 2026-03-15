"""Inline keyboards for channel management flow."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def channels_list_keyboard(channels: list[dict]) -> InlineKeyboardMarkup:
    """
    Create keyboard with list of user's channels.

    Args:
        channels: List of channel dicts with id and name

    Returns:
        Inline keyboard with channel buttons and actions
    """
    builder = InlineKeyboardBuilder()

    # Add channel buttons
    for channel in channels:
        channel_id = channel.get("id")
        channel_name = channel.get("name", "Канал")
        # Truncate long names
        display_name = channel_name[:30] + "..." if len(channel_name) > 30 else channel_name
        builder.button(text=f"📢 {display_name}", callback_data=f"channel_{channel_id}")

    # Add action buttons
    builder.button(text="➕ Новый перенос", callback_data="menu_new_transfer")
    builder.button(text="🔄 Новый автопостинг", callback_data="menu_new_autopost")
    builder.button(text="↩️ В меню", callback_data="nav_goto_menu")

    # Adjust: channels in rows of 1, actions in rows of 1
    if channels:
        builder.adjust(*([1] * len(channels) + [1, 1, 1]))
    else:
        builder.adjust(1, 1, 1)

    return builder.as_markup()


def no_channels_keyboard() -> InlineKeyboardMarkup:
    """
    Create keyboard for when user has no channels.

    Returns:
        Inline keyboard with add channel buttons
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Новый перенос", callback_data="menu_new_transfer")
    builder.button(text="🔄 Новый автопостинг", callback_data="menu_new_autopost")
    builder.button(text="↩️ В меню", callback_data="nav_goto_menu")
    builder.adjust(1)
    return builder.as_markup()


def channel_settings_keyboard(channel_id: int, auto_repost: bool) -> InlineKeyboardMarkup:
    """
    Create keyboard for channel settings/actions.

    Args:
        channel_id: Channel database ID
        auto_repost: Whether auto-repost is enabled

    Returns:
        Inline keyboard with channel action buttons
    """
    builder = InlineKeyboardBuilder()

    # Dynamic button based on auto-repost status
    if auto_repost:
        builder.button(text="⏸ Остановить автопостинг", callback_data=f"channel_toggle_{channel_id}")
    else:
        builder.button(text="▶️ Запустить автопостинг", callback_data=f"channel_toggle_{channel_id}")

    builder.button(text="📥 Начать перенос", callback_data=f"channel_transfer_{channel_id}")
    builder.button(text="🔧 Настроить фильтры", callback_data=f"channel_filters_{channel_id}")
    builder.button(text="🔍 Проверить TG", callback_data=f"channel_check_tg_{channel_id}")
    builder.button(text="🔍 Проверить MAX", callback_data=f"channel_check_max_{channel_id}")
    builder.button(text="🗑 Удалить канал", callback_data=f"channel_delete_{channel_id}")
    builder.button(text="↩️ К списку каналов", callback_data="menu_channels")

    builder.adjust(1)
    return builder.as_markup()


def delete_confirm_keyboard(channel_id: int) -> InlineKeyboardMarkup:
    """
    Create keyboard for confirming channel deletion.

    Args:
        channel_id: Channel database ID

    Returns:
        Inline keyboard with confirm/cancel buttons
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, удалить", callback_data=f"channel_delete_confirm_{channel_id}")
    builder.button(text="❌ Отмена", callback_data=f"channel_cancel_delete_{channel_id}")
    builder.adjust(2)
    return builder.as_markup()
