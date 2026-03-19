"""Post transfer handler - FSM flow for manual post transfer from TG to Max."""

import asyncio
import re
import time
from typing import Optional

from aiogram import Router
from aiogram.filters import StateFilter
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
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
    verified_channels_keyboard,
)
from bot.core.verification import generate_verification_code, verify_channel_ownership
from bot.max_api.client import MaxClient, MaxAPIError
from bot.core.telethon_client import get_telethon_client
from bot.core.transfer_engine import TransferEngine, TransferResult
from bot.database.repositories.max_channel_binding import MaxChannelBindingRepository
from bot.database.repositories.verified_channel import VerifiedChannelRepository
from config.settings import settings


# =============================================================================
# Concurrency Control
# =============================================================================

# Track active user transfers (Level 1: per-user limit)
_active_transfers: set[int] = set()  # user_ids currently transferring

# Global semaphore for max concurrent transfers (Level 2: global limit)
_transfer_semaphore = asyncio.Semaphore(3)

# Track active transfer engines for cancellation
_active_engines: dict[int, TransferEngine] = {}  # user_id -> TransferEngine

# Maximum posts per transfer limit
MAX_TRANSFER_POSTS = 500


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

    Shows description and asks for TG channel link.

    Args:
        callback: Callback query
        state: FSM state
    """
    # Answer callback FIRST before any async operations
    await callback.answer()
    
    # Store user_id and chat_id in state for later use
    await state.update_data(
        user_id=callback.from_user.id,
        chat_id=callback.message.chat.id,
        bot_message_id=callback.message.message_id,
    )
    
    await callback.message.edit_text(
        "<b>📥 Перенос контента из Telegram в Max</b>\n\n"
        "Перенесу все ваши посты из Telegram-канала в канал Max с сохранением:\n"
        "✅ Текста и форматирования\n"
        "✅ Фото и видео\n"
        "✅ Аудио и документов\n"
        "✅ Ссылок\n\n"
        "<b>Отправьте ссылку на ваш Telegram-канал:</b>\n"
        "Например: <code>@channelname</code> или <code>https://t.me/channelname</code>",
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )

    # Set FSM state
    await state.set_state(TransferStates.transfer_waiting_tg_channel)


@transfer_router.callback_query(lambda c: c.data == "menu_my_channels")
async def show_my_verified_channels(
    callback: CallbackQuery,
    verified_channel_repo: VerifiedChannelRepository,
) -> None:
    """
    Show list of user's verified channels.
    
    Allows quick selection of previously verified channels,
    skipping the verification step. Also allows deleting channels.
    
    Args:
        callback: Callback query
        verified_channel_repo: Repository for verified channels
    """
    # Answer callback FIRST before any async operations
    await callback.answer()
    
    user_id = callback.from_user.id
    
    try:
        # Get user's verified channels
        channels = await verified_channel_repo.get_user_verified_channels(user_id)
        
        if channels:
            text = (
                "<b>📢 Мои каналы</b>\n\n"
                "Здесь хранятся ваши верифицированные Telegram-каналы. "
                "Выберите канал для быстрого переноса (без повторной верификации):\n"
            )
            # Build keyboard manually with 2 columns (channel + delete button)
            builder = InlineKeyboardBuilder()
            
            for channel in channels:
                display_name = channel.tg_channel[:30] if len(channel.tg_channel) <= 30 else channel.tg_channel[:27] + "..."
                # Channel button (column 1)
                builder.button(
                    text=f"📢 @{display_name}",
                    callback_data=f"select_verified_channel:{channel.tg_channel}",
                )
                # Delete button (column 2)
                builder.button(
                    text="🗑",
                    callback_data=f"delete_verified_channel:{channel.tg_channel}",
                )
            
            # Add new channel button
            builder.button(text="➕ Добавить новый канал", callback_data="start_setup_transfer")
            builder.button(text="↩️ Назад", callback_data="nav_goto_menu")
            builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
            
            # Adjust layout: 2 columns for channels+delete, then 1 column for new channel, then 2 columns for back+menu
            builder.adjust(2, repeat=True)
            keyboard = builder.as_markup()
        else:
            text = (
                "<b>📢 Мои каналы</b>\n\n"
                "У вас пока нет верифицированных каналов.\n\n"
                "Нажмите «Настроить перенос» чтобы добавить первый канал."
            )
            builder = InlineKeyboardBuilder()
            builder.button(text="➕ Добавить канал", callback_data="start_setup_transfer")
            builder.button(text="↩️ Назад", callback_data="nav_goto_menu")
            builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
            builder.adjust(1)
            keyboard = builder.as_markup()
        
        await callback.message.edit_text(text, reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Error loading verified channels for user {user_id}: {e}")
        await callback.answer("❌ Ошибка загрузки каналов", show_alert=True)


@transfer_router.callback_query(lambda c: c.data.startswith("delete_verified_channel:"))
async def delete_verified_channel_prompt(
    callback: CallbackQuery,
    state,
    verified_channel_repo: VerifiedChannelRepository,
) -> None:
    """
    Show confirmation for deleting a verified channel.
    
    Args:
        callback: Callback query
        state: FSM state
        verified_channel_repo: Repository for verified channels
    """
    # Answer callback FIRST before any async operations
    await callback.answer()
    
    # Extract channel from callback_data
    tg_channel = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    
    try:
        # Verify the channel exists for this user
        channel = await verified_channel_repo.get_verified_channel(user_id, tg_channel)
        if not channel:
            await callback.answer("❌ Канал не найден", show_alert=True)
            return
        
        # Store channel in state for confirmation handler
        await state.update_data(channel_to_delete=tg_channel)
        
        text = (
            "🗑 <b>Удаление канала</b>\n\n"
            f"Вы уверены, что хотите удалить канал <b>@{tg_channel}</b> из списка?\n\n"
            "⚠️ Это также удалит все сохранённые привязки к каналам Max для этого канала.\n\n"
            "Действие нельзя отменить."
        )
        
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Да, удалить", callback_data="confirm_delete_verified_channel")
        builder.button(text="❌ Отмена", callback_data="cancel_delete_verified_channel")
        builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
        builder.adjust(2, 1)
        
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
        
    except Exception as e:
        logger.error(f"Error preparing delete for channel {tg_channel}: {e}")
        await callback.answer("❌ Ошибка", show_alert=True)


@transfer_router.callback_query(lambda c: c.data == "confirm_delete_verified_channel")
async def confirm_delete_verified_channel(
    callback: CallbackQuery,
    state,
    db_session,
    verified_channel_repo: VerifiedChannelRepository,
) -> None:
    """
    Confirm deletion of a verified channel.
    
    Deletes the verified channel record and all related Max channel bindings.
    
    Args:
        callback: Callback query
        state: FSM state
        db_session: Database session
        verified_channel_repo: Repository for verified channels
    """
    # Answer callback FIRST before any async operations
    await callback.answer()
    
    user_id = callback.from_user.id
    
    # Get channel from state
    data = await state.get_data()
    tg_channel = data.get("channel_to_delete")
    
    if not tg_channel:
        await callback.answer("❌ Ошибка: данные утеряны", show_alert=True)
        return
    
    try:
        # Delete related Max channel bindings first
        binding_repo = MaxChannelBindingRepository(db_session)
        bindings = await binding_repo.get_by_user_and_tg_channel(user_id, tg_channel)
        for binding in bindings:
            try:
                await binding_repo.delete_binding(user_id, binding.id)
                logger.info(f"Deleted binding {binding.id} for user {user_id}, channel {tg_channel}")
            except Exception as e:
                logger.warning(f"Failed to delete binding {binding.id}: {e}")
        
        # Delete the verified channel
        deleted = await verified_channel_repo.delete_verification(user_id, tg_channel)
        
        if deleted:
            await callback.answer("✅ Канал удалён", show_alert=True)
            logger.info(f"User {user_id} deleted verified channel {tg_channel}")
        else:
            await callback.answer("❌ Канал не найден", show_alert=True)
        
        # Clear the channel_to_delete from state
        await state.update_data(channel_to_delete=None)
        
        # Refresh the channel list
        await show_my_verified_channels(callback, verified_channel_repo)
        
    except Exception as e:
        logger.error(f"Error deleting verified channel {tg_channel}: {e}")
        await callback.answer("❌ Ошибка удаления", show_alert=True)


@transfer_router.callback_query(lambda c: c.data == "cancel_delete_verified_channel")
async def cancel_delete_verified_channel(
    callback: CallbackQuery,
    state,
    verified_channel_repo: VerifiedChannelRepository,
) -> None:
    """
    Cancel deletion and return to channel list.
    
    Args:
        callback: Callback query
        state: FSM state
        verified_channel_repo: Repository for verified channels
    """
    # Answer callback FIRST before any async operations
    await callback.answer("Отменено")
    
    # Clear the channel_to_delete from state
    await state.update_data(channel_to_delete=None)
    
    # Return to channel list
    await show_my_verified_channels(callback, verified_channel_repo)


@transfer_router.callback_query(lambda c: c.data.startswith("select_verified_channel:"))
async def select_verified_channel(
    callback: CallbackQuery,
    state,
    bot,
    db_session,
    verified_channel_repo: VerifiedChannelRepository,
) -> None:
    """
    Select a verified channel and proceed to Max channel selection.
    
    Skips TG channel input and verification since channel is already verified.
    Works with both public and private channels.
    
    Args:
        callback: Callback query
        state: FSM state
        bot: Bot instance
        db_session: Database session
        verified_channel_repo: Repository for verified channels
    """
    # Answer callback FIRST before any async operations
    await callback.answer()
    
    # Extract channel identifier from callback data
    tg_channel = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    
    try:
        # Get verified channel record to get tg_channel_id (for private channels)
        verified_channel = await verified_channel_repo.get_verified_channel(user_id, tg_channel)
        tg_channel_id = verified_channel.tg_channel_id if verified_channel else None
        
        # Try to get channel info using Telethon (supports both public and private)
        chat_title = None
        chat_id = None
        
        try:
            telethon = get_telethon_client(
                api_id=settings.telegram_api_id,
                api_hash=settings.telegram_api_hash,
                phone=settings.telegram_phone,
            )
            client = await telethon._get_client()
            
            # Try by channel_id first (for private channels), then by username
            if tg_channel_id:
                try:
                    entity = await client.get_entity(int(tg_channel_id))
                    chat_title = entity.title
                    chat_id = tg_channel_id
                except Exception:
                    pass
            
            # Fallback to username/public identifier
            if not chat_title:
                entity = await client.get_entity(tg_channel)
                chat_title = entity.title
                chat_id = str(entity.id)
                
        except Exception as e:
            logger.warning(f"Telethon failed to get channel info, trying Bot API: {e}")
            # Fallback to Bot API (only works for public channels)
            try:
                chat = await bot.get_chat(f"@{tg_channel}")
                chat_title = chat.title
                chat_id = str(chat.id)
            except Exception as e2:
                logger.error(f"Both Telethon and Bot API failed: {e2}")
                raise
        
        # Store in state
        await state.update_data(
            user_id=user_id,
            chat_id=callback.message.chat.id,
            bot_message_id=callback.message.message_id,
            transfer_tg_channel_id=chat_id,
            transfer_tg_channel_title=chat_title,
            transfer_tg_channel_username=tg_channel,
        )
        
        # Check if channel is still verified (should be, but double-check)
        is_verified = await verified_channel_repo.is_channel_verified(user_id, tg_channel)
        if not is_verified:
            # Should not happen, but handle gracefully
            await callback.answer("⚠️ Требуется повторная верификация", show_alert=True)
            await _show_verification_code(callback.message, state, chat_title, db_session, verified_channel_repo)
            return
        
        # Proceed directly to Max channel selection (skip verification)
        await _show_max_connection_instructions(callback.message, state, chat_title, db_session, user_repo=None)
        
    except Exception as e:
        logger.error(f"Error selecting verified channel {tg_channel}: {e}")
        await callback.answer("❌ Ошибка выбора канала. Убедитесь что вы всё ещё подписаны.", show_alert=True)


@transfer_router.message(StateFilter(TransferStates.transfer_waiting_tg_channel))
async def process_transfer_tg_channel(
    message: Message,
    state,
    bot,
    db_session,
    verified_channel_repo: VerifiedChannelRepository,
) -> None:
    """
    Process Telegram channel link for transfer.

    Similar to autopost flow - validates and gets channel info.

    Args:
        message: User message with channel link
        state: FSM state
        bot: Bot instance for API calls
        db_session: Database session
        verified_channel_repo: Repository for verified channels
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
            message,
            text="❌ Не удалось распознать ссылку на канал.\n\n"
            "Отправьте ссылку в формате:\n"
            "<i>https://t.me/channelname</i> или <i>@channelname</i>",
            state=state,
        )
        return

    # Store channel username in state
    await state.update_data(transfer_tg_channel_username=channel_username)

    # Try to get chat info (Bot API first, then Telethon fallback)
    chat = None
    chat_id = None
    chat_title = None
    
    # Step 1: Try Bot API
    try:
        chat = await bot.get_chat(f"@{channel_username}")
        chat_id = str(chat.id)
        chat_title = chat.title
        logger.info(f"Channel @{channel_username} found via Bot API: {chat_title}")
    except Exception as e:
        logger.warning(f"Bot API failed to get chat info for @{channel_username}: {e}")
    
    # Step 2: If Bot API failed, try Telethon (for public channels)
    if chat is None:
        try:
            telethon = get_telethon_client(
                api_id=settings.telegram_api_id,
                api_hash=settings.telegram_api_hash,
                phone=settings.telegram_phone,
            )
            # Get entity via Telethon
            from telethon.tl.functions.channels import GetFullChannelRequest
            entity = await telethon._client.get_entity(channel_username)
            full = await telethon._client(GetFullChannelRequest(entity))
            chat_id = str(entity.id)
            chat_title = entity.title
            logger.info(f"Channel @{channel_username} found via Telethon: {chat_title}")
        except Exception as e:
            logger.error(f"Telethon also failed to get chat info for @{channel_username}: {e}")
    
    # Step 3: If both failed, show error
    if chat_id is None:
        await _edit_or_send_message(
            message,
            text="❌ Канал не найден.\n\n"
            "Возможные причины:\n"
            "• Канал приватный (бот работает только с публичными)\n"
            "• Ссылка введена с ошибкой\n"
            "• Канал не существует\n\n"
            "Введите ссылку ещё раз:",
            state=state,
            reply_markup=back_keyboard(),
        )
        return

    # Store chat info
    await state.update_data(
        transfer_tg_channel_id=chat_id,
        transfer_tg_channel_title=chat_title,
        transfer_tg_channel_username=channel_username,
    )

    # Check if bot is admin (required for accessing channel history)
    bot_user = await bot.me()
    
    # Convert chat_id to int for Bot API (Telethon returns int ID, Bot API expects int)
    try:
        chat_id_int = int(chat_id)
        member = await bot.get_chat_member(chat_id_int, bot_user.id)
        from aiogram.enums import ChatMemberStatus
        is_bot_admin = member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
    except Exception as e:
        logger.warning(f"Could not check bot membership via Bot API: {e}")
        # If we can't check, assume not admin (safer)
        is_bot_admin = False

    if not is_bot_admin:
        await _edit_or_send_message(
            message,
            text=f"<b>📢 Канал найден: {chat_title}</b>\n\n"
            f"Для доступа к постам бота нужно добавить в администраторы.\n\n"
            f"<b>Инструкция:</b>\n"
            f"1. Откройте настройки канала ➡ Администраторы.\n"
            f"2. Добавьте @{TG_BOT_USERNAME} как администратора.\n"
            f"3. Сохраните изменения и нажмите «Продолжить».",
            state=state,
            reply_markup=_build_continue_keyboard(),
        )
        await state.set_state(TransferStates.transfer_waiting_verification)
    else:
        # Bot is already admin - proceed to ownership verification (check if already verified)
        await _show_verification_code(message, state, chat_title, db_session, verified_channel_repo)


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
    db_session=None,
    verified_channel_repo=None,
) -> None:
    """
    Show verification code for channel ownership confirmation.
    
    If channel is already verified for this user, skip verification
    and proceed directly to Max channel selection.
    
    Args:
        target_message: Message or CallbackQuery to edit
        state: FSM state
        channel_title: Telegram channel title
        db_session: Optional database session
        verified_channel_repo: Optional verified channel repository
    """
    # Check if we should verify or skip
    if db_session and verified_channel_repo:
        data = await state.get_data()
        user_id = data.get("user_id")
        tg_channel = data.get("transfer_tg_channel_username")
        
        if user_id and tg_channel:
            is_verified = await verified_channel_repo.is_channel_verified(user_id, tg_channel)
            if is_verified:
                # Channel already verified - skip to Max selection
                logger.info(f"Channel {tg_channel} already verified for user {user_id}, skipping verification")
                await _edit_or_send_message(
                    target_message,
                    text=f"✅ Канал <b>{channel_title}</b> ранее верифицирован.\n\nПереходим к настройке Max...",
                    state=state,
                )
                await _show_max_connection_instructions(target_message, state, channel_title, db_session)
                return
    
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
        target_message,
        text=text,
        state=state,
        reply_markup=verify_code_keyboard(),
    )
    await state.set_state(TransferStates.transfer_verify_code)


