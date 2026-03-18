"""Auto-posting setup handler - FSM flow for connecting TG channels to Max."""

from aiogram import Router
from aiogram.filters import StateFilter
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from loguru import logger

from bot.telegram.states import AutopostStates
from bot.telegram.keyboards.autopost import (
    check_admin_keyboard,
    back_to_menu_keyboard,
    autopost_complete_keyboard,
    autopost_list_keyboard,
    autopost_manage_keyboard,
    autopost_confirm_delete_keyboard,
    autopost_channel_select_keyboard,
    autopost_max_select_keyboard,
    autopost_confirm_creation_keyboard,
)
from bot.max_api.client import MaxClient, MaxAPIError


# Bot configuration
MAX_BOT_USERNAME = "id752703975446_1_bot"
MAX_BOT_LINK = "https://max.ru/id752703975446_1_bot"
TG_BOT_USERNAME = "maxx_repost_bot"

# Constants
COST_PER_POST = 3  # 3 rubles per post


# Create router
autopost_router = Router(name="autopost")


# =============================================================================
# Screen 1: List of Autopostings (callback: menu_manage_autopost)
# =============================================================================


@autopost_router.callback_query(lambda c: c.data == "menu_manage_autopost")
async def show_autopost_list(
    callback: CallbackQuery,
    autopost_sub_repo,
    balance_repo,
) -> None:
    """
    Show list of user's autopost subscriptions with statistics.
    
    This is the main autopost management screen.
    
    Args:
        callback: Callback query
        autopost_sub_repo: Repository for autopost subscriptions
        balance_repo: Repository for user balances
    """
    await callback.answer("⏳")
    
    user_id = callback.from_user.id
    
    try:
        # Get user's subscriptions and balance
        subscriptions = await autopost_sub_repo.get_user_subscriptions(user_id)
        balance, _ = await balance_repo.get_or_create(user_id)
        balance_str = f"{int(balance.balance)}₽"
        
        if subscriptions:
            # Build subscription list text
            lines = ["⚡ <b>Ваши автопостинги:</b>\n"]
            
            for sub in subscriptions:
                # Status emoji
                if sub.is_active:
                    status_emoji = "✅"
                    status_text = "Активен"
                else:
                    status_emoji = "⏸"
                    status_text = "Приостановлен"
                    if sub.paused_reason == "insufficient_funds":
                        status_text = "Приостановлен: недостаточно средств"
                
                # Max channel display name
                max_name = sub.max_channel_name or f"Max {sub.max_chat_id[:15]}..."
                
                # Calculate spent amount
                spent = sub.posts_transferred * COST_PER_POST
                
                line = (
                    f"{status_emoji} @{sub.tg_channel} → {max_name} ({COST_PER_POST}₽/пост)\n"
                    f"   Перенесено: {sub.posts_transferred} постов | Потрачено: {spent}₽"
                )
                
                if not sub.is_active and sub.paused_reason:
                    line += f"\n   <i>{status_text}</i>"
                
                lines.append(line)
            
            lines.append(f"\n💰 <b>Баланс:</b> {balance_str}")
            
            text = "\n\n".join(lines)
            
            # Build keyboard with subscription buttons
            builder = InlineKeyboardBuilder()
            
            for sub in subscriptions:
                status_emoji = "✅" if sub.is_active else "⏸"
                display_name = sub.tg_channel[:25] + "..." if len(sub.tg_channel) > 25 else sub.tg_channel
                builder.button(
                    text=f"{status_emoji} Управлять @{display_name}",
                    callback_data=f"autopost_manage:{sub.id}"
                )
            
            builder.button(text="➕ Новый автопостинг", callback_data="autopost_new")
            builder.button(text="📢 Мои каналы", callback_data="menu_my_channels")
            builder.button(text="💰 Пополнить баланс", callback_data="menu_topup_balance")
            builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
            builder.adjust(1)
            
            await callback.message.edit_text(text, reply_markup=builder.as_markup())
        else:
            # No subscriptions yet
            text = (
                "⚡ <b>Автопостинг</b>\n\n"
                "У вас пока нет настроенных автопостингов.\n\n"
                "Автопостинг автоматически переносит новые посты из вашего "
                f"Telegram-канала в Max за {COST_PER_POST}₽ за пост.\n\n"
                f"💰 <b>Баланс:</b> {balance_str}"
            )
            
            await callback.message.edit_text(
                text,
                reply_markup=autopost_list_keyboard(has_subscriptions=False)
            )
            
    except Exception as e:
        logger.error(f"Error in show_autopost_list: {e}")
        await callback.message.edit_text(
            "❌ Произошла ошибка при загрузке автопостингов. Попробуйте позже.",
            reply_markup=back_to_menu_keyboard()
        )


