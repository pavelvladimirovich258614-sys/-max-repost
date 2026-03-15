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
    select_count_keyboard,
    transfer_complete_keyboard,
)
from bot.max_api.client import MaxClient, MaxAPIError
from bot.core.telethon_client import get_telethon_client
from bot.core.transfer_engine import TransferEngine, TransferResult
from config.settings import settings


# =============================================================================
# Utilities
# =============================================================================


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
        await message.answer(
            "❌ Не удалось распознать ссылку на канал.\n\n"
            "Отправьте ссылку в формате:\n"
            "<i>https://t.me/channelname</i> или <i>@channelname</i>",
            parse_mode="HTML",
        )
        return

    # Store channel username in state
    await state.update_data(transfer_tg_channel_username=channel_username)

    # TEMP: Let exceptions propagate to see full traceback
    # Try to get chat info
    chat = await bot.get_chat(f"@{channel_username}")

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
        await message.answer(
            f"<b>📢 Канал найден: {chat.title}</b>\n\n"
            f"Для доступа к постам бота нужно добавить в администраторы.\n\n"
            f"<b>Инструкция:</b>\n"
            f"1. Откройте настройки канала ➡ Администраторы.\n"
            f"2. Добавьте @{TG_BOT_USERNAME} как администратора.\n"
            f"3. Сохраните изменения и нажмите «Продолжить».",
            parse_mode="HTML",
            reply_markup=_build_continue_keyboard(),
        )
        await state.set_state(TransferStates.transfer_waiting_verification)
    else:
        # Bot is already admin - skip to Max connection
        await _show_max_connection_instructions(message, state, chat.title)


def _build_continue_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard for 'Continue' after adding bot as admin."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Продолжить", callback_data="transfer_verify_admin")
    builder.button(text="↩️ Назад", callback_data="nav_goto_menu")
    builder.adjust(1)
    return builder.as_markup()


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
            # Bot is admin - proceed to Max setup
            await _show_max_connection_instructions(callback.message, state, channel_title)
        else:
            await callback.answer("❌ Бот ещё не добавлен в администраторы.", show_alert=True)

    except Exception as e:
        logger.error(f"Error verifying admin: {e}")
        await callback.answer("❌ Ошибка проверки. Попробуйте снова.", show_alert=True)


async def _show_max_connection_instructions(message: Message, state, channel_title: str) -> None:
    """
    Show Max channel connection instructions.

    Args:
        message: Message to edit or send
        state: FSM state
        channel_title: Telegram channel title
    """
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

    if isinstance(message, CallbackQuery):
        await message.message.edit_text(text, parse_mode="HTML")
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=back_keyboard())

    await state.set_state(TransferStates.transfer_waiting_max_channel)


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


@transfer_router.message(StateFilter(TransferStates.transfer_waiting_max_channel))
async def process_transfer_max_channel(message: Message, state) -> None:
    """
    Process Max channel link for transfer.

    Args:
        message: User message with Max channel link/ID
        state: FSM state
    """
    text = message.text.strip()

    # Parse Max channel ID
    if "max.me/" in text or "max.ru/" in text:
        parts = text.split("/")[-1].strip()
        max_channel_id = parts
    elif text.startswith("@"):
        max_channel_id = text[1:]
    else:
        max_channel_id = text.strip()

    if not max_channel_id:
        await message.answer(
            "❌ Не удалось распознать ссылку на канал MAX.\n\n"
            "Отправьте ссылку или ID канала.",
        )
        return

    # Verify Max API access
    try:
        async with MaxClient() as client:
            chats = await client.get_chats()
            logger.info(f"Max API verified, accessible chats: {len(chats)}")

    except MaxAPIError as e:
        logger.error(f"Max API error: {e}")
        await message.answer(
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
        await message.answer(
            "❌ Произошла ошибка. Попробуйте позже.",
            reply_markup=back_keyboard(),
        )
        return

    # Store Max channel ID
    await state.update_data(transfer_max_channel_id=max_channel_id)

    # Get channel info
    data = await state.get_data()
    channel_title = data.get("transfer_tg_channel_title", "Канал")
    channel_username = data.get("transfer_tg_channel_username", "")

    # Count posts using Telethon (real count, not placeholder)
    try:
        telethon = get_telethon_client(
            api_id=settings.telegram_api_id,
            api_hash=settings.telegram_api_hash,
            phone=settings.telegram_phone,
        )
        post_count = await telethon.count_channel_posts(channel_username)
    except Exception as e:
        logger.error(f"Error counting posts: {e}")
        await message.answer(
            "❌ Не удалось подсчитать посты в канале.\n\n"
            f"Ошибка: {str(e)}\n\n"
            "Убедитесь, что канал публичный и бот имеет к нему доступ.",
            reply_markup=back_keyboard(),
        )
        return

    total_price = post_count * PRICE_PER_POST

    await message.answer(
        f"<b>🚀 Мастер переноса</b>\n"
        f"Канал: {channel_title}\n"
        f"Постов: ~{post_count}\n\n"
        f"💰 Тариф: {PRICE_PER_POST} руб./пост\n"
        f"Итого за все: {total_price} руб.\n\n"
        f"🔢 Сколько постов перенести?",
        parse_mode="HTML",
        reply_markup=select_count_keyboard(post_count),
    )

    await state.set_state(TransferStates.transfer_select_count)


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
    text = message.text.strip()

    try:
        count = int(text)
        if count <= 0:
            raise ValueError()
    except ValueError:
        await message.answer(
            "❌ Введите корректное число (больше 0).",
            reply_markup=back_keyboard(),
        )
        return

    await _execute_transfer(message, state, count, is_callback=False)


# No additional imports needed
