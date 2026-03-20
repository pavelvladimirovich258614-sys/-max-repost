"""Auto-posting handler - managing TG -> Max autopost subscriptions."""

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from loguru import logger

from bot.telegram.keyboards.main import back_to_menu_keyboard
from bot.database.models import MaxChannelBinding
from bot.core.autopost import get_autopost_manager
from bot.database.repositories.verified_channel import VerifiedChannelRepository
from bot.database.connection import get_session

# Constants
COST_PER_POST = 3  # 3 rubles per post

# Create router
autopost_router = Router(name="autopost")


# =============================================================================
# Helper Functions
# =============================================================================

async def _render_autopost_list(
    message: Message,
    user_id: int,
    autopost_sub_repo,
    balance_repo,
) -> None:
    """
    Render the autopost list directly to a message.
    
    This is a helper function used when we need to refresh the autopost list
    but cannot modify callback.data (it's frozen in aiogram 3).
    """
    try:
        # Get user's subscriptions and balance
        subscriptions = await autopost_sub_repo.get_user_subscriptions(user_id)
        
        if balance_repo:
            balance, _ = await balance_repo.get_or_create(user_id)
            balance_int = int(balance.balance)
        else:
            balance_int = 0
        
        if not subscriptions:
            # No subscriptions yet
            text = (
                "⚡ <b>Автопостинг</b>\n\n"
                "Автопостинг автоматически переносит <b>НОВЫЕ</b> посты "
                "из вашего Telegram-канала в Max в реальном времени.\n\n"
                f"Стоимость: {COST_PER_POST}₽ за каждый новый пост.\n\n"
                "У вас пока нет активных автопостингов."
            )
            
            builder = InlineKeyboardBuilder()
            builder.button(text="➕ Подключить автопостинг", callback_data="autopost_new")
            builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
            builder.adjust(1)
            
            await message.edit_text(text, reply_markup=builder.as_markup())
            return
        
        # Build subscription list text
        lines = ["⚡ <b>Автопостинг</b>\n"]
        
        for sub in subscriptions:
            status_emoji = "✅" if sub.is_active else "⏸"
            lines.append(
                f"{status_emoji} @{sub.tg_channel} → Max\n"
                f"   Статус: {'активен' if sub.is_active else 'приостановлен'} | "
                f"Перенесено: {sub.posts_transferred} постов"
            )
        
        lines.append(f"\n💰 <b>Баланс:</b> {balance_int}₽")
        
        text = "\n".join(lines)
        
        # Build keyboard with subscription control buttons
        builder = InlineKeyboardBuilder()
        
        for sub in subscriptions:
            if sub.is_active:
                builder.button(
                    text=f"⏸ Приостановить @{sub.tg_channel[:20]}",
                    callback_data=f"autopost_toggle:{sub.id}"
                )
            else:
                builder.button(
                    text=f"▶️ Возобновить @{sub.tg_channel[:20]}",
                    callback_data=f"autopost_toggle:{sub.id}"
                )
            builder.button(
                text=f"🗑 Отключить @{sub.tg_channel[:20]}",
                callback_data=f"autopost_delete:{sub.id}"
            )
        
        builder.button(text="➕ Подключить ещё", callback_data="autopost_new")
        builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
        builder.adjust(1)
        
        await message.edit_text(text, reply_markup=builder.as_markup())
        
    except Exception as e:
        logger.error(f"Error in _render_autopost_list: {e}")
        await message.edit_text(
            "❌ Произошла ошибка при загрузке автопостингов. Попробуйте позже.",
            reply_markup=back_to_menu_keyboard()
        )


# =============================================================================
# Main Screen: List of Autopostings
# =============================================================================


@autopost_router.callback_query(lambda c: c.data == "menu_manage_autopost")
async def show_autopost_list(
    callback: CallbackQuery,
    autopost_sub_repo,
    balance_repo,
) -> None:
    """Show list of user's autopost subscriptions."""
    await callback.answer()
    
    user_id = callback.from_user.id
    
    try:
        # Get user's subscriptions and balance
        subscriptions = await autopost_sub_repo.get_user_subscriptions(user_id)
        balance, _ = await balance_repo.get_or_create(user_id)
        balance_int = int(balance.balance)
        
        if not subscriptions:
            # No subscriptions yet
            text = (
                "⚡ <b>Автопостинг</b>\n\n"
                "Автопостинг автоматически переносит <b>НОВЫЕ</b> посты "
                "из вашего Telegram-канала в Max в реальном времени.\n\n"
                f"Стоимость: {COST_PER_POST}₽ за каждый новый пост.\n\n"
                "У вас пока нет активных автопостингов."
            )
            
            builder = InlineKeyboardBuilder()
            builder.button(text="➕ Подключить автопостинг", callback_data="autopost_new")
            builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
            builder.adjust(1)
            
            await callback.message.edit_text(text, reply_markup=builder.as_markup())
            return
        
        # Build subscription list text
        lines = ["⚡ <b>Автопостинг</b>\n"]
        
        for sub in subscriptions:
            status_emoji = "✅" if sub.is_active else "⏸"
            lines.append(
                f"{status_emoji} @{sub.tg_channel} → Max\n"
                f"   Статус: {'активен' if sub.is_active else 'приостановлен'} | "
                f"Перенесено: {sub.posts_transferred} постов"
            )
        
        lines.append(f"\n💰 <b>Баланс:</b> {balance_int}₽")
        
        text = "\n".join(lines)
        
        # Build keyboard with subscription control buttons
        builder = InlineKeyboardBuilder()
        
        for sub in subscriptions:
            if sub.is_active:
                builder.button(
                    text=f"⏸ Приостановить @{sub.tg_channel[:20]}",
                    callback_data=f"autopost_toggle:{sub.id}"
                )
            else:
                builder.button(
                    text=f"▶️ Возобновить @{sub.tg_channel[:20]}",
                    callback_data=f"autopost_toggle:{sub.id}"
                )
            builder.button(
                text=f"🗑 Отключить @{sub.tg_channel[:20]}",
                callback_data=f"autopost_delete:{sub.id}"
            )
        
        builder.button(text="➕ Подключить ещё", callback_data="autopost_new")
        builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
        builder.adjust(1)
        
        await callback.message.edit_text(text, reply_markup=builder.as_markup())
        
    except Exception as e:
        logger.error(f"Error in show_autopost_list: {e}")
        await callback.message.edit_text(
            "❌ Произошла ошибка при загрузке автопостингов. Попробуйте позже.",
            reply_markup=back_to_menu_keyboard()
        )


@autopost_router.callback_query(lambda c: c.data.startswith("autopost_toggle:"))
async def toggle_autopost_handler(
    callback: CallbackQuery,
    autopost_sub_repo,
) -> None:
    """Toggle autopost subscription active status (pause/resume)."""
    # Answer callback immediately to prevent timeout
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass

    user_id = callback.from_user.id
    subscription_id = int(callback.data.split(":", 1)[1])

    try:
        # Get subscription before toggle
        sub = await autopost_sub_repo.get_by_id_and_user(subscription_id, user_id)
        if not sub:
            await callback.answer("❌ Автопостинг не найден", show_alert=True)
            return
        
        manager = get_autopost_manager()
        
        if sub.is_active:
            # Currently active -> pause
            if manager:
                await manager.stop_monitoring(subscription_id)
            await autopost_sub_repo.pause_subscription(subscription_id, "manual_pause")
            await callback.answer("⏸ Автопостинг приостановлен", show_alert=True)
        else:
            # Currently paused -> resume
            await autopost_sub_repo.resume_subscription(subscription_id)
            if manager:
                # Reload subscription and start monitoring
                sub = await autopost_sub_repo.get_by_id_and_user(subscription_id, user_id)
                await manager.start_monitoring(sub)
            await callback.answer("▶️ Автопостинг возобновлён", show_alert=True)
        
        # Refresh the list
        await _render_autopost_list(callback.message, user_id, autopost_sub_repo, None)
        
    except Exception as e:
        logger.error(f"Error in toggle_autopost_handler: {e}")
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@autopost_router.callback_query(lambda c: c.data.startswith("autopost_delete:"))
async def delete_autopost_confirm(
    callback: CallbackQuery,
    autopost_sub_repo,
) -> None:
    """Show confirmation for deleting autopost subscription."""
    # Answer callback immediately to prevent timeout
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass

    user_id = callback.from_user.id
    subscription_id = int(callback.data.split(":", 1)[1])

    try:
        sub = await autopost_sub_repo.get_by_id_and_user(subscription_id, user_id)
        
        if not sub:
            await callback.answer("❌ Автопостинг не найден", show_alert=True)
            await _render_autopost_list(callback.message, user_id, autopost_sub_repo, None)
            return
        
        text = (
            f"🗑 <b>Отключить автопостинг?</b>\n\n"
            f"@{sub.tg_channel} → Max\n\n"
            f"Автопостинг будет полностью удалён. "
            f"Это действие нельзя отменить."
        )
        
        builder = InlineKeyboardBuilder()
        builder.button(
            text="🗑 Да, отключить",
            callback_data=f"autopost_delete_confirm:{subscription_id}"
        )
        builder.button(
            text="↩️ Отмена",
            callback_data="menu_manage_autopost"
        )
        builder.adjust(1)
        
        await callback.message.edit_text(text, reply_markup=builder.as_markup())
        
    except Exception as e:
        logger.error(f"Error in delete_autopost_confirm: {e}")
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@autopost_router.callback_query(lambda c: c.data.startswith("autopost_delete_confirm:"))
async def delete_autopost_handler(
    callback: CallbackQuery,
    autopost_sub_repo,
) -> None:
    """Delete autopost subscription."""
    # Answer callback immediately to prevent timeout
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass

    user_id = callback.from_user.id
    subscription_id = int(callback.data.split(":", 1)[1])

    try:
        # Stop monitoring first
        manager = get_autopost_manager()
        if manager:
            await manager.stop_monitoring(subscription_id)
        
        # Then delete from DB
        success = await autopost_sub_repo.delete_by_user(subscription_id, user_id)
        
        if success:
            await callback.answer("✅ Автопостинг отключён", show_alert=True)
        else:
            await callback.answer("❌ Не удалось отключить автопостинг", show_alert=True)
        
        # Return to list
        await _render_autopost_list(callback.message, user_id, autopost_sub_repo, None)
        
    except Exception as e:
        logger.error(f"Error in delete_autopost_handler: {e}")
        await callback.answer("❌ Произошла ошибка", show_alert=True)


# =============================================================================
# Create New Autoposting
# =============================================================================


@autopost_router.callback_query(lambda c: c.data == "autopost_new")
async def start_new_autopost(
    callback: CallbackQuery,
    state: FSMContext,
    verified_channel_repo,
    max_binding_repo,
    balance_repo,
) -> None:
    """Start creating new autopost - show channels that already have Max binding."""
    await callback.answer()
    await state.clear()
    
    user_id = callback.from_user.id
    
    try:
        # Get user's verified channels
        verified_channels = await verified_channel_repo.get_user_verified_channels(user_id)
        
        if not verified_channels:
            text = (
                "⚡ <b>Подключить автопостинг</b>\n\n"
                "У вас нет верифицированных каналов.\n\n"
                "Сначала необходимо верифицировать канал и привязать его к Max "
                "в разделе <b>📥 Настроить перенос</b>."
            )
            
            builder = InlineKeyboardBuilder()
            builder.button(text="📥 Настроить перенос", callback_data="menu_new_transfer")
            builder.button(text="↩️ Назад", callback_data="menu_manage_autopost")
            builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
            builder.adjust(1)
            
            await callback.message.edit_text(text, reply_markup=builder.as_markup())
            return
        
        # Get user's Max bindings
        bindings = await max_binding_repo.get_by_user(user_id)
        
        if not bindings:
            text = (
                "⚡ <b>Подключить автопостинг</b>\n\n"
                "У вас нет привязанных Max каналов.\n\n"
                "Сначала необходимо привязать канал к Max в разделе "
                "<b>📥 Настроить перенос</b>."
            )
            
            builder = InlineKeyboardBuilder()
            builder.button(text="📥 Настроить перенос", callback_data="menu_new_transfer")
            builder.button(text="↩️ Назад", callback_data="menu_manage_autopost")
            builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
            builder.adjust(1)
            
            await callback.message.edit_text(text, reply_markup=builder.as_markup())
            return
        
        # Create a set of channels that have Max bindings
        bound_channel_names = {b.tg_channel.lower().lstrip('@') for b in bindings}
        
        # Filter verified channels to only those with Max bindings
        eligible_channels = [
            ch for ch in verified_channels 
            if ch.tg_channel.lower().lstrip('@') in bound_channel_names
        ]
        
        if not eligible_channels:
            text = (
                "⚡ <b>Подключить автопостинг</b>\n\n"
                "У ваших верифицированных каналов нет привязки к Max.\n\n"
                "Сначала необходимо привязать канал к Max в разделе "
                "<b>📥 Настроить перенос</b>."
            )
            
            builder = InlineKeyboardBuilder()
            builder.button(text="📥 Настроить переноз", callback_data="menu_new_transfer")
            builder.button(text="↩️ Назад", callback_data="menu_manage_autopost")
            builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
            builder.adjust(1)
            
            await callback.message.edit_text(text, reply_markup=builder.as_markup())
            return
        
        # Get user's balance
        balance, _ = await balance_repo.get_or_create(user_id)
        balance_int = int(balance.balance)
        
        # Show eligible channels
        text = (
            "⚡ <b>Подключить автопостинг</b>\n\n"
            "Выберите Telegram-канал:\n\n"
            "<i>Доступны только каналы с уже настроенной привязкой к Max. "
            "Новые посты будут автоматически переноситься в реальном времени.</i>\n\n"
            f"💰 Ваш баланс: {balance_int}₽"
        )
        
        builder = InlineKeyboardBuilder()
        
        for channel in eligible_channels:
            tg_channel = channel.tg_channel.lstrip('@')
            builder.button(
                text=f"📢 @{tg_channel}",
                callback_data=f"autopost_select_ch:{tg_channel}"
            )
        
        builder.button(text="↩️ Назад", callback_data="menu_manage_autopost")
        builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
        builder.adjust(1)
        
        await callback.message.edit_text(text, reply_markup=builder.as_markup())
        
    except Exception as e:
        logger.error(f"Error in start_new_autopost: {e}")
        await callback.message.edit_text(
            "❌ Произошла ошибка. Попробуйте позже.",
            reply_markup=back_to_menu_keyboard()
        )