# =============================================================================
# Screen 2: Manage Specific Autoposting (callback: autopost_manage:{id})
# =============================================================================


@autopost_router.callback_query(lambda c: c.data.startswith("autopost_manage:"))
async def show_autopost_management(
    callback: CallbackQuery,
    autopost_sub_repo,
) -> None:
    """
    Show management screen for a specific autopost subscription.
    
    Args:
        callback: Callback query
        autopost_sub_repo: Repository for autopost subscriptions
    """
    await callback.answer("⏳")
    
    user_id = callback.from_user.id
    subscription_id = int(callback.data.split(":", 1)[1])
    
    try:
        # Get subscription
        sub = await autopost_sub_repo.get_by_id_and_user(subscription_id, user_id)
        
        if not sub:
            await callback.answer("❌ Автопостинг не найден", show_alert=True)
            await show_autopost_list(callback, autopost_sub_repo, None)
            return
        
        # Calculate statistics
        spent = sub.posts_transferred * COST_PER_POST
        
        # Status display
        if sub.is_active:
            status_display = "✅ Активен"
        else:
            status_display = "⏸ Приостановлен"
            if sub.paused_reason == "insufficient_funds":
                status_display = "⏸ Приостановлен: недостаточно средств"
        
        max_name = sub.max_channel_name or f"Max канал {sub.max_chat_id[:20]}..."
        
        text = (
            f"📢 <b>@{sub.tg_channel}</b>\n\n"
            f"<b>Статус:</b> {status_display}\n"
            f"<b>Max канал:</b> {max_name}\n"
            f"<b>Стоимость:</b> {COST_PER_POST}₽/пост\n"
            f"<b>Перенесено:</b> {sub.posts_transferred} постов\n"
            f"<b>Потрачено:</b> {spent}₽"
        )
        
        await callback.message.edit_text(
            text,
            reply_markup=autopost_manage_keyboard(subscription_id, sub.is_active)
        )
        
    except Exception as e:
        logger.error(f"Error in show_autopost_management: {e}")
        await callback.message.edit_text(
            "❌ Произошла ошибка. Попробуйте позже.",
            reply_markup=back_to_menu_keyboard()
        )


