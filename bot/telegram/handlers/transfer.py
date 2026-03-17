"""Post transfer handler - FSM flow for manual post transfer from TG to Max."""

import re
import time
from typing import Optional

from aiogram import Router
from aiogram.filters import StateFilter
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from bot.telegram.states import TransferStates
from bot.telegram.keyboards.transfer import (
    back_keyboard,
    back_to_start_keyboard,
    detect_channel_keyboard,
    confirm_channel_keyboard,
    retry_detect_keyboard,
    select_count_keyboard,
    transfer_complete_keyboard,
    saved_max_channels_keyboard,
    confirm_delete_binding_keyboard,
    verify_code_keyboard,
)
from bot.core.verification import generate_verification_code, verify_channel_ownership
from bot.max_api.client import MaxClient, MaxAPIError
from bot.core.telethon_client import get_telethon_client
from bot.core.transfer_engine import TransferEngine, TransferResult
from bot.database.repositories.max_channel_binding import MaxChannelBindingRepository
from config.settings import settings


# =============================================================================
# Utilities
# =============================================================================


async def _delete_user_message(message: Message) -> None:
    """Delete user message (ignore errors if no permission)."""
    try:
        await message.delete()
    except Exception:
        pass  # Bot may not have delete permission


async def _edit_or_send_message(
    target_message: Message | CallbackQuery,
    text: str,
    state,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str = "HTML",
) -> Message:
    """
    Edit existing bot message or send new one.
    
    Stores message_id in state for future edits.
    
    Args:
        target_message: Message or CallbackQuery to respond to
        state: FSM state
        text: Text to send
        reply_markup: Optional keyboard
        parse_mode: Parse mode for text
        
    Returns:
        Sent or edited message
    """
    data = await state.get_data()
    bot_msg_id = data.get("bot_message_id")
    chat_id = data.get("chat_id")
    
    # Try to edit existing message
    if bot_msg_id and chat_id:
        try:
            from bot.telegram.bot import bot
            edited = await bot.edit_message_text(
                chat_id=chat_id,
                message_id=bot_msg_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            return edited
        except Exception:
            pass  # Fall through to send new message
    
    # Try to edit callback message if it's a CallbackQuery
    if isinstance(target_message, CallbackQuery):
        try:
            edited = await target_message.message.edit_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            # Store message_id for future edits
            await state.update_data(bot_message_id=edited.message_id, chat_id=edited.chat.id)
            return edited
        except Exception:
            # Fall through to send new message
            target_message = target_message.message
    
    # Send new message
    try:
        sent = await target_message.answer(
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        # Store message_id for future edits
        await state.update_data(bot_message_id=sent.message_id, chat_id=sent.chat.id)
        return sent
    except Exception:
        # Fallback: try to answer on message directly
        if hasattr(target_message, 'chat'):
            from bot.telegram.bot import bot
            sent = await bot.send_message(
                chat_id=target_message.chat.id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            await state.update_data(bot_message_id=sent.message_id, chat_id=sent.chat.id)
            return sent
        raise


def _strip_html(text: str, max_length: int = 200) -> str:
    """
    Remove HTML tags from text and truncate.

    Args:
        text: Text that may contain HTML
        max_length: Maximum length of returned text

    Returns:
        Clean text without HTML tags
    """
    if not text:
        return ""

    # Remove HTML tags
    clean = re.sub(r'<[^>]+>', ' ', text)
    # Remove extra whitespace
    clean = re.sub(r'\s+', ' ', clean).strip()
    # Truncate if too long
    if len(clean) > max_length:
        clean = clean[:max_length] + "..."
    return clean


# Bot configuration
MAX_BOT_USERNAME = "id752703975446_1_bot"
MAX_BOT_LINK = "https://max.ru/id752703975446_1_bot"
TG_BOT_USERNAME = "maxx_repost_bot"
PRICE_PER_POST = 3  # rubles per post


# Create router
transfer_router = Router(name="transfer")


# =============================================================================
# Entry Points
# =============================================================================


@transfer_router.callback_query(lambda c: c.data == "start_setup_transfer")
@transfer_router.callback_query(lambda c: c.data == "menu_new_transfer")
async def start_transfer_setup(callback: CallbackQuery, state) -> None:
    """
    Start post transfer setup flow.

    Explains the 4 stages and asks for TG channel link.

    Args:
        callback: Callback query
        state: FSM state
    """
    # Store user_id and chat_id in state for later use
    await state.update_data(
        user_id=callback.from_user.id,
        chat_id=callback.message.chat.id,
        bot_message_id=callback.message.message_id,
    )
    
    await callback.message.edit_text(
        "<b>🔗 Пришлите ссылку на ваш Telegram-канал</b>\n\n"
        "Мы выполним перенос постов в 4 этапа:\n"
        "1. <b>Анализ</b>: Посчитаем количество постов.\n"
        "2. <b>Расчёт</b>: Определим стоимость переноса.\n"
        "3. <b>Подключение</b>: Настроим связь с MAX.\n"
        "4. <b>Запуск</b>: Начнём перенос контента.\n\n"
        "👉 Отправьте ссылку на ваш публичный Telegram-канал:\n"
        "<i>https://t.me/channelname</i>",
        parse_mode="HTML",
    )
    await callback.answer()

    # Set FSM state
    await state.set_state(TransferStates.transfer_waiting_tg_channel)


@transfer_router.message(StateFilter(TransferStates.transfer_waiting_tg_channel))
async def process_transfer_tg_channel(message: Message, state, bot) -> None:
    """
    Process Telegram channel link for transfer.

    Similar to autopost flow - validates and gets channel info.

    Args:
        message: User message with channel link
        state: FSM state
        bot: Bot instance for API calls
    """
    # Delete user message
    await _delete_user_message(message)
    
    text = message.text.strip()
    channel_username = None

    # Parse channel link
    if text.startswith("https://t.me/"):
        channel_username = text.replace("https://t.me/", "").strip("/")
    elif text.startswith("@"):
        channel_username = text[1:]
    elif text.startswith("t.me/"):
        channel_username = text.replace("t.me/", "").strip("/")
    else:
        channel_username = text.strip("/@")

    if not channel_username:
        await _edit_or_send_message(
            message, state,
            "❌ Не удалось распознать ссылку на канал.\n\n"
            "Отправьте ссылку в формате:\n"
            "<i>https://t.me/channelname</i> или <i>@channelname</i>",
        )
        return

    # Store channel username in state
    await state.update_data(transfer_tg_channel_username=channel_username)

    # Try to get chat info
    try:
        chat = await bot.get_chat(f"@{channel_username}")
    except Exception as e:
        logger.error(f"Failed to get chat info: {e}")
        await _edit_or_send_message(
            message, state,
            "❌ Не удалось получить информацию о канале.\n\n"
            "Убедитесь, что:\n"
            "• Канал публичный\n"
            "• Ссылка корректная\n\n"
            "Попробуйте снова:",
            reply_markup=back_keyboard(),
        )
        return

    # Store chat info
    await state.update_data(
        transfer_tg_channel_id=str(chat.id),
        transfer_tg_channel_title=chat.title,
        transfer_tg_channel_username=channel_username,
    )

    # Check if bot is admin (required for accessing channel history)
    bot_user = await bot.me()
    member = await bot.get_chat_member(chat.id, bot_user.id)

    from aiogram.enums import ChatMemberStatus

    if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
        await _edit_or_send_message(
            message, state,
            f"<b>📢 Канал найден: {chat.title}</b>\n\n"
            f"Для доступа к постам бота нужно добавить в администраторы.\n\n"
            f"<b>Инструкция:</b>\n"
            f"1. Откройте настройки канала ➡ Администраторы.\n"
            f"2. Добавьте @{TG_BOT_USERNAME} как администратора.\n"
            f"3. Сохраните изменения и нажмите «Продолжить».",
            reply_markup=_build_continue_keyboard(),
        )
        await state.set_state(TransferStates.transfer_waiting_verification)
    else:
        # Bot is already admin - proceed to ownership verification
        await _show_verification_code(message, state, chat.title)


def _build_continue_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard for 'Continue' after adding bot as admin."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Продолжить", callback_data="transfer_verify_admin")
    builder.button(text="↩️ Назад", callback_data="nav_goto_menu")
    builder.adjust(1)
    return builder.as_markup()


async def _show_verification_code(
    target_message: Message | CallbackQuery,
    state,
    channel_title: str,
) -> None:
    """
    Show verification code for channel ownership confirmation.
    
    Args:
        target_message: Message or CallbackQuery to edit
        state: FSM state
        channel_title: Telegram channel title
    """
    # Generate verification code
    code = generate_verification_code()
    await state.update_data(verification_code=code)
    
    text = (
        f"<b>🔐 Подтверждение прав владения</b>\n\n"
        f"Канал: <b>{channel_title}</b>\n\n"
        f"Для подтверждения что вы владелец канала:\n"
        f"1. Откройте настройки Telegram-канала\n"
        f"2. Перейдите в раздел «Описание»\n"
        f"3. Вставьте код: <code>{code}</code>\n"
        f"4. Нажмите «Проверить» ниже\n\n"
        f"⚠️ Код можно удалить сразу после проверки."
    )
    
    await _edit_or_send_message(
        target_message, state,
        text=text,
        reply_markup=verify_code_keyboard(),
    )
    await state.set_state(TransferStates.transfer_verify_code)


@transfer_router.callback_query(lambda c: c.data == "transfer_verify_admin", StateFilter(TransferStates.transfer_waiting_verification))
async def verify_admin_after_prompt(callback: CallbackQuery, state, bot) -> None:
    """
    Verify bot is now admin after user added it.

    Args:
        callback: Callback query
        state: FSM state
        bot: Bot instance
    """
    data = await state.get_data()
    channel_id = data.get("transfer_tg_channel_id")
    channel_title = data.get("transfer_tg_channel_title", "Канал")

    if not channel_id:
        await callback.message.edit_text(
            "❌ Ошибка: данные канала утеряны. Начните заново.",
            reply_markup=back_to_start_keyboard(),
        )
        await callback.answer()
        await state.clear()
        return

    try:
        # Check if bot is admin
        bot_user = await bot.me()
        member = await bot.get_chat_member(channel_id, bot_user.id)

        from aiogram.enums import ChatMemberStatus

        if member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            # Bot is admin - proceed to ownership verification
            await _show_verification_code(callback.message, state, channel_title)
        else:
            await callback.answer("❌ Бот ещё не добавлен в администраторы.", show_alert=True)

    except Exception as e:
        logger.error(f"Error verifying admin: {e}")
        await callback.answer("❌ Ошибка проверки. Попробуйте снова.", show_alert=True)


async def _show_max_connection_instructions(
    target_message: Message | CallbackQuery,
    state,
    channel_title: str,
    db_session=None,
) -> None:
    """
    Show Max channel connection instructions or saved channels list.
    
    If user has saved Max channel bindings for this TG channel,
    shows them for quick selection. Otherwise shows connection instructions.

    Args:
        target_message: Message or CallbackQuery to edit
        state: FSM state
        channel_title: Telegram channel title
        db_session: Optional database session
    """
    # Get user_id and tg_channel from state
    state_data = await state.get_data()
    user_id = state_data.get("user_id")
    tg_channel = state_data.get("transfer_tg_channel_username")
    tg_channel_id = state_data.get("transfer_tg_channel_id")
    
    # Store tg_channel_id in state for later use
    if tg_channel_id:
        await state.update_data(transfer_tg_channel_id=tg_channel_id)
    
    # Check for saved bindings if we have db session
    saved_bindings = []
    if db_session and user_id and tg_channel:
        try:
            binding_repo = MaxChannelBindingRepository(db_session)
            saved_bindings = await binding_repo.get_by_user_and_tg_channel(
                user_id=user_id,
                tg_channel=tg_channel,
            )
            logger.info(f"Found {len(saved_bindings)} saved Max bindings for user {user_id}, tg_channel {tg_channel}")
        except Exception as e:
            logger.warning(f"Failed to load saved bindings: {e}")
    
    if saved_bindings:
        # Show saved channels list
        text = (
            f"✅ Канал <b>{channel_title}</b> подтвержден!\n\n"
            f"📋 <b>Сохранённые каналы Max:</b>\n"
            f"Выберите канал для переноса или добавьте новый:"
        )
        
        keyboard = saved_max_channels_keyboard(saved_bindings, show_delete=True)
        
        await _edit_or_send_message(
            target_message, state,
            text=text,
            reply_markup=keyboard,
        )
        
        await state.set_state(TransferStates.transfer_select_saved_max)
    else:
        # No saved bindings - show connection instructions
        text = (
            f"✅ Канал <b>{channel_title}</b> подтвержден!\n\n"
            f"Теперь подключите канал в MAX.\n\n"
            f"<b>Инструкция:</b>\n"
            f"1. Откройте <b>Настройки канала ➡ Подписчики</b>\n"
            f"2. Добавьте подписчика «Репост» ({MAX_BOT_USERNAME})\n"
            f"3. Перейдите в <b>Настройки канала ➡ Администраторы</b>\n"
            f"4. Добавьте администратора «Репост» ({MAX_BOT_USERNAME})\n"
            f"5. Включите <b>«Писать посты»</b> и сохраните\n\n"
            f"➡ <b>Вернитесь сюда и отправьте ссылку на канал в MAX</b>\n"
            f"<i>https://max.me/username, https://max.ru/join/..., или ID канала</i>\n\n"
            f"⚠️ Если Max не находит бота по нику — попробуйте найти по названию «Репост»"
        )

        await _edit_or_send_message(
            target_message, state,
            text=text,
            reply_markup=back_keyboard(),
        )

        await state.set_state(TransferStates.transfer_waiting_max_channel)


# =============================================================================
# Channel Ownership Verification Handlers
# =============================================================================


@transfer_router.callback_query(lambda c: c.data == "verify_check", StateFilter(TransferStates.transfer_verify_code))
async def check_verification_code(callback: CallbackQuery, state) -> None:
    """
    Check if verification code is present in channel description.
    
    Args:
        callback: Callback query
        state: FSM state
    """
    data = await state.get_data()
    code = data.get("verification_code")
    tg_channel = data.get("transfer_tg_channel_username")
    channel_title = data.get("transfer_tg_channel_title", "Канал")
    
    if not code or not tg_channel:
        await callback.message.edit_text(
            "❌ Ошибка: данные верификации утеряны. Начните заново.",
            reply_markup=back_to_start_keyboard(),
        )
        await callback.answer()
        await state.clear()
        return
    
    try:
        # Get Telethon client
        telethon = get_telethon_client(
            api_id=settings.telegram_api_id,
            api_hash=settings.telegram_api_hash,
            phone=settings.telegram_phone,
        )
        
        # Verify code in channel description
        is_verified = await verify_channel_ownership(telethon, tg_channel, code)
        
        if is_verified:
            await callback.answer("✅ Права подтверждены!", show_alert=True)
            logger.info(f"Channel {tg_channel} ownership verified for user {callback.from_user.id}")
            # Proceed to Max channel selection
            await _show_max_connection_instructions(callback.message, state, channel_title)
        else:
            await callback.answer("❌ Код не найден", show_alert=True)
            await callback.message.edit_text(
                f"<b>🔐 Подтверждение прав владения</b>\n\n"
                f"Канал: <b>{channel_title}</b>\n\n"
                f"❌ <b>Код не найден в описании канала.</b>\n\n"
                f"Убедитесь что код <code>{code}</code> добавлен в описание и попробуйте снова.\n\n"
                f"Инструкция:\n"
                f"1. Откройте настройки Telegram-канала\n"
                f"2. Перейдите в раздел «Описание»\n"
                f"3. Вставьте код: <code>{code}</code>\n"
                f"4. Нажмите «Проверить» ниже\n\n"
                f"⚠️ Код можно удалить сразу после проверки.",
                reply_markup=verify_code_keyboard(),
            )
            
    except Exception as e:
        logger.error(f"Error verifying channel ownership: {e}")
        await callback.answer("❌ Ошибка проверки. Попробуйте снова.", show_alert=True)


@transfer_router.callback_query(lambda c: c.data == "verify_new_code", StateFilter(TransferStates.transfer_verify_code))
async def generate_new_verification_code(callback: CallbackQuery, state) -> None:
    """
    Generate new verification code.
    
    Args:
        callback: Callback query
        state: FSM state
    """
    data = await state.get_data()
    channel_title = data.get("transfer_tg_channel_title", "Канал")
    
    # Generate new code
    code = generate_verification_code()
    await state.update_data(verification_code=code)
    
    await callback.message.edit_text(
        f"<b>🔐 Подтверждение прав владения</b>\n\n"
        f"Канал: <b>{channel_title}</b>\n\n"
        f"<b>Новый код сгенерирован!</b>\n\n"
        f"Для подтверждения что вы владелец канала:\n"
        f"1. Откройте настройки Telegram-канала\n"
        f"2. Перейдите в раздел «Описание»\n"
        f"3. Вставьте код: <code>{code}</code>\n"
        f"4. Нажмите «Проверить» ниже\n\n"
        f"⚠️ Код можно удалить сразу после проверки.",
        reply_markup=verify_code_keyboard(),
    )
    await callback.answer("🔄 Новый код сгенерирован")


@transfer_router.callback_query(lambda c: c.data == "verify_back", StateFilter(TransferStates.transfer_verify_code))
async def back_from_verification(callback: CallbackQuery, state) -> None:
    """
    Go back from verification to TG channel input.
    
    Args:
        callback: Callback query
        state: FSM state
    """
    await callback.message.edit_text(
        "<b>🔗 Пришлите ссылку на ваш Telegram-канал</b>\n\n"
        "Мы выполним перенос постов в 4 этапа:\n"
        "1. <b>Анализ</b>: Посчитаем количество постов.\n"
        "2. <b>Расчёт</b>: Определим стоимость переноса.\n"
        "3. <b>Подключение</b>: Настроим связь с MAX.\n"
        "4. <b>Запуск</b>: Начнём перенос контента.\n\n"
        "👉 Отправьте ссылку на ваш публичный Telegram-канал:\n"
        "<i>https://t.me/channelname</i>",
        parse_mode="HTML",
    )
    await callback.answer()
    await state.set_state(TransferStates.transfer_waiting_tg_channel)


# =============================================================================
# Channel Auto-Detection Handlers
# =============================================================================


@transfer_router.callback_query(lambda c: c.data == "transfer_detect_auto", StateFilter(TransferStates.transfer_detect_max_channel))
async def detect_channel_auto(callback: CallbackQuery, state) -> None:
    """
    Auto-detect channel chat_id by listening to Max API updates.

    Args:
        callback: Callback query
        state: FSM state
    """
    import asyncio

    await callback.message.edit_text(
        "⏳ <b>Слушаю обновления Max API...</b> (до 30 сек)\n\n"
        "Если бот уже в канале — напишите любое сообщение в канал Max.",
        parse_mode="HTML",
    )
    await callback.answer()

    try:
        # Use asyncio.wait_for to prevent blocking the bot for too long
        async with MaxClient() as client:
            chat_id = await asyncio.wait_for(
                client.find_channel_chat_id(timeout=30),
                timeout=35  # Slightly longer than API timeout
            )

        if chat_id:
            # Found channel - ask for confirmation
            await callback.message.edit_text(
                f"✅ <b>Найден канал!</b>\n\n"
                f"chat_id = <code>{chat_id}</code>\n\n"
                f"Использовать этот канал?",
                parse_mode="HTML",
                reply_markup=confirm_channel_keyboard(chat_id),
            )
        else:
            # No channel found
            await callback.message.edit_text(
                "❌ <b>Не удалось определить ID</b>\n\n"
                "Попробуйте:\n"
                "1. Удалите бота из канала и добавьте заново\n"
                "2. Напишите сообщение в канал\n"
                "3. Нажмите <b>'Определить ID'</b> ещё раз",
                parse_mode="HTML",
                reply_markup=retry_detect_keyboard(),
            )

    except asyncio.TimeoutError:
        logger.warning("Channel detection timed out")
        await callback.message.edit_text(
            "❌ <b>Не удалось определить ID</b>\n\n"
            "Таймаут ожидания (30 сек).\n\n"
            "Попробуйте:\n"
            "1. Удалите бота из канала и добавьте заново\n"
            "2. Напишите сообщение в канал\n"
            "3. Нажмите <b>'Определить ID'</b> ещё раз",
            parse_mode="HTML",
            reply_markup=retry_detect_keyboard(),
        )

    except MaxAPIError as e:
        logger.error(f"Max API error during channel detection: {e}")
        await callback.message.edit_text(
            f"❌ <b>Ошибка Max API</b>\n\n"
            f"{str(e)}\n\n"
            f"Попробуйте ввести chat_id вручную:",
            parse_mode="HTML",
            reply_markup=retry_detect_keyboard(),
        )

    except Exception as e:
        logger.error(f"Unexpected error during channel detection: {e}")
        await callback.message.edit_text(
            "❌ <b>Ошибка при определении канала</b>\n\n"
            "Попробуйте ввести chat_id вручную:",
            parse_mode="HTML",
            reply_markup=retry_detect_keyboard(),
        )


@transfer_router.callback_query(lambda c: c.data.startswith("transfer_confirm_channel:"), StateFilter(TransferStates.transfer_detect_max_channel))
async def confirm_detected_channel(callback: CallbackQuery, state, db_session) -> None:
    """
    Confirm using the detected channel chat_id.

    Args:
        callback: Callback query
        state: FSM state
        db_session: Database session
    """
    # Extract chat_id from callback data
    chat_id = int(callback.data.split(":")[1])

    # Store the chat_id
    await state.update_data(transfer_max_channel_id=chat_id)
    logger.info(f"Auto-detected chat_id confirmed: {chat_id}")

    await callback.answer("✅ Канал выбран")

    # Continue to post counting (save binding for future use)
    await _continue_after_max_channel_set(callback.message, state, db_session)


@transfer_router.callback_query(lambda c: c.data == "transfer_reject_channel", StateFilter(TransferStates.transfer_detect_max_channel))
async def reject_detected_channel(callback: CallbackQuery, state) -> None:
    """
    Reject the detected channel and try again or enter manually.

    Args:
        callback: Callback query
        state: FSM state
    """
    await callback.message.edit_text(
        "🔍 <b>Определяю ID канала...</b>\n\n"
        "Убедитесь что бот добавлен в канал Max как администратор "
        "с правом <b>'Писать посты'</b>.",
        parse_mode="HTML",
        reply_markup=detect_channel_keyboard(),
    )
    await callback.answer()


# =============================================================================
# Saved Max Channels Handlers
# =============================================================================


@transfer_router.callback_query(lambda c: c.data.startswith("transfer_select_saved_max:"), StateFilter(TransferStates.transfer_select_saved_max))
async def select_saved_max_channel(callback: CallbackQuery, state, db_session) -> None:
    """
    Select a saved Max channel for transfer.
    
    Args:
        callback: Callback query
        state: FSM state
        db_session: Database session
    """
    # Extract binding_id from callback data
    binding_id = int(callback.data.split(":")[1])
    
    try:
        # Get binding from database
        binding_repo = MaxChannelBindingRepository(db_session)
        binding = await binding_repo.get(binding_id)
        
        if not binding:
            await callback.answer("❌ Канал не найден", show_alert=True)
            return
        
        # Store max_channel_id in state
        await state.update_data(transfer_max_channel_id=int(binding.max_chat_id))
        logger.info(f"Selected saved Max channel: {binding.max_chat_id} (binding_id={binding_id})")
        
        # Update last_used_at (already updated, no need to save binding again)
        await binding_repo.update_last_used(binding_id)
        
        await callback.answer("✅ Канал выбран")
        
        # Continue to post counting (don't save binding again for saved channels)
        await _continue_after_max_channel_set(callback.message, state, db_session=None)
        
    except Exception as e:
        logger.error(f"Error selecting saved channel: {e}")
        await callback.answer("❌ Ошибка выбора канала", show_alert=True)


@transfer_router.callback_query(lambda c: c.data == "transfer_add_new_max", StateFilter(TransferStates.transfer_select_saved_max))
async def add_new_max_channel(callback: CallbackQuery, state) -> None:
    """
    User wants to add a new Max channel (not using saved one).
    
    Args:
        callback: Callback query
        state: FSM state
    """
    state_data = await state.get_data()
    channel_title = state_data.get("transfer_tg_channel_title", "Канал")
    
    text = (
        f"✅ Канал <b>{channel_title}</b> подтвержден!\n\n"
        f"Теперь подключите канал в MAX.\n\n"
        f"<b>Инструкция:</b>\n"
        f"1. Откройте <b>Настройки канала ➡ Подписчики</b>\n"
        f"2. Добавьте подписчика «Репост» ({MAX_BOT_USERNAME})\n"
        f"3. Перейдите в <b>Настройки канала ➡ Администраторы</b>\n"
        f"4. Добавьте администратора «Репост» ({MAX_BOT_USERNAME})\n"
        f"5. Включите <b>«Писать посты»</b> и сохраните\n\n"
        f"➡ <b>Вернитесь сюда и отправьте ссылку на канал в MAX</b>\n"
        f"<i>https://max.me/username или ID канала</i>\n\n"
        f"⚠️ Если Max не находит бота по нику — попробуйте найти по названию «Репост»"
    )
    
    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.answer()
    await state.set_state(TransferStates.transfer_waiting_max_channel)


@transfer_router.callback_query(lambda c: c.data.startswith("transfer_delete_saved_max:"), StateFilter(TransferStates.transfer_select_saved_max))
async def delete_saved_max_channel_prompt(callback: CallbackQuery, state) -> None:
    """
    Show confirmation for deleting a saved Max channel binding.
    
    Args:
        callback: Callback query
        state: FSM state
    """
    binding_id = int(callback.data.split(":")[1])
    
    text = (
        "🗑 <b>Удаление канала</b>\n\n"
        "Вы уверены, что хотите удалить этот канал из сохранённых?\n\n"
        "Это не удалит канал в Max, только уберёт из списка быстрого доступа."
    )
    
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=confirm_delete_binding_keyboard(binding_id),
    )
    await callback.answer()
    await state.set_state(TransferStates.transfer_select_saved_max)


@transfer_router.callback_query(lambda c: c.data.startswith("transfer_confirm_delete_binding:"), StateFilter(TransferStates.transfer_select_saved_max))
async def confirm_delete_binding(callback: CallbackQuery, state, db_session) -> None:
    """
    Confirm deletion of a saved Max channel binding.
    
    Args:
        callback: Callback query
        state: FSM state
        db_session: Database session
    """
    binding_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    
    try:
        binding_repo = MaxChannelBindingRepository(db_session)
        deleted = await binding_repo.delete_binding(user_id, binding_id)
        
        if deleted:
            await callback.answer("✅ Канал удалён", show_alert=True)
            logger.info(f"User {user_id} deleted binding {binding_id}")
        else:
            await callback.answer("❌ Канал не найден", show_alert=True)
            
        # Refresh the list
        state_data = await state.get_data()
        channel_title = state_data.get("transfer_tg_channel_title", "Канал")
        await _show_max_connection_instructions(callback.message, state, channel_title, db_session)
        
    except Exception as e:
        logger.error(f"Error deleting binding: {e}")
        await callback.answer("❌ Ошибка удаления", show_alert=True)


@transfer_router.callback_query(lambda c: c.data == "transfer_cancel_delete_binding", StateFilter(TransferStates.transfer_select_saved_max))
async def cancel_delete_binding(callback: CallbackQuery, state, db_session) -> None:
    """
    Cancel deletion and return to saved channels list.
    
    Args:
        callback: Callback query
        state: FSM state
        db_session: Database session
    """
    state_data = await state.get_data()
    channel_title = state_data.get("transfer_tg_channel_title", "Канал")
    
    await callback.answer("Отменено")
    await _show_max_connection_instructions(callback.message, state, channel_title, db_session)


# =============================================================================
# Transfer Execution
# =============================================================================


async def _execute_transfer(
    message: Message,
    state,
    count: int | str,
    is_callback: bool = False,
) -> None:
    """
    Execute the actual transfer process.

    Args:
        message: Message object (from callback or message handler)
        state: FSM state
        count: Number of posts to transfer or "all"
        is_callback: Whether message comes from callback query
    """
    data = await state.get_data()
    tg_channel = data.get("transfer_tg_channel_username", "")
    max_channel_id = data.get("transfer_max_channel_id", "")
    
    # Ensure max_channel_id is int (Max API requires numeric chat_id)
    if isinstance(max_channel_id, str) and max_channel_id.lstrip('-').isdigit():
        max_channel_id = int(max_channel_id)
    channel_title = data.get("transfer_tg_channel_title", "Канал")

    if not tg_channel or not max_channel_id:
        error_text = "❌ Ошибка: данные канала утеряны. Начните заново."
        if is_callback:
            await message.edit_text(error_text, reply_markup=back_to_start_keyboard())
        else:
            await message.answer(error_text, reply_markup=back_to_start_keyboard())
        await state.clear()
        return

    # Show initial progress message
    count_text = "Все посты" if count == "all" else f"{count} постов"
    progress_message = await (message.answer if not is_callback else message.edit_text)(
        f"⏳ Запускаю перенос...\n\n"
        f"Канал: {channel_title}\n"
        f"Выбрано: {count_text}\n\n"
        f"Подготовка...",
        parse_mode="HTML",
    )

    # Create progress callback with throttling
    last_update_time = 0
    UPDATE_INTERVAL = 3.0  # Minimum seconds between updates

    async def progress_callback(current: int, total: int, success: int, failed: int, skipped: int) -> None:
        nonlocal last_update_time
        current_time = time.time()

        # Throttle updates to avoid FloodWait
        if current_time - last_update_time < UPDATE_INTERVAL:
            return

        last_update_time = current_time
        percent = int((current / total) * 100) if total > 0 else 0

        try:
            if is_callback:
                await progress_message.edit_text(
                    f"⏳ <b>Перенос в процессе</b>\n\n"
                    f"Канал: {channel_title}\n"
                    f"Прогресс: {current}/{total} ({percent}%)\n\n"
                    f"✅ Успешно: {success}\n"
                    f"❌ Ошибок: {failed}\n"
                    f"⏭ Пропущено: {skipped}",
                    parse_mode="HTML",
                )
            else:
                # For message handlers, we need to send new messages
                # (editing is more complex due to message_id differences)
                await progress_message.edit_text(
                    f"⏳ <b>Перенос в процессе</b>\n\n"
                    f"Канал: {channel_title}\n"
                    f"Прогресс: {current}/{total} ({percent}%)\n\n"
                    f"✅ Успешно: {success}\n"
                    f"❌ Ошибок: {failed}\n"
                    f"⏭ Пропущено: {skipped}",
                    parse_mode="HTML",
                )
        except Exception as e:
            logger.warning(f"Failed to update progress message: {e}")

    # Execute transfer
    try:
        # Get clients
        telethon = get_telethon_client(
            api_id=settings.telegram_api_id,
            api_hash=settings.telegram_api_hash,
            phone=settings.telegram_phone,
        )
        max_client = MaxClient()

        # Create engine
        engine = TransferEngine(
            telethon_client=telethon,
            max_api_client=max_client,
            db_session=None,  # No DB session needed for one-time transfer
        )

        # Run transfer
        result: TransferResult = await engine.transfer_posts(
            tg_channel=tg_channel,
            max_channel_id=max_channel_id,
            count=count,
            progress_callback=progress_callback,
        )

        # Close clients
        await max_client.close()

        # Show final result
        result_text = (
            f"✅ <b>Перенос завершён!</b>\n\n"
            f"Канал: {channel_title}\n\n"
            f"📊 <b>Статистика:</b>\n"
            f"Всего обработано: {result.total}\n"
            f"✅ Успешно: {result.success}\n"
            f"❌ Ошибок: {result.failed}\n"
            f"⏭ Пропущено: {result.skipped}"
        )

        if result.errors:
            error_summary = "\n\n".join(
                f"• Пост {e.post_id}: {_strip_html(e.error_message, 100)}"
                for e in result.errors[:3]  # Show first 3 errors
            )
            if len(result.errors) > 3:
                error_summary += f"\n• и ещё {len(result.errors) - 3} ошибок"
            result_text += f"\n\n❌ <b>Ошибки:</b>\n{error_summary}"

        if is_callback:
            await progress_message.edit_text(
                result_text,
                parse_mode="HTML",
                reply_markup=transfer_complete_keyboard(),
            )
        else:
            await progress_message.edit_text(
                result_text,
                parse_mode="HTML",
                reply_markup=transfer_complete_keyboard(),
            )

        logger.info(
            f"Transfer completed: {result.success} success, "
            f"{result.failed} failed, {result.skipped} skipped"
        )

    except MaxAPIError as e:
        logger.error(f"Max API error during transfer: {e}")
        # Clean error message - remove HTML tags
        clean_error = _strip_html(str(e))
        error_text = (
            f"❌ <b>Ошибка переноса</b>\n\n"
            f"Канал: {channel_title}\n\n"
            f"Ошибка: {clean_error}\n\n"
            f"Проверьте:\n"
            f"• Бот «Репост» добавлен в канал Max\n"
            f"• Боту выданы права «Писать посты»"
        )
        if is_callback:
            await progress_message.edit_text(error_text, parse_mode="HTML", reply_markup=back_keyboard())
        else:
            await progress_message.edit_text(error_text, parse_mode="HTML", reply_markup=back_keyboard())

    except RuntimeError as e:
        logger.error(f"Runtime error during transfer: {e}")
        error_text = (
            f"❌ <b>Ошибка переноса</b>\n\n"
            f"{str(e)}\n\n"
            f"Убедитесь, что:\n"
            f"• Вы авторизовали Telethon (python scripts/auth_telethon.py)\n"
            f"• Файл user_session.session существует"
        )
        if is_callback:
            await progress_message.edit_text(error_text, parse_mode="HTML", reply_markup=back_keyboard())
        else:
            await progress_message.edit_text(error_text, parse_mode="HTML", reply_markup=back_keyboard())

    except Exception as e:
        logger.error(f"Unexpected error during transfer: {e}")
        error_text = (
            f"❌ <b>Ошибка переноса</b>\n\n"
            f"Произошла непредвиденная ошибка.\n"
            f"Попробуйте позже или обратитесь в поддержку."
        )
        if is_callback:
            await progress_message.edit_text(error_text, parse_mode="HTML", reply_markup=back_keyboard())
        else:
            await progress_message.edit_text(error_text, parse_mode="HTML", reply_markup=back_keyboard())

    finally:
        await state.clear()


# =============================================================================
# Max Channel Handler
# =============================================================================


def _build_manual_chat_id_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard for manual chat_id entry."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📝 Ввести chat_id вручную", callback_data="transfer_enter_chat_id")
    builder.button(text="↩️ Назад", callback_data="nav_goto_menu")
    builder.adjust(1)
    return builder.as_markup()


