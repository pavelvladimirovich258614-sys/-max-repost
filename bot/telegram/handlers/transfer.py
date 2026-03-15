"""Post transfer handler - FSM flow for manual post transfer from TG to Max."""

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

    try:
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

        from aiogram.enums import ChatMemberAdministrator

        if member.status not in (ChatMemberAdministrator.ADMINISTRATOR, ChatMemberAdministrator.OWNER):
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

    except Exception as e:
        logger.error(f"Error getting chat info: {e}")
        await message.answer(
            "❌ Канал не найден. Проверьте, что:\n"
            "• Ссылка правильная\n"
            "• Канал публичный (есть username)\n"
            "• Бот имеет доступ к каналу\n\n"
            "Попробуйте снова:",
            reply_markup=back_to_start_keyboard(),
        )
        await state.clear()


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

        from aiogram.enums import ChatMemberAdministrator

        if member.status in (ChatMemberAdministrator.ADMINISTRATOR, ChatMemberAdministrator.OWNER):
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
        f"1. Перейдите по ссылке {MAX_BOT_LINK} и нажмите «Старт».\n"
        f"2. Откройте Настройки канала ➡ Подписчики.\n"
        f"3. Добавьте подписчика «Репост» ({MAX_BOT_USERNAME}).\n"
        f"4. Перейдите в Настройки канала ➡ Администраторы.\n"
        f"5. Добавьте администратора «Репост» ({MAX_BOT_USERNAME}).\n"
        f"6. Включите функцию «Писать посты» и нажмите «Сохранить».\n\n"
        f"Теперь укажите ссылку на канал в MAX:\n"
        f"<i>https://max.me/username или ID канала</i>"
    )

    if isinstance(message, CallbackQuery):
        await message.message.edit_text(text, parse_mode="HTML")
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=back_keyboard())

    await state.set_state(TransferStates.transfer_waiting_max_channel)


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

    # TODO: Analyze channel to count posts
    # For now, use placeholder
    post_count = 150  # Placeholder - will be calculated from real channel

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
    Process user's selection of post count.

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

    data = await state.get_data()
    channel_title = data.get("transfer_tg_channel_title", "Канал")

    if count == "all":
        count_text = "Все посты"
    else:
        count_text = f"{count} постов"

    await callback.message.edit_text(
        f"🚧 <b>Перенос будет реализован на следующем этапе</b>\n\n"
        f"Канал: {channel_title}\n"
        f"Выбрано: {count_text}\n\n"
        f"Функционал переноса постов будет доступен после реализации Core Engine.",
        parse_mode="HTML",
        reply_markup=transfer_complete_keyboard(),
    )

    logger.info(f"Transfer selection made: {count_text} from {channel_title}")

    await state.clear()
    await callback.answer()


# =============================================================================
# Custom count input
# =============================================================================


@transfer_router.message(StateFilter(TransferStates.transfer_select_count))
async def process_custom_post_count(message: Message, state) -> None:
    """
    Process custom post count input.

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

    data = await state.get_data()
    channel_title = data.get("transfer_tg_channel_title", "Канал")
    total_price = count * PRICE_PER_POST

    await message.answer(
        f"🚧 <b>Перенос будет реализован на следующем этапе</b>\n\n"
        f"Канал: {channel_title}\n"
        f"Выбрано: {count} постов\n"
        f"Стоимость: {total_price} руб.\n\n"
        f"Функционал переноса постов будет доступен после реализации Core Engine.",
        parse_mode="HTML",
        reply_markup=transfer_complete_keyboard(),
    )

    logger.info(f"Transfer custom count: {count} from {channel_title}")

    await state.clear()


# No additional imports needed