@autopost_router.callback_query(lambda c: c.data.startswith("autopost_toggle:"))
async def toggle_autopost_handler(
    callback: CallbackQuery,
    autopost_sub_repo,
) -> None:
    """
    Toggle autopost subscription active status.
    
    Args:
        callback: Callback query
        autopost_sub_repo: Repository for autopost subscriptions
    """
    await callback.answer("⏳")
    
    user_id = callback.from_user.id
    subscription_id = int(callback.data.split(":", 1)[1])
    
    try:
        sub = await autopost_sub_repo.toggle_status(subscription_id, user_id)
        
        if sub:
            status_text = "возобновлен" if sub.is_active else "приостановлен"
            await callback.answer(f"✅ Автопостинг {status_text}", show_alert=True)
        else:
            await callback.answer("❌ Не удалось изменить статус", show_alert=True)
        
        # Refresh the management screen
        callback.data = f"autopost_manage:{subscription_id}"
        await show_autopost_management(callback, autopost_sub_repo)
        
    except Exception as e:
        logger.error(f"Error in toggle_autopost_handler: {e}")
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@autopost_router.callback_query(lambda c: c.data.startswith("autopost_delete:"))
async def delete_autopost_confirm(
    callback: CallbackQuery,
    autopost_sub_repo,
) -> None:
    """
    Show confirmation for deleting autopost subscription.
    
    Args:
        callback: Callback query
        autopost_sub_repo: Repository for autopost subscriptions
    """
    await callback.answer()
    
    user_id = callback.from_user.id
    subscription_id = int(callback.data.split(":", 1)[1])
    
    try:
        sub = await autopost_sub_repo.get_by_id_and_user(subscription_id, user_id)
        
        if not sub:
            await callback.answer("❌ Автопостинг не найден", show_alert=True)
            await show_autopost_list(callback, autopost_sub_repo, None)
            return
        
        text = (
            f"🗑 <b>Удаление автопостинга</b>\n\n"
            f"Вы уверены, что хотите удалить автопостинг?\n\n"
            f"📢 @{sub.tg_channel} → {sub.max_channel_name or sub.max_chat_id}\n\n"
            f"<i>Это действие нельзя отменить. Все настройки будут удалены.</i>"
        )
        
        await callback.message.edit_text(
            text,
            reply_markup=autopost_confirm_delete_keyboard(subscription_id)
        )
        
    except Exception as e:
        logger.error(f"Error in delete_autopost_confirm: {e}")
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@autopost_router.callback_query(lambda c: c.data.startswith("autopost_delete_confirm:"))
async def delete_autopost_handler(
    callback: CallbackQuery,
    autopost_sub_repo,
) -> None:
    """
    Delete autopost subscription.
    
    Args:
        callback: Callback query
        autopost_sub_repo: Repository for autopost subscriptions
    """
    await callback.answer("⏳")
    
    user_id = callback.from_user.id
    subscription_id = int(callback.data.split(":", 1)[1])
    
    try:
        success = await autopost_sub_repo.delete_by_user(subscription_id, user_id)
        
        if success:
            await callback.answer("✅ Автопостинг удален", show_alert=True)
        else:
            await callback.answer("❌ Не удалось удалить автопостинг", show_alert=True)
        
        # Return to list
        callback.data = "menu_manage_autopost"
        await show_autopost_list(callback, autopost_sub_repo, None)
        
    except Exception as e:
        logger.error(f"Error in delete_autopost_handler: {e}")
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@autopost_router.callback_query(lambda c: c.data.startswith("autopost_price:"))
async def change_price_handler(callback: CallbackQuery) -> None:
    """
    Show price change info (price is fixed at 3₽ for now).
    
    Args:
        callback: Callback query
    """
    await callback.answer(
        f"💰 Стоимость фиксирована: {COST_PER_POST}₽ за пост",
        show_alert=True
    )


# =============================================================================
# Screen 3: Creating New Autoposting
# =============================================================================


@autopost_router.callback_query(lambda c: c.data == "autopost_new")
async def start_new_autopost(
    callback: CallbackQuery,
    state: FSMContext,
    verified_channel_repo,
    max_binding_repo,
) -> None:
    """
    Start creating new autopost subscription.
    
    Step 1: Show list of verified channels to select from.
    
    Args:
        callback: Callback query
        state: FSM state
        verified_channel_repo: Repository for verified channels
        max_binding_repo: Repository for Max channel bindings
    """
    await callback.answer("⏳")
    
    user_id = callback.from_user.id
    
    try:
        # Get user's verified channels
        channels = await verified_channel_repo.get_user_verified_channels(user_id)
        
        if not channels:
            # No verified channels - need to verify first
            text = (
                "⚡ <b>Новый автопостинг</b>\n\n"
                "Для создания автопостинга необходимо сначала верифицировать "
                "ваш Telegram-канал.\n\n"
                "👉 Отправьте ссылку на ваш канал:\n"
                "<i>https://t.me/channelname</i>"
            )
            
            await callback.message.edit_text(text)
            await state.set_state(AutopostStates.creating_select_channel)
            return
        
        # Show verified channels
        text = (
            "⚡ <b>Новый автопостинг</b>\n\n"
            "Выберите Telegram-канал для автопостинга:\n\n"
            "<i>Или отправьте ссылку на новый канал</i>"
        )
        
        # Build channel list
        channel_list = [
            {"username": ch.tg_channel, "title": ch.tg_channel}
            for ch in channels
        ]
        
        await callback.message.edit_text(
            text,
            reply_markup=autopost_channel_select_keyboard(
                channel_list,
                action_prefix="autopost_select_ch"
            )
        )
        
        await state.set_state(AutopostStates.creating_select_channel)
        
    except Exception as e:
        logger.error(f"Error in start_new_autopost: {e}")
        await callback.message.edit_text(
            "❌ Произошла ошибка. Попробуйте позже.",
            reply_markup=back_to_menu_keyboard()
        )


@autopost_router.callback_query(
    lambda c: c.data.startswith("autopost_select_ch:"),
    StateFilter(AutopostStates.creating_select_channel)
)
async def select_channel_for_autopost(
    callback: CallbackQuery,
    state: FSMContext,
    max_binding_repo,
) -> None:
    """
    Handle TG channel selection for new autopost.
    
    Step 2: Show list of saved Max channels or option to add new.
    
    Args:
        callback: Callback query
        state: FSM state
        max_binding_repo: Repository for Max channel bindings
    """
    await callback.answer("⏳")
    
    user_id = callback.from_user.id
    tg_channel = callback.data.split(":", 1)[1]
    
    try:
        # Store selected channel
        await state.update_data(selected_tg_channel=tg_channel)
        
        # Get saved Max bindings for this user
        bindings = await max_binding_repo.get_by_user(user_id)
        
        text = (
            f"⚡ <b>Новый автопостинг</b>\n\n"
            f"📢 Telegram: @{tg_channel}\n\n"
            f"Теперь выберите Max канал для публикации:"
        )
        
        if bindings:
            # Show saved bindings
            binding_list = [
                {
                    "max_chat_id": b.max_chat_id,
                    "max_channel_name": b.max_channel_name
                }
                for b in bindings
            ]
            
            await callback.message.edit_text(
                text,
                reply_markup=autopost_max_select_keyboard(binding_list, tg_channel)
            )
        else:
            # No saved bindings - ask for new Max channel
            builder = InlineKeyboardBuilder()
            builder.button(
                text="➕ Указать Max канал",
                callback_data=f"autopost_new_max:{tg_channel}"
            )
            builder.button(text="↩️ Назад", callback_data="autopost_new")
            builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
            builder.adjust(1)
            
            await callback.message.edit_text(text, reply_markup=builder.as_markup())
        
        await state.set_state(AutopostStates.creating_select_max)
        
    except Exception as e:
        logger.error(f"Error in select_channel_for_autopost: {e}")
        await callback.message.edit_text(
            "❌ Произошла ошибка. Попробуйте позже.",
            reply_markup=back_to_menu_keyboard()
        )


@autopost_router.callback_query(
    lambda c: c.data.startswith("autopost_select_max:")
)
async def select_max_for_autopost(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """
    Handle Max channel selection for new autopost.
    
    Step 3: Show confirmation screen.
    
    Args:
        callback: Callback query
        state: FSM state
    """
    await callback.answer("⏳")
    
    parts = callback.data.split(":")
    tg_channel = parts[1]
    max_chat_id = parts[2] if len(parts) > 2 else None
    
    if max_chat_id == "back":
        # User clicked back - return to channel selection
        callback.data = "autopost_new"
        await start_new_autopost(callback, state, None, None)
        return
    
    try:
        # Store selected Max channel
        await state.update_data(selected_max_chat_id=max_chat_id)
        
        text = (
            f"⚡ <b>Подтверждение автопостинга</b>\n\n"
            f"📢 Telegram: @{tg_channel}\n"
            f"➡ Max: {max_chat_id}\n\n"
            f"💰 <b>Стоимость:</b> {COST_PER_POST}₽ за пост\n\n"
            f"<i>Новые посты будут автоматически переноситься из Telegram в Max. "
            f"Средства будут списываться с баланса автоматически.</i>"
        )
        
        await callback.message.edit_text(
            text,
            reply_markup=autopost_confirm_creation_keyboard(tg_channel, max_chat_id)
        )
        
        await state.set_state(AutopostStates.creating_confirm)
        
    except Exception as e:
        logger.error(f"Error in select_max_for_autopost: {e}")
        await callback.message.edit_text(
            "❌ Произошла ошибка. Попробуйте позже.",
            reply_markup=back_to_menu_keyboard()
        )


@autopost_router.callback_query(
    lambda c: c.data.startswith("autopost_create_confirm:"),
    StateFilter(AutopostStates.creating_confirm)
)
async def confirm_create_autopost(
    callback: CallbackQuery,
    state: FSMContext,
    autopost_sub_repo,
) -> None:
    """
    Create new autopost subscription after confirmation.
    
    Args:
        callback: Callback query
        state: FSM state
        autopost_sub_repo: Repository for autopost subscriptions
    """
    await callback.answer("⏳")
    
    user_id = callback.from_user.id
    parts = callback.data.split(":")
    tg_channel = parts[1]
    max_chat_id = parts[2] if len(parts) > 2 else None
    
    try:
        # Check if subscription already exists
        existing = await autopost_sub_repo.get_by_tg_channel(user_id, tg_channel)
        
        if existing:
            await callback.answer(
                "⚠️ Автопостинг для этого канала уже существует",
                show_alert=True
            )
            callback.data = "menu_manage_autopost"
            await show_autopost_list(callback, autopost_sub_repo, None)
            await state.clear()
            return
        
        # Create new subscription
        sub = await autopost_sub_repo.create(
            user_id=user_id,
            tg_channel=tg_channel,
            max_chat_id=max_chat_id,
            is_active=True,
            posts_transferred=0,
        )
        
        logger.info(
            f"Created new autopost subscription: user={user_id}, "
            f"tg={tg_channel}, max={max_chat_id}, id={sub.id}"
        )
        
        await callback.answer("✅ Автопостинг создан!", show_alert=True)
        
        # Show success and return to list
        text = (
            f"🎉 <b>Автопостинг создан!</b>\n\n"
            f"📢 @{tg_channel}\n"
            f"➡ Max: {max_chat_id}\n"
            f"💰 {COST_PER_POST}₽/пост\n\n"
            f"Новые посты будут автоматически переноситься."
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
    """
    Legacy entry point - redirects to new flow.
    
    Args:
        callback: Callback query
        state: FSM state
    """
    await callback.answer("⏳")
    
    text = (
        "⚡ <b>Настройка автопостинга</b>\n\n"
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


# =============================================================================
# Message Handlers for New Channel Input
# =============================================================================


@autopost_router.message(StateFilter(AutopostStates.creating_select_channel))
async def process_new_channel_input(
    message: Message,
    state: FSMContext,
    verified_channel_repo,
) -> None:
    """
    Process new channel input during autopost creation.
    
    Args:
        message: User message with channel link
        state: FSM state
        verified_channel_repo: Repository for verified channels
    """
    text = message.text.strip()
    
    # Parse channel link
    if text.startswith("https://t.me/"):
        channel_username = text.replace("https://t.me/", "").strip("/")
    elif text.startswith("t.me/"):
        channel_username = text.replace("t.me/", "").strip("/")
    elif text.startswith("@"):
        channel_username = text[1:]
    else:
        channel_username = text.strip("/@")
    
    if not channel_username:
        await message.answer(
            "❌ Не удалось распознать ссылку.\n\n"
            "Отправьте ссылку в формате:\n"
            "<i>https://t.me/channelname</i>",
            parse_mode="HTML"
        )
        return
    
    # Check if channel is already verified
    # For now, redirect to verification flow
    await message.answer(
        f"📢 Канал @{channel_username}\n\n"
        f"Для продолжения необходимо верифицировать канал. "
        f"Используйте раздел <b>📥 Настроить перенос</b> сначала.",
        parse_mode="HTML",
        reply_markup=back_to_menu_keyboard()
    )
    
    await state.clear()