@transfer_router.callback_query(lambda c: c.data == "transfer_enter_chat_id", StateFilter(TransferStates.transfer_waiting_max_channel, TransferStates.transfer_detect_max_channel))
async def prompt_manual_chat_id(callback: CallbackQuery, state) -> None:
    """
    Prompt user to enter chat_id manually when /chats is empty.
    
    Args:
        callback: Callback query
        state: FSM state
    """
    await callback.message.edit_text(
        "<b>📝 Ввод chat_id вручную</b>\n\n"
        "Max API не возвращает каналы в списке чатов.\n"
        "Введите числовой ID канала (отрицательное число).\n\n"
        "<b>Как получить chat_id:</b>\n"
        "1. Запустите: <code>python scripts/listen_updates.py</code>\n"
        "2. Удалите и добавьте бота в канал Max\n"
        "3. Скопируйте числовой ID из события\n\n"
        "<b>Пример:</b> <code>-70977371223467</code>",
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )
    await callback.answer()
    await state.set_state(TransferStates.transfer_enter_max_chat_id)


@transfer_router.message(StateFilter(TransferStates.transfer_enter_max_chat_id))
async def process_manual_chat_id(message: Message, state, db_session) -> None:
    """
    Process manually entered Max chat_id.
    
    Args:
        message: User message with chat_id
        state: FSM state
        db_session: Database session
    """
    # Delete user message
    await _delete_user_message(message)
    
    text = message.text.strip()
    
    # Validate: should be a negative integer (Max channel IDs are negative)
    try:
        chat_id = int(text)
        if chat_id >= 0:
            await _edit_or_send_message(
                message, state,
                "❌ <b>Некорректный chat_id</b>\n\n"
                "ID канала в Max должен быть <b>отрицательным числом</b>.\n"
                "Пример: <code>-70977371223467</code>\n\n"
                "Попробуйте снова:",
                reply_markup=back_keyboard(),
            )
            return
    except ValueError:
        await _edit_or_send_message(
            message, state,
            "❌ <b>Некорректный формат</b>\n\n"
            "Введите числовой ID (только цифры со знаком минус).\n"
            "Пример: <code>-70977371223467</code>\n\n"
            "Попробуйте снова:",
            reply_markup=back_keyboard(),
        )
        return
    
    # Store the chat_id
    await state.update_data(transfer_max_channel_id=chat_id)
    logger.info(f"Manually entered chat_id: {chat_id}")
    
    # Proceed to count posts (save binding for future use)
    await _continue_after_max_channel_set(message, state, db_session)


async def _save_max_channel_binding(state, db_session=None) -> None:
    """
    Save Max channel binding to database for future use.
    
    Args:
        state: FSM state with channel info
        db_session: Optional database session
    """
    if not db_session:
        return
    
    try:
        data = await state.get_data()
        user_id = data.get("user_id")
        tg_channel = data.get("transfer_tg_channel_username")
        tg_channel_id = data.get("transfer_tg_channel_id")
        max_channel_id = data.get("transfer_max_channel_id")
        
        if not all([user_id, tg_channel, tg_channel_id, max_channel_id]):
            logger.warning("Cannot save binding: missing required data")
            return
        
        binding_repo = MaxChannelBindingRepository(db_session)
        await binding_repo.create_or_update(
            user_id=user_id,
            tg_channel=tg_channel,
            tg_channel_id=str(tg_channel_id),
            max_chat_id=str(max_channel_id),
            max_channel_name=None,  # Could be fetched from Max API if needed
        )
        logger.info(f"Saved Max channel binding: user={user_id}, tg={tg_channel}, max={max_channel_id}")
        
    except Exception as e:
        logger.error(f"Failed to save Max channel binding: {e}")


async def _continue_after_max_channel_set(target_message: Message | CallbackQuery, state, db_session=None) -> None:
    """Continue flow after max_channel_id is set (either from API or manual entry)."""
    # Save the binding for future use
    await _save_max_channel_binding(state, db_session)
    
    # Get channel info
    data = await state.get_data()
    channel_title = data.get("transfer_tg_channel_title", "Канал")
    channel_username = data.get("transfer_tg_channel_username", "")
    max_channel_id = data.get("transfer_max_channel_id")

    # Count posts using Telethon
    try:
        telethon = get_telethon_client(
            api_id=settings.telegram_api_id,
            api_hash=settings.telegram_api_hash,
            phone=settings.telegram_phone,
        )
        post_count = await telethon.count_channel_posts(channel_username)
    except Exception as e:
        logger.error(f"Error counting posts: {e}")
        await _edit_or_send_message(
            target_message, state,
            "❌ Не удалось подсчитать посты в канале.\n\n"
            f"Ошибка: {str(e)}\n\n"
            "Убедитесь, что канал публичный и бот имеет к нему доступ.",
            reply_markup=back_keyboard(),
        )
        await state.clear()
        return

    total_price = post_count * PRICE_PER_POST

    await _edit_or_send_message(
        target_message, state,
        f"<b>🚀 Мастер переноса</b>\n"
        f"Канал: {channel_title}\n"
        f"Постов: ~{post_count}\n\n"
        f"💰 Тариф: {PRICE_PER_POST} руб./пост\n"
        f"Итого за все: {total_price} руб.\n\n"
        f"🔢 Сколько постов перенести?",
        reply_markup=select_count_keyboard(post_count),
    )

    await state.set_state(TransferStates.transfer_select_count)


def _parse_max_channel_link(text: str) -> str | None:
    """
    Parse Max channel link or ID from user input.
    
    Supports formats:
    - https://max.me/username
    - https://max.me/join/...
    - https://max.ru/username
    - https://max.ru/join/...
    - @username
    - -123456789 (numeric chat_id)
    
    Args:
        text: User input text
        
    Returns:
        Parsed identifier or None if invalid
    """
    text = text.strip()
    
    # Try to parse as numeric ID first
    if text.lstrip('-').isdigit():
        return text
    
    # Handle URLs
    if text.startswith("http://") or text.startswith("https://"):
        # Remove protocol and split
        url_part = text.split("://", 1)[-1]
        parts = url_part.split("/")
        
        # Check for max domains
        if parts[0] in ("max.me", "max.ru", "www.max.me", "www.max.ru"):
            # Get the last non-empty part
            for part in reversed(parts[1:]):
                if part:
                    return part
            return None
    
    # Handle @username
    if text.startswith("@"):
        return text[1:]
    
    # Return as-is (might be a username or ID)
    return text


@transfer_router.message(StateFilter(TransferStates.transfer_waiting_max_channel))
async def process_transfer_max_channel(message: Message, state, db_session) -> None:
    """
    Process Max channel link for transfer.

    Args:
        message: User message with Max channel link/ID
        state: FSM state
        db_session: Database session
    """
    # Delete user message
    await _delete_user_message(message)
    
    text = message.text.strip()
    max_channel_identifier = _parse_max_channel_link(text)

    if not max_channel_identifier:
        await _edit_or_send_message(
            message, state,
            "❌ Не удалось распознать ссылку на канал MAX.\n\n"
            "Отправьте:\n"
            "• Ссылку на канал: <i>https://max.me/username</i> или <i>https://max.ru/join/...</i>\n"
            "• Или числовой ID: <i>-123456789</i>",
            reply_markup=back_keyboard(),
        )
        return

    # Check if it's a numeric chat_id (negative number for channels)
    if max_channel_identifier.lstrip('-').isdigit():
        # Direct numeric ID - use it
        max_channel_id = int(max_channel_identifier)
        logger.info(f"Using numeric Max chat_id: {max_channel_id}")
        
        # Store and continue
        await state.update_data(transfer_max_channel_id=max_channel_id)
        await _continue_after_max_channel_set(message, state, db_session)
        return

    # Try to find channel via API
    max_channel_id = None
    try:
        async with MaxClient() as client:
            chats = await client.get_chats()
            logger.info(f"Available Max chats: {chats}")

            # Check if bot has access to any chats
            if not chats:
                # Max API doesn't return channels in /chats - show auto-detect options
                await _edit_or_send_message(
                    message, state,
                    "🔍 <b>Определяю ID канала...</b>\n\n"
                    "Убедитесь что бот добавлен в канал Max как администратор "
                    "с правом <b>'Писать посты'</b>.",
                    reply_markup=detect_channel_keyboard(),
                )
                await state.set_state(TransferStates.transfer_detect_max_channel)
                return

            # Find channel by name/username matching the identifier from link
            identifier_lower = max_channel_identifier.lower()
            for chat in chats:
                chat_name_lower = (chat.name or "").lower()
                chat_username_lower = (chat.username or "").lower()

                if (identifier_lower in chat_name_lower or
                    chat_name_lower in identifier_lower or
                    identifier_lower == chat_username_lower):
                    max_channel_id = chat.id
                    logger.info(f"Found matching channel: {chat.name} (id={chat.id})")
                    break

            # If no match found, use the first available channel (fallback)
            if not max_channel_id and chats:
                first_chat = chats[0]
                max_channel_id = first_chat.id
                logger.info(f"No exact match found, using first available channel: {first_chat.name} (id={first_chat.id})")

    except MaxAPIError as e:
        logger.error(f"Max API error: {e}")
        await _edit_or_send_message(
            message, state,
            "❌ Ошибка подключения к Max API.\n\n"
            "Убедитесь, что:\n"
            f"• Бот «Репост» ({MAX_BOT_USERNAME}) добавлен в канал\n"
            "• Боту выданы права «Писать посты»\n\n"
            "Попробуйте снова:",
            reply_markup=back_keyboard(),
        )
        return

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        await _edit_or_send_message(
            message, state,
            "❌ Произошла ошибка. Попробуйте позже.",
            reply_markup=back_keyboard(),
        )
        return

    if not max_channel_id:
        await _edit_or_send_message(
            message, state,
            "❌ <b>Не удалось определить канал</b>\n\n"
            "Попробуйте ввести chat_id вручную:",
            reply_markup=_build_manual_chat_id_keyboard(),
        )
        return

    # Store numeric Max channel ID
    await state.update_data(transfer_max_channel_id=max_channel_id)
    
    # Continue to post counting (save binding for future use)
    await _continue_after_max_channel_set(message, state, db_session)


@transfer_router.callback_query(StateFilter(TransferStates.transfer_select_count))
async def process_post_count_selection(callback: CallbackQuery, state) -> None:
    """
    Process user's selection of post count and start transfer.

    Args:
        callback: Callback query with selection
        state: FSM state
    """
    if callback.data == "transfer_cancel":
        await state.clear()
        await callback.message.edit_text(
            "❌ Перенос отменен.",
            reply_markup=back_to_start_keyboard(),
        )
        await callback.answer()
        return

    if callback.data == "transfer_count_custom":
        await callback.answer()
        await callback.message.edit_text(
            "🔢 Введите количество постов для переноса:",
            reply_markup=back_keyboard(),
        )
        await state.set_state(TransferStates.transfer_select_count)
        return

    # Determine count
    if callback.data == "transfer_count_all":
        count = "all"
    elif callback.data == "transfer_count_100":
        count = 100
    elif callback.data == "transfer_count_50":
        count = 50
    else:
        await callback.answer("Неизвестный выбор", show_alert=True)
        return

    await callback.answer()
    await _execute_transfer(callback.message, state, count, is_callback=True)


# =============================================================================
# Custom count input
# =============================================================================


@transfer_router.message(StateFilter(TransferStates.transfer_select_count))
async def process_custom_post_count(message: Message, state) -> None:
    """
    Process custom post count input and start transfer.

    Args:
        message: User message with number
        state: FSM state
    """
    # Delete user message
    await _delete_user_message(message)
    
    text = message.text.strip()

    try:
        count = int(text)
        if count <= 0:
            raise ValueError()
    except ValueError:
        await _edit_or_send_message(
            message, state,
            "❌ Введите корректное число (больше 0).",
            reply_markup=back_keyboard(),
        )
        return

    await _execute_transfer(message, state, count, is_callback=False)