@transfer_router.callback_query(lambda c: c.data == "transfer_verify_admin", StateFilter(TransferStates.transfer_waiting_verification))
async def verify_admin_after_prompt(
    callback: CallbackQuery,
    state,
    bot,
    db_session,
    verified_channel_repo: VerifiedChannelRepository,
) -> None:
    """
    Verify bot is now admin after user added it.

    Args:
        callback: Callback query
        state: FSM state
        bot: Bot instance
        db_session: Database session
        verified_channel_repo: Repository for verified channels
    """
    # Answer callback FIRST with loading indicator before any async operations
    await callback.answer("⏳ Проверяю...")
    
    data = await state.get_data()
    channel_id = data.get("transfer_tg_channel_id")
    channel_title = data.get("transfer_tg_channel_title", "Канал")

    if not channel_id:
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
        await callback.message.edit_text(
            "❌ Ошибка: данные канала утеряны. Начните заново.",
            reply_markup=builder.as_markup(),
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
            # Bot is admin - proceed to ownership verification (check if already verified)
            await _show_verification_code(callback.message, state, channel_title, db_session, verified_channel_repo)
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
    user_repo=None,
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
            f"📋 <b>Сохранённые каналы/чаты Max:</b>\n"
            f"Выберите канал/чат для переноса или добавьте новый:"
        )
        
        keyboard = saved_max_channels_keyboard(saved_bindings, show_delete=True)
        
        await _edit_or_send_message(
            target_message,
            text=text,
            state=state,
            reply_markup=keyboard,
        )
        
        await state.set_state(TransferStates.transfer_select_saved_max)
    else:
        # No saved bindings - show connection instructions
        text = (
            f"✅ Канал <b>{channel_title}</b> подтвержден!\n\n"
            f"Теперь подключите канал/чат в Max.\n\n"
            f"<b>Инструкция:</b>\n"
            f"1. Откройте <b>Настройки канала/чата ➡ Подписчики</b>\n"
            f"2. Добавьте подписчика «Репост» ({MAX_BOT_USERNAME})\n"
            f"3. Перейдите в <b>Настройки канала/чата ➡ Администраторы</b>\n"
            f"4. Добавьте администратора «Репост» ({MAX_BOT_USERNAME})\n"
            f"5. Включите <b>«Писать посты»</b> и сохраните\n\n"
            f"➡ <b>Вернитесь сюда и отправьте ссылку на канал/чат в Max</b>\n"
            f"<i>https://max.me/username, https://max.ru/join/..., или ID канала/чата</i>\n\n"
            f"⚠️ Если Max не находит бота по нику — попробуйте найти по названию «Репост»"
        )

        await _edit_or_send_message(
            target_message,
            text=text,
            state=state,
            reply_markup=back_keyboard(),
        )

        await state.set_state(TransferStates.transfer_waiting_max_channel)


