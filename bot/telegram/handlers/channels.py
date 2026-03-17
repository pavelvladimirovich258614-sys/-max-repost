"""Channel management handler - list, view, toggle autopost, delete channels."""

from aiogram import Router
from aiogram.types import CallbackQuery, Message
from aiogram.filters import StateFilter
from loguru import logger

from bot.telegram.states import ChannelStates
from bot.telegram.keyboards.channels import (
    channels_list_keyboard,
    no_channels_keyboard,
    channel_settings_keyboard,
    delete_confirm_keyboard,
)


# Create router
channels_router = Router(name="channels")


# =============================================================================
# My Channels List
# =============================================================================


@channels_router.callback_query(lambda c: c.data == "menu_channels")
async def show_channels_list(callback: CallbackQuery, user_repo, channel_repo) -> None:
    """
    Show list of user's connected channels.

    Args:
        callback: Callback query
        user_repo: User repository
        channel_repo: Channel repository
    """
    # Answer callback FIRST with loading indicator before any async operations
    await callback.answer("⏳")
    
    user = await user_repo.get_by_telegram_id(callback.from_user.id)

    if user is None:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
        await callback.message.edit_text(
            "❌ Пользователь не найден.",
            reply_markup=builder.as_markup(),
        )
        return

    # Get user's channels
    channels = await channel_repo.get_by_user(user.id)

    if not channels:
        await callback.message.edit_text(
            "<b>📢 Мои каналы</b>\n\n"
            "У вас пока нет подключённых каналов.\n\n"
            "Создайте первый канал для переноса постов!",
            parse_mode="HTML",
            reply_markup=no_channels_keyboard(),
        )
        return

    # Build channel data for keyboard
    channels_data = [
        {"id": ch.id, "name": ch.telegram_channel_name}
        for ch in channels
    ]

    text = (
        f"<b>📢 Мои каналы</b>\n\n"
        f"Подключено каналов: <b>{len(channels)}</b>\n\n"
        "Выберите канал для управления:"
    )

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=channels_list_keyboard(channels_data),
    )


# =============================================================================
# Channel Details
# =============================================================================


@channels_router.callback_query(lambda c: c.data and c.data.startswith("channel_"))
async def show_channel_details(callback: CallbackQuery, user_repo, channel_repo) -> None:
    """
    Show channel details and settings.

    Args:
        callback: Callback query with channel_{id} data
        user_repo: User repository
        channel_repo: Channel repository
    """
    # Answer callback FIRST with loading indicator before any async operations
    await callback.answer("⏳")
    
    # Extract channel ID
    try:
        channel_id = int(callback.data.split("_")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверный формат данных", show_alert=True)
        return

    user = await user_repo.get_by_telegram_id(callback.from_user.id)
    if user is None:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    # Get channel and verify ownership
    channel = await channel_repo.get(channel_id)
    if channel is None or channel.user_id != user.id:
        await callback.answer("❌ Канал не найден", show_alert=True)
        return

    # Build channel info text
    auto_status = "🟢 Активен" if channel.auto_repost else "🔴 Выключен"

    # Get settings for display
    settings = channel.settings or {}
    skip_forwards = settings.get("skip_forwards", False)
    filter_words = settings.get("filter_words", [])
    force_send = settings.get("force_send", False)

    filter_text = "Нет"
    if skip_forwards:
        filter_text = "Пропускать пересылки"

    if filter_words:
        if isinstance(filter_words, list):
            filter_text = f"Слова: {', '.join(filter_words[:3])}"
        else:
            filter_text = f"Слова: {filter_words}"

    force_text = "Вкл" if force_send else "Выкл"

    text = (
        f"<b>⚙️ Параметры канала</b>\n\n"
        f"📌 Название: {channel.telegram_channel_name}\n"
        f"ID: {channel.telegram_channel_id}\n"
        f"🔄 Автопостинг: {auto_status}\n\n"
        f"🔧 Фильтры:\n"
        f"• Пропускать пересылки: {'Да' if skip_forwards else 'Нет'}\n"
        f"• Фильтр по словам: {filter_text if filter_words else 'Нет'}\n"
        f"• Принудительная отправка: {force_text}"
    )

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=channel_settings_keyboard(channel.id, channel.auto_repost),
    )


# =============================================================================
# Toggle Autopost
# =============================================================================