@autopost_router.callback_query(lambda c: c.data.startswith("autopost_select_ch:"))
async def select_channel_for_autopost(
    callback: CallbackQuery,
    state: FSMContext,
    max_binding_repo,
    autopost_sub_repo,
    balance_repo,
) -> None:
    """Handle TG channel selection - show confirmation for autopost creation."""
    user_id = callback.from_user.id
    tg_channel = callback.data.split(":", 1)[1]
    
    try:
        # Check if subscription already exists for this channel
        existing = await autopost_sub_repo.get_by_tg_channel(user_id, tg_channel)
        
        if existing:
            await callback.answer(
                "⚠️ Автопостинг для этого канала уже существует",
                show_alert=True
            )
            await _render_autopost_list(callback.message, user_id, autopost_sub_repo, balance_repo)
            return
        
        # Get the Max binding for this channel
        bindings = await max_binding_repo.get_by_user(user_id)
        binding = None
        
        for b in bindings:
            if b.tg_channel.lower().lstrip('@') == tg_channel.lower().lstrip('@'):
                binding = b
                break
        
        if not binding:
            await callback.answer(
                "❌ Привязка к Max не найдена. Настройте перенос сначала.",
                show_alert=True
            )
            await _render_autopost_list(callback.message, user_id, autopost_sub_repo, balance_repo)
            return
        
        # Store selection in state
        await state.update_data(
            selected_tg_channel=tg_channel,
            selected_max_chat_id=binding.max_chat_id,
            selected_max_channel_name=binding.max_channel_name
        )
        
        # Get balance
        balance, _ = await balance_repo.get_or_create(user_id)
        balance_int = int(balance.balance)
        
        # Show confirmation
        text = (
            f"⚡ <b>Включить автопостинг?</b>\n\n"
            f"Канал: @{tg_channel}\n\n"
            f"Новые посты будут автоматически появляться в Max.\n\n"
            f"Стоимость: {COST_PER_POST}₽ за пост.\n\n"
            f"💰 Ваш баланс: {balance_int}₽"
        )
        
        builder = InlineKeyboardBuilder()
        builder.button(
            text="✅ Включить",
            callback_data=f"autopost_create_confirm:{tg_channel}:{binding.max_chat_id}"
        )
        builder.button(
            text="❌ Отмена",
            callback_data="autopost_new"
        )
        builder.adjust(1)
        
        await callback.message.edit_text(text, reply_markup=builder.as_markup())
        
    except Exception as e:
        logger.error(f"Error in select_channel_for_autopost: {e}")
        await callback.message.edit_text(
            "❌ Произошла ошибка. Попробуйте позже.",
            reply_markup=back_to_menu_keyboard()
        )


@autopost_router.callback_query(lambda c: c.data.startswith("autopost_create_confirm:"))
async def confirm_create_autopost(
    callback: CallbackQuery,
    state: FSMContext,
    session,
    autopost_sub_repo,
    balance_repo,
) -> None:
    """Create new autopost subscription after confirmation."""
    # Answer callback immediately to prevent timeout
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass

    user_id = callback.from_user.id
    parts = callback.data.split(":")
    tg_channel = parts[1]
    max_chat_id = parts[2] if len(parts) > 2 else None

    try:
        # Double-check if subscription already exists
        existing = await autopost_sub_repo.get_by_tg_channel(user_id, tg_channel)
        
        if existing:
            await callback.answer(
                "⚠️ Автопостинг для этого канала уже существует",
                show_alert=True
            )
            await state.clear()
            await _render_autopost_list(callback.message, user_id, autopost_sub_repo, balance_repo)
            return
        
        # Get stored channel name from state
        state_data = await state.get_data()
        max_channel_name = state_data.get("selected_max_channel_name")

        # Try to get tg_channel_id from verified_channels
        tg_channel_id = None
        try:
            verified_repo = VerifiedChannelRepository(session)
            verified = await verified_repo.get_verified_channel(user_id, tg_channel)
            if verified and verified.tg_channel_id:
                tg_channel_id = verified.tg_channel_id
                logger.info(f"Found tg_channel_id={tg_channel_id} for @{tg_channel} from verified_channels")
        except Exception as e:
            logger.warning(f"Could not get tg_channel_id from verified_channels: {e}")

        # Create new subscription
        sub = await autopost_sub_repo.create(
            user_id=user_id,
            tg_channel=tg_channel,
            max_chat_id=max_chat_id,
            is_active=True,
            posts_transferred=0,
            tg_channel_id=tg_channel_id,
        )
        
        if max_channel_name:
            # Update the subscription with the channel name if available
            # Note: This would require an update method in the repository
            pass
        
        # Set initial last_post_id to prevent catching up historical posts
        manager = get_autopost_manager()
        if manager:
            try:
                client = await manager.telethon_client._get_client()
                # Use numeric channel ID for private channels, username for public
                entity_id = int(tg_channel_id) if (tg_channel_id and str(tg_channel_id).lstrip('-').isdigit()) else tg_channel
                entity = await client.get_entity(entity_id)
                # Get the latest message from the channel
                messages = await client.get_messages(entity, limit=1)
                if messages:
                    latest_post_id = messages[0].id
                    await autopost_sub_repo.update_last_post_id(sub.id, latest_post_id)
                    logger.info(f"Set initial last_post_id={latest_post_id} for new subscription @{tg_channel}")
            except Exception as e:
                logger.warning(f"Could not set initial last_post_id: {e}")
        
        logger.info(
            f"Created new autopost subscription: user={user_id}, "
            f"tg={tg_channel}, max={max_chat_id}, id={sub.id}"
        )
        
        await callback.answer("✅ Автопостинг включён!", show_alert=True)
        
        # Show success and return to list
        text = (
            f"🎉 <b>Автопостинг включён!</b>\n\n"
            f"📢 @{tg_channel}\n"
            f"➡ Max\n\n"
            f"Новые посты будут автоматически переноситься в реальном времени."
        )
        
        builder = InlineKeyboardBuilder()
        builder.button(text="📋 К списку автопостингов", callback_data="menu_manage_autopost")
        builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
        builder.adjust(1)
        
        await callback.message.edit_text(text, reply_markup=builder.as_markup())
        
        await state.clear()
        
    except Exception as e:
        logger.error(f"Error in confirm_create_autopost: {e}")
        await callback.message.edit_text(
            "❌ Произошла ошибка при создании автопостинга. Попробуйте позже.",
            reply_markup=back_to_menu_keyboard()
        )
        await state.clear()


# =============================================================================
# Legacy Handlers (for backward compatibility)
# =============================================================================


@autopost_router.callback_query(lambda c: c.data == "start_setup_autopost")
@autopost_router.callback_query(lambda c: c.data == "menu_new_autopost")
async def start_autopost_setup_legacy(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Legacy entry point - redirects to new flow."""
    await callback.answer()
    await state.clear()
    
    text = (
        "⚡ <b>Автопостинг</b>\n\n"
        "Выберите действие:\n\n"
        "📋 <b>Мои автопостинги</b> - управление существующими\n"
        "➕ <b>Новый автопостинг</b> - создать новый"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Мои автопостинги", callback_data="menu_manage_autopost")
    builder.button(text="➕ Новый автопостинг", callback_data="autopost_new")
    builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
    builder.adjust(1)
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup())


@autopost_router.callback_query(lambda c: c.data == "autopost_cancel")
async def cancel_autopost(callback: CallbackQuery, state: FSMContext) -> None:
    """Cancel autopost setup and return to menu."""
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(
        "❌ Настройка автопостинга отменена.",
        reply_markup=back_to_menu_keyboard(),
    )