# =============================================================================
# Channel Ownership Verification Handlers
# =============================================================================


@transfer_router.callback_query(lambda c: c.data == "verify_check", StateFilter(TransferStates.transfer_verify_code))
async def check_verification_code(
    callback: CallbackQuery,
    state,
    db_session,
    verified_channel_repo: VerifiedChannelRepository,
) -> None:
    """
    Check if verification code is present in channel description.
    
    If verification succeeds, saves the channel as verified for this user.
    
    Args:
        callback: Callback query
        state: FSM state
        db_session: Database session from middleware
        verified_channel_repo: Repository for verified channels
    """
    # Answer callback FIRST before any async operations
    await callback.answer("⏳ Проверяю код...")
    
    data = await state.get_data()
    code = data.get("verification_code")
    tg_channel = data.get("transfer_tg_channel_username")
    channel_title = data.get("transfer_tg_channel_title", "Канал")
    
    if not code or not tg_channel:
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
        await callback.message.edit_text(
            "❌ Ошибка: данные верификации утеряны. Начните заново.",
            reply_markup=builder.as_markup(),
        )
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
            
            # Save channel as verified for this user
            if verified_channel_repo:
                try:
                    data = await state.get_data()
                    tg_channel_id = data.get("transfer_tg_channel_id")
                    await verified_channel_repo.verify_channel(
                        user_id=callback.from_user.id,
                        tg_channel=tg_channel,
                        tg_channel_id=tg_channel_id,
                    )
                    logger.info(f"Channel {tg_channel} saved as verified for user {callback.from_user.id}")
                except Exception as e:
                    logger.error(f"Failed to save verified channel: {e}")
            
            # Proceed to Max channel selection (pass db_session to load saved bindings)
            await _show_max_connection_instructions(callback.message, state, channel_title, db_session, user_repo=None)
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
    # Answer callback FIRST before any async operations
    await callback.answer("🔄 Генерирую новый код...")
    
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