@channels_router.callback_query(lambda c: c.data and c.data.startswith("channel_toggle_"))
async def toggle_autopost(callback: CallbackQuery, user_repo, channel_repo) -> None:
    """
    Toggle auto-repost for a channel.

    Args:
        callback: Callback query with channel_toggle_{id}
        user_repo: User repository
        channel_repo: Channel repository
    """
    # Answer callback FIRST with loading indicator before any async operations
    await callback.answer("⏳")
    
    try:
        channel_id = int(callback.data.split("_")[2])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверный формат данных", show_alert=True)
        return

    user = await user_repo.get_by_telegram_id(callback.from_user.id)
    if user is None:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    # Get channel and verify ownership
    channel = await channel_repo.get(channel_id)
    if channel is None or channel.user_id != user.id:
        await callback.answer("❌ Канал не найден", show_alert=True)
        return

    # Toggle autopost
    new_state = not channel.auto_repost
    updated = await channel_repo.toggle_autopost(channel_id, new_state)

    if updated is None:
        await callback.answer("❌ Ошибка обновления", show_alert=True)
        return

    status_text = "включен" if new_state else "выключен"
    logger.info(f"Autopost {status_text} for channel {channel_id} by user {user.id}")

    # Refresh the view
    auto_status = "🟢 Активен" if new_state else "🔴 Выключен"

    settings = channel.settings or {}
    skip_forwards = settings.get("skip_forwards", False)
    filter_words = settings.get("filter_words", [])
    force_send = settings.get("force_send", False)

    filter_text = "Нет"
    if skip_forwards:
        filter_text = "Пропускать пересылки"
    if filter_words:
        if isinstance(filter_words, list):
            filter_text = f"Слова: {', '.join(filter_words[:3])}"
        else:
            filter_text = f"Слова: {filter_words}"
    force_text = "Вкл" if force_send else "Выкл"

    text = (
        f"<b>⚙️ Параметры канала</b>\n\n"
        f"📌 Название: {channel.telegram_channel_name}\n"
        f"ID: {channel.telegram_channel_id}\n"
        f"🔄 Автопостинг: {auto_status}\n\n"
        f"🔧 Фильтры:\n"
        f"• Пропускать пересылки: {'Да' if skip_forwards else 'Нет'}\n"
        f"• Фильтр по словам: {filter_text if filter_words else 'Нет'}\n"
        f"• Принудительная отправка: {force_text}"
    )

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=channel_settings_keyboard(channel.id, new_state),
    )


# =============================================================================
# Placeholder Actions
# =============================================================================


@channels_router.callback_query(lambda c: c.data and c.data.startswith("channel_transfer_"))
async def channel_transfer(callback: CallbackQuery) -> None:
    """Placeholder: Start transfer from existing channel."""
    await callback.answer("🚧 В разработке", show_alert=True)


@channels_router.callback_query(lambda c: c.data and c.data.startswith("channel_filters_"))
async def channel_filters(callback: CallbackQuery) -> None:
    """Placeholder: Configure channel filters."""
    await callback.answer("🚧 В разработке", show_alert=True)


@channels_router.callback_query(lambda c: c.data and c.data.startswith("channel_check_tg_"))
async def channel_check_tg(callback: CallbackQuery) -> None:
    """Placeholder: Check Telegram channel connection."""
    await callback.answer("🚧 В разработке", show_alert=True)


@channels_router.callback_query(lambda c: c.data and c.data.startswith("channel_check_max_"))
async def channel_check_max(callback: CallbackQuery) -> None:
    """Placeholder: Check Max channel connection."""
    await callback.answer("🚧 В разработке", show_alert=True)


# =============================================================================
# Delete Channel
# =============================================================================


@channels_router.callback_query(lambda c: c.data and c.data.startswith("channel_delete_"))
async def request_delete_channel(callback: CallbackQuery) -> None:
    """
    Show confirmation dialog for channel deletion.

    Args:
        callback: Callback query with channel_delete_{id}
    """
    # Answer callback FIRST before any async operations
    await callback.answer()
    
    try:
        channel_id = int(callback.data.split("_")[2])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверный формат данных", show_alert=True)
        return

    await callback.message.edit_text(
        "<b>⚠️ Удалить канал?</b>\n\n"
        "Это действие нельзя отменить. Все настройки будут потеряны.",
        parse_mode="HTML",
        reply_markup=delete_confirm_keyboard(channel_id),
    )


@channels_router.callback_query(lambda c: c.data and c.data.startswith("channel_delete_confirm_"))
async def confirm_delete_channel(callback: CallbackQuery, user_repo, channel_repo) -> None:
    """
    Confirm and execute channel deletion.

    Args:
        callback: Callback query with channel_delete_confirm_{id}
        user_repo: User repository
        channel_repo: Channel repository
    """
    # Answer callback FIRST with loading indicator before any async operations
    await callback.answer("⏳")
    
    try:
        channel_id = int(callback.data.split("_")[3])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверный формат данных", show_alert=True)
        return

    user = await user_repo.get_by_telegram_id(callback.from_user.id)
    if user is None:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    # Verify ownership and delete
    deleted = await channel_repo.delete_by_user(user.id, channel_id)

    if deleted:
        logger.info(f"Channel {channel_id} deleted by user {user.id}")
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
        await callback.message.edit_text(
            "✅ Канал удалён.",
            reply_markup=builder.as_markup(),
        )

        # Show updated list after a brief delay
        # In real implementation, you'd call show_channels_list directly
        from bot.telegram.keyboards.channels import no_channels_keyboard

        await callback.message.edit_text(
            "<b>📢 Мои каналы</b>\n\n"
            "У вас пока нет подключённых каналов.\n\n"
            "Создайте первый канал для переноса постов!",
            parse_mode="HTML",
            reply_markup=no_channels_keyboard(),
        )
    else:
        await callback.answer("❌ Не удалось удалить канал", show_alert=True)


@channels_router.callback_query(lambda c: c.data and c.data.startswith("channel_cancel_delete_"))
async def cancel_delete_channel(callback: CallbackQuery, user_repo, channel_repo) -> None:
    """
    Cancel channel deletion and return to channel details.

    Args:
        callback: Callback query
        user_repo: User repository
        channel_repo: Channel repository
    """
    # Answer callback FIRST before any async operations
    await callback.answer()
    
    # Get the channel ID from the callback data (need to extract it differently)
    # Since we cancelled, we need to go back to the channels list
    # This is a simplified version - in production you'd track which channel was being viewed

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
    
    await callback.message.edit_text(
        "<b>📢 Мои каналы</b>\n\n"
        "Выберите канал для управления:",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )
