"""Inline keyboards for auto-posting setup flow."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def check_admin_keyboard() -> InlineKeyboardMarkup:
    """
    Create keyboard with 'Check' and 'Back' buttons.

    Used when user needs to add bot as admin and verify.

    Returns:
        Inline keyboard with buttons
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Проверить", callback_data="autopost_check_admin")
    builder.button(text="↩️ Назад", callback_data="nav_goto_menu")
    builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
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


def autopost_complete_keyboard() -> InlineKeyboardMarkup:
    """
    Create keyboard for successful autopost setup completion.

    Returns:
        Inline keyboard with 'Done' button
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
    return builder.as_markup()


def autopost_list_keyboard(has_subscriptions: bool = True) -> InlineKeyboardMarkup:
    """
    Create keyboard for autopost list screen.

    Args:
        has_subscriptions: Whether user has any subscriptions

    Returns:
        Inline keyboard with action buttons
    """
    builder = InlineKeyboardBuilder()
    
    builder.button(text="➕ Новый автопостинг", callback_data="autopost_new")
    builder.button(text="📢 Мои каналы", callback_data="menu_my_channels")
    builder.button(text="💰 Пополнить баланс", callback_data="menu_topup_balance")
    builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
    
    builder.adjust(1)
    return builder.as_markup()


def autopost_manage_keyboard(subscription_id: int, is_active: bool) -> InlineKeyboardMarkup:
    """
    Create keyboard for managing a specific autopost subscription.

    Args:
        subscription_id: The subscription ID
        is_active: Whether the subscription is currently active

    Returns:
        Inline keyboard with management buttons
    """
    builder = InlineKeyboardBuilder()
    
    # Toggle status button
    if is_active:
        builder.button(
            text="⏸ Приостановить",
            callback_data=f"autopost_toggle:{subscription_id}"
        )
    else:
        builder.button(
            text="▶️ Возобновить",
            callback_data=f"autopost_toggle:{subscription_id}"
        )
    
    builder.button(
        text="💰 Изменить стоимость",
        callback_data=f"autopost_price:{subscription_id}"
    )
    builder.button(
        text="🗑 Удалить автопостинг",
        callback_data=f"autopost_delete:{subscription_id}"
    )
    builder.button(text="↩️ Назад", callback_data="menu_manage_autopost")
    
    builder.adjust(1)
    return builder.as_markup()


def autopost_confirm_delete_keyboard(subscription_id: int) -> InlineKeyboardMarkup:
    """
    Create keyboard for confirming autopost deletion.

    Args:
        subscription_id: The subscription ID to delete

    Returns:
        Inline keyboard with confirm/cancel buttons
    """
    builder = InlineKeyboardBuilder()
    
    builder.button(
        text="🗑 Да, удалить",
        callback_data=f"autopost_delete_confirm:{subscription_id}"
    )
    builder.button(
        text="↩️ Отмена",
        callback_data=f"autopost_manage:{subscription_id}"
    )
    
    builder.adjust(1)
    return builder.as_markup()


def autopost_channel_select_keyboard(
    channels: list[dict],
    action_prefix: str = "autopost_select_ch"
) -> InlineKeyboardMarkup:
    """
    Create keyboard for selecting a channel from list.

    Args:
        channels: List of channel dicts with 'username' and 'title' keys
        action_prefix: Prefix for callback data

    Returns:
        Inline keyboard with channel buttons
    """
    builder = InlineKeyboardBuilder()
    
    for channel in channels:
        username = channel.get("username", "")
        title = channel.get("title", username)
        display = f"📢 @{username}" if username else f"📢 {title[:25]}"
        builder.button(
            text=display,
            callback_data=f"{action_prefix}:{username}"
        )
    
    builder.button(text="↩️ Назад", callback_data="menu_manage_autopost")
    builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
    
    builder.adjust(1)
    return builder.as_markup()


def autopost_max_select_keyboard(
    bindings: list[dict],
    tg_channel: str
) -> InlineKeyboardMarkup:
    """
    Create keyboard for selecting a Max channel from saved bindings.

    Args:
        bindings: List of MaxChannelBinding dicts with 'max_chat_id' and 'max_channel_name' keys
        tg_channel: The selected TG channel username

    Returns:
        Inline keyboard with Max channel buttons
    """
    builder = InlineKeyboardBuilder()
    
    for binding in bindings:
        chat_id = binding.get("max_chat_id", "")
        name = binding.get("max_channel_name") or f"Канал {chat_id}"
        display = f"➡ {name[:30]}"
        builder.button(
            text=display,
            callback_data=f"autopost_select_max:{tg_channel}:{chat_id}"
        )
    
    builder.button(
        text="➕ Новый Max канал",
        callback_data=f"autopost_new_max:{tg_channel}"
    )
    builder.button(text="↩️ Назад", callback_data="autopost_new")
    builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
    
    builder.adjust(1)
    return builder.as_markup()


def autopost_confirm_creation_keyboard(tg_channel: str, max_chat_id: str) -> InlineKeyboardMarkup:
    """
    Create keyboard for confirming autopost creation.

    Args:
        tg_channel: TG channel username
        max_chat_id: Max channel chat_id

    Returns:
        Inline keyboard with confirm/cancel buttons
    """
    builder = InlineKeyboardBuilder()
    
    builder.button(
        text="✅ Подтвердить",
        callback_data=f"autopost_create_confirm:{tg_channel}:{max_chat_id}"
    )
    builder.button(
        text="↩️ Назад",
        callback_data=f"autopost_select_max:{tg_channel}:back"
    )
    builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
    
    builder.adjust(1)
    return builder.as_markup()