@transfer_router.callback_query(lambda c: c.data == "verify_back", StateFilter(TransferStates.transfer_verify_code))
async def back_from_verification(callback: CallbackQuery, state) -> None:
    """
    Go back from verification to TG channel input.
    
    Args:
        callback: Callback query
        state: FSM state
    """
    # Answer callback FIRST before any async operations
    await callback.answer()
    
    await callback.message.edit_text(
        "<b>🔗 Пришлите ссылку на ваш Telegram-канал</b>\n\n"
        "Мы выполним перенос постов в 4 этапа:\n"
        "1. <b>Анализ</b>: Посчитаем количество постов.\n"
        "2. <b>Расчёт</b>: Определим стоимость переноса.\n"
        "3. <b>Подключение</b>: Настроим связь с Max.\n"
        "4. <b>Запуск</b>: Начнём перенос контента.\n\n"
        "👉 Отправьте ссылку на ваш публичный Telegram-канал:\n"
        "<i>https://t.me/channelname</i>",
        parse_mode="HTML",
    )
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

    # Answer callback FIRST before any async operations
    await callback.answer("⏳ Определяю ID...")

    await callback.message.edit_text(
        "⏳ <b>Слушаю обновления Max API...</b> (до 60 сек)\n\n"
        "Если бот уже в канале/чате — напишите любое сообщение в канал/чат Max.",
        parse_mode="HTML",
    )

    try:
        # Use asyncio.wait_for to prevent blocking the bot for too long
        async with MaxClient() as client:
            chat_id = await asyncio.wait_for(
                client.find_chat_id(timeout=60),
                timeout=65  # Slightly longer than API timeout
            )

        if chat_id:
            # Found channel/chat - ask for confirmation
            await callback.message.edit_text(
                f"✅ <b>Найден канал/чат!</b>\n\n"
                f"chat_id = <code>{chat_id}</code>\n\n"
                f"Использовать этот канал/чат?",
                parse_mode="HTML",
                reply_markup=confirm_channel_keyboard(chat_id),
            )
        else:
            # No channel/chat found
            await callback.message.edit_text(
                "❌ <b>Не удалось определить ID</b>\n\n"
                "Убедитесь что:\n"
                "1. Бот @id752703975446_1_bot добавлен в канал/чат Max\n"
                "2. Бот имеет права администратора в канале Max\n"
                "3. Вы написали любое сообщение в этот канал ПОСЛЕ добавления бота\n\n"
                "Попробуйте ещё раз:",
                parse_mode="HTML",
                reply_markup=retry_detect_keyboard(),
            )

    except asyncio.TimeoutError:
        logger.warning("Channel detection timed out")
        await callback.message.edit_text(
            "❌ <b>Не удалось определить ID</b>\n\n"
            "Убедитесь что:\n"
            "1. Бот @id752703975446_1_bot добавлен в канал/чат Max\n"
            "2. Бот имеет права администратора в канале Max\n"
            "3. Вы написали любое сообщение в этот канал ПОСЛЕ добавления бота\n\n"
            "Попробуйте ещё раз:",
            parse_mode="HTML",
            reply_markup=retry_detect_keyboard(),
        )

    except MaxAPIError as e:
        logger.error(f"Max API error during chat detection: {e}")
        await callback.message.edit_text(
            "❌ <b>Ошибка подключения к Max</b>\n\n"
            "Не удалось определить канал автоматически.\n\n"
            "Попробуйте ввести chat_id вручную:",
            parse_mode="HTML",
            reply_markup=retry_detect_keyboard(),
        )

    except Exception as e:
        logger.error(f"Unexpected error during chat detection: {e}")
        await callback.message.edit_text(
            "❌ <b>Ошибка при определении канала/чата</b>\n\n"
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
    # Answer callback FIRST before any async operations
    await callback.answer("✅ Канал/чат выбран")
    
    # Extract chat_id from callback data
    chat_id = int(callback.data.split(":")[1])

    # Store the chat_id
    await state.update_data(transfer_max_channel_id=chat_id)
    logger.info(f"Auto-detected chat_id confirmed: {chat_id}")

    # Continue to post counting (save binding for future use)
    await _continue_after_max_channel_set(callback.message, state, db_session, user_repo=None)


@transfer_router.callback_query(lambda c: c.data == "transfer_reject_channel", StateFilter(TransferStates.transfer_detect_max_channel))
async def reject_detected_channel(callback: CallbackQuery, state) -> None:
    """
    Reject the detected channel and try again or enter manually.

    Args:
        callback: Callback query
        state: FSM state
    """
    # Answer callback FIRST before any async operations
    await callback.answer()
    
    await callback.message.edit_text(
        "🔍 <b>Определяю ID канала/чата...</b>\n\n"
        "Убедитесь, что бот добавлен в канал/чат Max как администратор "
        "с правом <b>«Писать посты»</b>.",
        parse_mode="HTML",
        reply_markup=detect_channel_keyboard(),
    )


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
    # Answer callback FIRST before any async operations
    await callback.answer()
    
    # Check if already processing a click (race condition protection)
    state_data = await state.get_data()
    if state_data.get("processing_saved_channel"):
        logger.warning(f"Duplicate click ignored for saved channel selection (binding_id={callback.data.split(':')[1]})")
        return
    
    # Set processing flag
    await state.update_data(processing_saved_channel=True)
    
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
        
        # Update last_used_at (non-critical: wrap in try/except to handle "database is locked")
        try:
            await binding_repo.update_last_used(binding_id)
        except Exception as e:
            logger.warning(f"Failed to update last_used_at for binding {binding_id}: {e}")
        
        # Continue to post counting (don't save binding again for saved channels)
        await _continue_after_max_channel_set(callback.message, state, db_session=None)
        
    except Exception as e:
        logger.error(f"Error selecting saved channel: {e}")
        await callback.answer("❌ Ошибка выбора канала", show_alert=True)
    finally:
        # Reset processing flag
        await state.update_data(processing_saved_channel=False)


@transfer_router.callback_query(lambda c: c.data == "transfer_add_new_max", StateFilter(TransferStates.transfer_select_saved_max))
async def add_new_max_channel(callback: CallbackQuery, state) -> None:
    """
    User wants to add a new Max channel (not using saved one).
    
    Args:
        callback: Callback query
        state: FSM state
    """
    # Answer callback FIRST before any async operations
    await callback.answer()
    
    state_data = await state.get_data()
    channel_title = state_data.get("transfer_tg_channel_title", "Канал")
    
    text = (
        f"✅ Канал <b>{channel_title}</b> подтвержден!\n\n"
        f"Теперь подключите канал/чат в Max.\n\n"
        f"<b>Инструкция:</b>\n"
        f"1. Откройте <b>Настройки канала/чата ➡ Подписчики</b>\n"
        f"2. Добавьте подписчика «Репост» ({MAX_BOT_USERNAME})\n"
        f"3. Перейдите в <b>Настройки канала/чата ➡ Администраторы</b>\n"
        f"4. Добавьте администратора «Репост» ({MAX_BOT_USERNAME})\n"
        f"5. Включите <b>«Писать посты»</b> и сохраните\n\n"
        f"➡ <b>Вернитесь сюда и отправьте ссылку на канал/чат в Max</b>\n"
        f"<i>https://max.me/username или ID канала/чата</i>\n\n"
        f"⚠️ Если Max не находит бота по нику — попробуйте найти по названию «Репост»"
    )
    
    await callback.message.edit_text(text, parse_mode="HTML")
    await state.set_state(TransferStates.transfer_waiting_max_channel)


@transfer_router.callback_query(lambda c: c.data.startswith("transfer_delete_saved_max:"), StateFilter(TransferStates.transfer_select_saved_max))
async def delete_saved_max_channel_prompt(callback: CallbackQuery, state) -> None:
    """
    Show confirmation for deleting a saved Max channel binding.
    
    Args:
        callback: Callback query
        state: FSM state
    """
    # Answer callback FIRST before any async operations
    await callback.answer()
    
    binding_id = int(callback.data.split(":")[1])
    
    text = (
        "🗑 <b>Удаление канала</b>\n\n"
        "Вы уверены, что хотите удалить этот канал/чат из сохранённых?\n\n"
        "Это не удалит канал/чат в Max, только уберёт из списка быстрого доступа."
    )
    
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=confirm_delete_binding_keyboard(binding_id),
    )
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
    # Answer callback FIRST before any async operations
    await callback.answer()
    
    binding_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    
    try:
        binding_repo = MaxChannelBindingRepository(db_session)
        deleted = await binding_repo.delete_binding(user_id, binding_id)
        
        if deleted:
            await callback.answer("✅ Канал/чат удалён", show_alert=True)
            logger.info(f"User {user_id} deleted binding {binding_id}")
        else:
            await callback.answer("❌ Канал/чат не найден", show_alert=True)
            
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
    # Answer callback FIRST before any async operations
    await callback.answer("Отменено")
    
    state_data = await state.get_data()
    channel_title = state_data.get("transfer_tg_channel_title", "Канал")
    
    await _show_max_connection_instructions(callback.message, state, channel_title, db_session)


# =============================================================================
# Transfer Execution
# =============================================================================


async def _execute_transfer(
    message: Message,
    state,
    count: int | str,
    is_callback: bool = False,
    db_session=None,
    user_repo=None,
) -> None:
    """
    Execute the actual transfer process.

    Args:
        message: Message object (from callback or message handler)
        state: FSM state
        count: Number of posts to transfer or "all"
        is_callback: Whether message comes from callback query
        db_session: Optional database session for duplicate tracking
        user_repo: Optional user repository for tracking free posts
    """
    data = await state.get_data()
    user_id = data.get("user_id")
    tg_channel = data.get("transfer_tg_channel_username", "")
    max_channel_id = data.get("transfer_max_channel_id", "")
    
    # Ensure max_channel_id is int (Max API requires numeric chat_id)
    if isinstance(max_channel_id, str) and max_channel_id.lstrip('-').isdigit():
        max_channel_id = int(max_channel_id)
    channel_title = data.get("transfer_tg_channel_title", "Канал")

    if not tg_channel or not max_channel_id:
        error_text = "❌ Ошибка: данные канала утеряны. Начните заново."
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
        await _edit_or_send_message(
            message,
            text=error_text,
            state=state,
            reply_markup=builder.as_markup(),
        )
        await state.clear()
        return

    # Level 1: Check if user already has an active transfer
    if user_id in _active_transfers:
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
        await _edit_or_send_message(
            message,
            text="⏳ Перенос уже выполняется, дождитесь завершения",
            state=state,
            reply_markup=builder.as_markup(),
        )
        return

    # Level 2: Check global limit
    if _transfer_semaphore.locked():
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
        await _edit_or_send_message(
            message,
            text="⏳ Сервер загружен, попробуйте через пару минут",
            state=state,
            reply_markup=builder.as_markup(),
        )
        return

    # Add user to active transfers set
    _active_transfers.add(user_id)

    # Apply 500 posts limit
    original_count = count
    limited_count = count
    if isinstance(count, int) and count > MAX_TRANSFER_POSTS:
        limited_count = MAX_TRANSFER_POSTS
        logger.info(f"Limiting transfer to {MAX_TRANSFER_POSTS} posts (requested {original_count})")
    # Note: "all" case - the limit will be applied based on actual total in the engine
    count = limited_count

    # Show initial progress message with cancel button
    count_text = "Все посты" if original_count == "all" else f"{original_count} постов"
    if isinstance(original_count, int) and original_count > MAX_TRANSFER_POSTS:
        count_text = f"{MAX_TRANSFER_POSTS} из {original_count} постов (лимит)"
    
    # Create cancel keyboard
    cancel_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Остановить перенос", callback_data="transfer_cancel")]
    ])
    
    # Use _edit_or_send_message to avoid stacking messages
    progress_message = await _edit_or_send_message(
        message,
        text=f"🚀 <b>Перенос запущен!</b>\n\n"
             f"📺 Канал: {channel_title}\n"
             f"📝 Выбрано: {count_text}\n\n"
             f"⏳ Подготовка...",
        state=state,
        reply_markup=cancel_keyboard,
    )
    
    # Store progress message ID and engine reference in state for cancel handler
    await state.update_data(transfer_progress_message_id=progress_message.message_id)

    # Create progress callback with progress bar
    posts_since_update = 0
    UPDATE_EVERY = 3  # Update every 3 posts to avoid rate limits
    transfer_start_time = time.time()  # Track actual transfer start time for ETA

    def build_progress_bar(current: int, total: int) -> str:
        """Build a 10-character progress bar."""
        if total == 0:
            return "░░░░░░░░░░"
        filled = round(current / total * 10)
        return "█" * filled + "░" * (10 - filled)

    async def progress_callback(current: int, total: int, success: int, failed: int, skipped: int) -> None:
        nonlocal posts_since_update
        posts_since_update += 1
        
        # Update every N posts
        if posts_since_update < UPDATE_EVERY and current < total:
            return
        posts_since_update = 0
        
        percent = int((current / total) * 100) if total > 0 else 0
        bar = build_progress_bar(current, total)
        
        # Calculate better ETA based on actual elapsed time
        elapsed = time.time() - transfer_start_time
        if current > 0:
            avg_time_per_post = elapsed / current
            remaining_posts = total - current
            eta_seconds = int(avg_time_per_post * remaining_posts)
        else:
            eta_seconds = 0
        
        # Format ETA text
        if eta_seconds > 3600:
            eta_text = f"~{eta_seconds // 3600} ч {(eta_seconds % 3600) // 60} мин осталось"
        elif eta_seconds > 60:
            eta_text = f"~{eta_seconds // 60} мин осталось"
        else:
            eta_text = f"~{eta_seconds} сек осталось"

        try:
            await progress_message.edit_text(
                f"🚀 <b>Перенос в процессе!</b>\n\n"
                f"📺 Канал: {channel_title}\n"
                f"📊 Прогресс: {bar} {percent}%\n"
                f"📤 Перенесено: {current}/{total} ({eta_text})",
                parse_mode="HTML",
                reply_markup=cancel_keyboard,
            )
        except Exception as e:
            logger.warning(f"Failed to update progress message: {e}")

    # Execute transfer with semaphore acquisition
    try:
        async with _transfer_semaphore:
            # Get clients
            telethon = get_telethon_client(
                api_id=settings.telegram_api_id,
                api_hash=settings.telegram_api_hash,
                phone=settings.telegram_phone,
            )
            max_client = MaxClient()

            # Create engine with duplicate tracking
            engine = TransferEngine(
                telethon_client=telethon,
                max_api_client=max_client,
                db_session=db_session,
                user_id=user_id,
                tg_channel=tg_channel,
                max_channel_id=max_channel_id,
            )
            
            # Store engine for potential cancellation
            _active_engines[user_id] = engine

            # Run transfer
            result: TransferResult = await engine.transfer_posts(
                tg_channel=tg_channel,
                max_channel_id=max_channel_id,
                count=count,
                progress_callback=progress_callback,
            )

            # Close clients
            await max_client.close()

        # Update free_posts_used if this was a free transfer
        data = await state.get_data()
        using_free_posts = data.get("using_free_posts", False)
        if using_free_posts and user_repo and result.success > 0:
            try:
                await user_repo.add_free_posts_used(user_id, result.success)
                logger.info(f"Updated free_posts_used for user {user_id}: +{result.success}")
            except Exception as e:
                logger.error(f"Failed to update free_posts_used for user {user_id}: {e}")

        # Send completion sticker
        try:
            sticker_msg = await message.answer_sticker(
                "CAACAgIAAxkBAAIhNGm5D520AuBBSj9fz9ldxq6Xj0WbAAIzTwACbtpISK7t5RKVOC4NOgQ"
            )
            await asyncio.sleep(5)
            try:
                await sticker_msg.delete()
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"Could not send sticker: {e}")

        # Build final result message
        result_text = (
            f"✅ <b>Перенос завершён: {result.success} постов</b>\n\n"
            f"⚡ Хотите подключить автопостинг?\n"
            f"Новые посты будут автоматически появляться в Max."
        )

        # Create keyboard with autopost options
        builder = InlineKeyboardBuilder()
        builder.button(text="⚡ Подключить автопостинг", callback_data="transfer_enable_autopost")
        builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
        builder.adjust(1)

        await progress_message.edit_text(
            result_text,
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )

        logger.info(
            f"Transfer completed: {result.success} success, "
            f"{result.failed} failed, {result.skipped} skipped, "
            f"{result.duplicates} duplicates"
        )

    except MaxAPIError as e:
        logger.error(f"Max API error during transfer: {e}")
        clean_error = _strip_html(str(e))
        error_text = (
            f"❌ <b>Ошибка переноса</b>\n\n"
            f"Канал: {channel_title}\n\n"
            f"Ошибка: {clean_error}\n\n"
            f"Проверьте:\n"
            f"• Бот «Репост» добавлен в канал Max\n"
            f"• Боту выданы права «Писать посты»"
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
        await progress_message.edit_text(error_text, parse_mode="HTML", reply_markup=builder.as_markup())

    except RuntimeError as e:
        logger.error(f"Runtime error during transfer: {e}")
        error_text = (
            "❌ <b>Ошибка переноса</b>\n\n"
            "Произошла техническая ошибка.\n\n"
            "Попробуйте позже или обратитесь в поддержку."
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
        await progress_message.edit_text(error_text, parse_mode="HTML", reply_markup=builder.as_markup())

    except Exception as e:
        logger.error(f"Unexpected error during transfer: {e}")
        error_text = (
            f"❌ <b>Ошибка переноса</b>\n\n"
            f"Произошла непредвиденная ошибка.\n"
            f"Попробуйте позже или обратитесь в поддержку."
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
        await progress_message.edit_text(error_text, parse_mode="HTML", reply_markup=builder.as_markup())

    finally:
        # Remove user from active transfers set and cleanup engine
        _active_transfers.discard(user_id)
        _active_engines.pop(user_id, None)
        await state.clear()


@transfer_router.callback_query(lambda c: c.data == "transfer_cancel")
async def cancel_transfer(callback: CallbackQuery, state) -> None:
    """
    Cancel the ongoing transfer for the user.
    
    Args:
        callback: Callback query
        state: FSM state
    """
    # Answer callback FIRST before any async operations
    await callback.answer("⏳ Останавливаю...")
    
    user_id = callback.from_user.id
    
    # Check if user has an active transfer
    if user_id not in _active_transfers:
        await callback.answer("❌ Нет активного переноса", show_alert=True)
        return
    
    # Get the engine and abort it
    engine = _active_engines.get(user_id)
    if engine:
        engine.abort()
        logger.info(f"Transfer cancelled by user {user_id}")
        try:
            await callback.message.edit_text(
                "⏳ <b>Останавливаю перенос...</b>\n\n"
                "Дождитесь завершения текущей операции.",
                parse_mode="HTML",
            )
        except Exception:
            pass
    else:
        await callback.answer("❌ Не удалось остановить перенос", show_alert=True)


@transfer_router.callback_query(lambda c: c.data == "transfer_enable_autopost")
async def enable_autopost_after_transfer(
    callback: CallbackQuery,
    state,
    autopost_manager,
) -> None:
    """
    Enable autoposting for the channel pair after successful transfer.
    
    Uses the channel info from the transfer that was just completed.
    
    Args:
        callback: Callback query
        state: FSM state (may still have data or be cleared)
        autopost_manager: AutopostManager instance
    """
    # Answer callback FIRST before any async operations
    await callback.answer()
    
    # Get data from state (may be cleared, so we need to handle that)
    data = await state.get_data()
    user_id = callback.from_user.id
    tg_channel = data.get("transfer_tg_channel_username")
    max_channel_id = data.get("transfer_max_channel_id")
    channel_title = data.get("transfer_tg_channel_title", "Канал")
    
    if not tg_channel or not max_channel_id:
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
        await callback.message.edit_text(
            "❌ Данные переноса утеряны. Настройте автопостинг через меню.",
            reply_markup=builder.as_markup(),
        )
        return
    
    # Ensure max_channel_id is int
    if isinstance(max_channel_id, str) and max_channel_id.lstrip('-').isdigit():
        max_channel_id = int(max_channel_id)
    
    try:
        # Start autoposting
        success = await autopost_manager.start_autopost(
            tg_channel=tg_channel,
            max_chat_id=max_channel_id,
            user_id=user_id,
        )
        
        if success:
            builder = InlineKeyboardBuilder()
            builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
            await callback.message.edit_text(
                f"✅ <b>Автопостинг включён!</b>\n\n"
                f"📺 Канал: {channel_title}\n\n"
                f"Новые посты будут автоматически появляться в Max через несколько секунд.",
                reply_markup=builder.as_markup(),
            )
        else:
            builder = InlineKeyboardBuilder()
            builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
            await callback.message.edit_text(
                f"⚠️ <b>Автопостинг уже активен</b>\n\n"
                f"📺 Канал: {channel_title}\n\n"
                f"Автопостинг для этого канала уже работает.",
                reply_markup=builder.as_markup(),
            )
        
    except Exception as e:
        logger.error(f"Failed to enable autopost: {e}")
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
        await callback.message.edit_text(
            "❌ Не удалось включить автопостинг.\n\n"
            "Попробуйте настроить через меню «⚡ Автопостинг».",
            reply_markup=builder.as_markup(),
        )


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
    # Answer callback FIRST before any async operations
    await callback.answer()
    
    await callback.message.edit_text(
        "<b>📝 Ввод chat_id вручную</b>\n\n"
        "Max API не возвращает каналы/чаты в списке.\n"
        "Введите числовой ID канала/чата (отрицательное число).\n\n"
        "<b>Как получить chat_id:</b>\n"
        "1. Запустите: <code>python scripts/listen_updates.py</code>\n"
        "2. Удалите и добавьте бота в канал/чат Max\n"
        "3. Скопируйте числовой ID из события\n\n"
        "<b>Пример:</b> <code>-70977371223467</code>",
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )
    await state.set_state(TransferStates.transfer_enter_max_chat_id)


@transfer_router.message(StateFilter(TransferStates.transfer_enter_max_chat_id))
async def process_manual_chat_id(message: Message, state, db_session, user_repo) -> None:
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
            builder = InlineKeyboardBuilder()
            builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
            await _edit_or_send_message(
                message,
                text="❌ <b>Некорректный chat_id</b>\n\n"
                "ID канала/чата в Max должен быть <b>отрицательным числом</b>.\n"
                "Пример: <code>-70977371223467</code>\n\n"
                "Попробуйте снова:",
                state=state,
                reply_markup=builder.as_markup(),
            )
            return
    except ValueError:
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
        await _edit_or_send_message(
            message,
            text="❌ <b>Некорректный формат</b>\n\n"
            "Введите числовой ID (только цифры со знаком минус).\n"
            "Пример: <code>-70977371223467</code>\n\n"
            "Попробуйте снова:",
            state=state,
            reply_markup=builder.as_markup(),
        )
        return
    
    # Store the chat_id
    await state.update_data(transfer_max_channel_id=chat_id)
    logger.info(f"Manually entered chat_id: {chat_id}")
    
    # Proceed to count posts (save binding for future use)
    await _continue_after_max_channel_set(message, state, db_session, user_repo)


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


async def _continue_after_max_channel_set(
    target_message: Message | CallbackQuery,
    state,
    db_session=None,
    user_repo=None,
) -> None:
    """Continue flow after max_channel_id is set (either from API or manual entry)."""
    # Save the binding for future use
    await _save_max_channel_binding(state, db_session)
    
    # Get channel info
    data = await state.get_data()
    channel_title = data.get("transfer_tg_channel_title", "Канал")
    channel_username = data.get("transfer_tg_channel_username", "")
    max_channel_id = data.get("transfer_max_channel_id")
    user_id = data.get("user_id")

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
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
        await _edit_or_send_message(
            target_message,
            text="❌ Не удалось получить доступ к каналу.\n\n"
            "Убедитесь, что:\n"
            "• Вы подписаны на канал (особенно для приватных)\n"
            "• Бот @maxx_repost_bot добавлен в администраторы канала\n"
            "• Канал доступен для чтения",
            state=state,
            reply_markup=builder.as_markup(),
        )
        await state.clear()
        return

    # Check free posts for user (admin bypass)
    free_remaining = 0
    is_admin = user_id in settings.ADMIN_IDS
    
    if is_admin:
        logger.info(f"Admin {user_id}: unlimited transfer access")
        free_remaining = 999999  # Unlimited for admin
    elif user_repo and user_id:
        try:
            user = await user_repo.get_by_telegram_id(user_id)
            if user:
                free_remaining = max(0, 5 - user.free_posts_used)
        except Exception as e:
            logger.warning(f"Could not get user free posts info: {e}")

    # Build message based on free posts availability
    if is_admin:
        # Admin - unlimited transfer
        message_text = (
            f"📺 Канал: {channel_title}\n"
            f"📊 Всего постов: {post_count}\n\n"
            f"♾️ <b>Безлимит</b>\n"
            f"Вы администратор. Перенос без ограничений.\n\n"
            f"Выберите сколько постов перенести:"
        )
    elif free_remaining > 0:
        # User has free posts available
        message_text = (
            f"📺 Канал: {channel_title}\n"
            f"📊 Всего постов: {post_count}\n\n"
            f"🎁 <b>У вас {free_remaining} бесплатных постов!</b>\n"
            f"Попробуйте перенос бесплатно, чтобы оценить качество.\n\n"
            f"💰 После бесплатных: {PRICE_PER_POST}₽ за пост\n\n"
            f"Выберите сколько постов перенести:"
        )
    else:
        # No free posts remaining - paid only
        total_price = post_count * PRICE_PER_POST
        message_text = (
            f"📺 Канал: {channel_title}\n"
            f"📊 Всего постов: {post_count}\n"
            f"💰 Стоимость: {total_price}₽ ({PRICE_PER_POST}₽/пост)\n\n"
            f"Выберите сколько постов перенести:"
        )

    await _edit_or_send_message(
        target_message,
        text=message_text,
        state=state,
        reply_markup=select_count_keyboard(post_count, free_remaining, is_admin=is_admin),
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
async def process_transfer_max_channel(message: Message, state, db_session, user_repo) -> None:
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
            message,
            text="❌ Не удалось распознать ссылку на канал Max.\n\n"
            "Отправьте:\n"
            "• Ссылку на канал: <i>https://max.me/username</i> или <i>https://max.ru/join/...</i>\n"
            "• Или числовой ID: <i>-123456789</i>",
            state=state,
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
        await _continue_after_max_channel_set(message, state, db_session, user_repo)
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
                    message,
                    text="🔍 <b>Определяю ID канала/чата...</b>\n\n"
                    "Убедитесь, что бот добавлен в канал Max как администратор "
                    "с правом <b>«Писать посты»</b>.",
                    state=state,
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
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
        await _edit_or_send_message(
            message,
            text="❌ Ошибка подключения к Max API.\n\n"
            "Убедитесь, что:\n"
            f"• Бот «Репост» ({MAX_BOT_USERNAME}) добавлен в канал\n"
            "• Боту выданы права «Писать посты»\n\n"
            "Попробуйте снова:",
            state=state,
            reply_markup=builder.as_markup(),
        )
        return

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
        await _edit_or_send_message(
            message,
            text="❌ Произошла ошибка. Попробуйте позже.",
            state=state,
            reply_markup=builder.as_markup(),
        )
        return

    if not max_channel_id:
        builder = InlineKeyboardBuilder()
        builder.button(text="📝 Ввести chat_id вручную", callback_data="transfer_enter_chat_id")
        builder.button(text="🏠 В меню", callback_data="nav_goto_menu")
        builder.adjust(1)
        await _edit_or_send_message(
            message,
            text="❌ <b>Не удалось определить канал/чат</b>\n\n"
            "Попробуйте ввести chat_id вручную:",
            state=state,
            reply_markup=builder.as_markup(),
        )
        return

    # Store numeric Max channel ID
    await state.update_data(transfer_max_channel_id=max_channel_id)
    
    # Continue to post counting (save binding for future use)
    await _continue_after_max_channel_set(message, state, db_session, user_repo)


@transfer_router.callback_query(lambda c: c.data.startswith("transfer_count:"), StateFilter(TransferStates.transfer_select_count))
async def process_post_count_selection(
    callback: CallbackQuery,
    state,
    db_session,
    user_repo,
) -> None:
    """
    Process user's selection of post count and start transfer.

    Args:
        callback: Callback query with selection
        state: FSM state
        db_session: Database session for duplicate tracking
        user_repo: User repository for tracking free posts
    """
    # Answer callback FIRST with loading indicator before any async operations
    await callback.answer("⏳ Подготовка...")
    
    action = callback.data.split(":", 1)[1]
    
    # Handle back button
    if action == "back":
        # Answer callback FIRST before any async operations
        await callback.answer()
        # Go back to Max channel selection
        data = await state.get_data()
        channel_title = data.get("transfer_tg_channel_title", "Канал")
        await _show_max_connection_instructions(callback.message, state, channel_title, db_session, user_repo)
        return
    
    # Handle custom count input
    if action == "custom":
        await callback.answer()
        await callback.message.edit_text(
            "🔢 Введите количество постов для переноса:",
            reply_markup=back_keyboard(),
        )
        await state.set_state(TransferStates.transfer_select_count)
        return
    
    # Handle free posts selection
    if action == "free":
        # Get user to check how many free posts remain
        data = await state.get_data()
        user_id = data.get("user_id")
        
        if not user_id or not user_repo:
            await callback.answer("❌ Ошибка: не удалось получить данные пользователя", show_alert=True)
            return
        
        # Admin bypass - unlimited transfer
        is_admin = user_id in settings.ADMIN_IDS
        if is_admin:
            # Admin can transfer all posts without limit
            await callback.answer("♾️ Админ: безлимитный перенос")
            await state.update_data(using_free_posts=False, is_admin_transfer=True)
            await _execute_transfer(
                callback.message,
                state,
                "all",
                is_callback=True,
                db_session=db_session,
                user_repo=user_repo,
            )
            return
        
        user = await user_repo.get_by_telegram_id(user_id)
        if not user:
            await callback.answer("❌ Ошибка: пользователь не найден", show_alert=True)
            return
        
        free_remaining = max(0, 5 - user.free_posts_used)
        if free_remaining <= 0:
            await callback.answer("❌ Бесплатные посты уже использованы", show_alert=True)
            return
        
        # Store that we're using free posts and the count
        await state.update_data(using_free_posts=True, free_posts_count=free_remaining)
        
        await callback.answer(f"🎁 Перенос {free_remaining} постов бесплатно")
        await _execute_transfer(
            callback.message, 
            state, 
            free_remaining, 
            is_callback=True, 
            db_session=db_session,
            user_repo=user_repo,
        )
        return

    # Determine count
    if action == "all":
        count = "all"
    elif action == "100":
        count = 100
    elif action == "50":
        count = 50
    else:
        await callback.answer("Неизвестный выбор", show_alert=True)
        return
    
    # Validate 500 posts limit for "all" selection
    if count == "all":
        data = await state.get_data()
        tg_channel = data.get("transfer_tg_channel_username", "")
        if tg_channel:
            try:
                telethon = get_telethon_client(
                    api_id=settings.telegram_api_id,
                    api_hash=settings.telegram_api_hash,
                    phone=settings.telegram_phone,
                )
                post_count = await telethon.count_channel_posts(tg_channel)
                if post_count > MAX_TRANSFER_POSTS:
                    logger.info(f"Limiting transfer to {MAX_TRANSFER_POSTS} posts (requested all, total {post_count})")
                    await callback.answer(
                        f"⚠️ В канале {post_count} постов. Будет перенесено первые {MAX_TRANSFER_POSTS}.",
                        show_alert=True
                    )
            except Exception as e:
                logger.warning(f"Could not count posts for limit check: {e}")

    await callback.answer()
    await _execute_transfer(
        callback.message, 
        state, 
        count, 
        is_callback=True, 
        db_session=db_session,
        user_repo=user_repo,
    )


# =============================================================================
# Custom count input
# =============================================================================


@transfer_router.message(StateFilter(TransferStates.transfer_select_count))
async def process_custom_post_count(
    message: Message,
    state,
    db_session,
    user_repo,
) -> None:
    """
    Process custom post count input and start transfer.

    Args:
        message: User message with number
        state: FSM state
        db_session: Database session for duplicate tracking
        user_repo: User repository for tracking free posts
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
            message,
            text="❌ Введите корректное число (больше 0).",
            state=state,
            reply_markup=back_keyboard(),
        )
        return
    
    # Validate 500 posts limit
    if count > MAX_TRANSFER_POSTS:
        logger.info(f"Limiting transfer to {MAX_TRANSFER_POSTS} posts (requested {count})")
        await _edit_or_send_message(
            message,
            text=f"⚠️ Максимум {MAX_TRANSFER_POSTS} постов за один перенос.\n"
                 f"Будет перенесено {MAX_TRANSFER_POSTS} постов.",
            state=state,
            reply_markup=back_keyboard(),
        )

    await _execute_transfer(message, state, count, is_callback=False, db_session=db_session, user_repo=user_repo)
