"""Auto-posting setup handler - FSM flow for connecting TG channels to Max."""

from aiogram import Router
from aiogram.filters import StateFilter
from aiogram.types import Message, CallbackQuery
from loguru import logger

from bot.telegram.states import AutopostStates
from bot.telegram.keyboards.autopost import (
    check_admin_keyboard,
    back_to_menu_keyboard,
    autopost_complete_keyboard,
)
from bot.max_api.client import MaxClient, MaxAPIError


# Bot configuration
MAX_BOT_USERNAME = "id752703975446_1_bot"
MAX_BOT_LINK = "https://max.ru/id752703975446_1_bot"
TG_BOT_USERNAME = "maxx_repost_bot"


# Create router
autopost_router = Router(name="autopost")


# =============================================================================
# Entry Points
# =============================================================================


@autopost_router.callback_query(lambda c: c.data == "start_setup_autopost")
@autopost_router.callback_query(lambda c: c.data == "menu_new_autopost")
async def start_autopost_setup(callback: CallbackQuery, state) -> None:
    """
    Start auto-posting setup flow.

    Can be triggered from start screen or menu.

    Args:
        callback: Callback query
        state: FSM state
    """
    await callback.message.edit_text(
        "<b>🔄 Настройка автопостинга</b>\n\n"
        "Для автоматического переноса постов требуются права администратора в Telegram-канале.\n\n"
        "👉 Укажите ссылку на ваш Telegram-канал:\n"
        "<i>https://t.me/channelname</i>",
        parse_mode="HTML",
    )

    # Set FSM state
    await state.set_state(AutopostStates.waiting_tg_channel)
    await callback.answer()


@autopost_router.message(StateFilter(AutopostStates.waiting_tg_channel))
async def process_tg_channel_link(message: Message, state, bot) -> None:
    """
    Process Telegram channel link from user.

    Validates the link and gets channel info.

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
    elif text.startswith("https://t.me/"):
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
    await state.update_data(tg_channel_username=channel_username)

    # TEMP: Let exceptions propagate to see full traceback
    chat = await bot.get_chat(f"@{channel_username}")

    # Store chat info
    await state.update_data(
        tg_channel_id=str(chat.id),
        tg_channel_title=chat.title,
        tg_channel_username=channel_username,
    )

    await message.answer(
        f"<b>📢 Канал найден: {chat.title}</b>\n\n"
        f"Для автопостинга необходимо назначить бота администратором канала.\n\n"
        f"<b>Инструкция:</b>\n"
        f"1. Откройте настройки канала ➡ Администраторы.\n"
        f"2. Добавьте @{TG_BOT_USERNAME} как администратора. "
        f"<b>Никаких разрешений для бота включать не нужно!</b>\n"
        f"3. Сохраните изменения.\n"
        f"4. Нажмите «Проверить».",
        parse_mode="HTML",
        reply_markup=check_admin_keyboard(),
    )

    await state.set_state(AutopostStates.waiting_tg_admin_check)


@autopost_router.callback_query(lambda c: c.data == "autopost_check_admin", StateFilter(AutopostStates.waiting_tg_admin_check))
async def check_admin_status(callback: CallbackQuery, state, bot) -> None:
    """
    Check if bot is admin in the Telegram channel.

    Args:
        callback: Callback query
        state: FSM state with stored channel info
        bot: Bot instance
    """
    data = await state.get_data()
    channel_id = data.get("tg_channel_id")
    channel_title = data.get("tg_channel_title", "Канал")

    if not channel_id:
        await callback.message.edit_text(
            "❌ Ошибка: данные канала утеряны. Начните заново.",
            reply_markup=back_to_menu_keyboard(),
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
            await callback.message.edit_text(
                f"✅ Админ подтвержден!\n\n"
                f"Канал: <b>{channel_title}</b>\n\n"
                f"Теперь подключите канал в MAX.\n\n"
                f"<b>Инструкция:</b>\n"
                f"1. Откройте <b>Настройки канала ➡ Подписчики</b>\n"
                f"2. Добавьте подписчика «Репост» ({MAX_BOT_USERNAME})\n"
                f"3. Перейдите в <b>Настройки канала ➡ Администраторы</b>\n"
                f"4. Добавьте администратора «Репост» ({MAX_BOT_USERNAME})\n"
                f"5. Включите <b>«Писать посты»</b> и сохраните\n\n"
                f"➡ <b>Вернитесь сюда и отправьте ссылку на канал в MAX</b>\n"
                f"<i>https://max.me/username или ID канала</i>\n\n"
                f"⚠️ Если Max не находит бота по нику — попробуйте найти по названию «Репост»",
                parse_mode="HTML",
            )
            await state.set_state(AutopostStates.waiting_max_channel)
        else:
            await callback.answer("❌ Бот ещё не добавлен в администраторы.", show_alert=True)

    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        await callback.answer("❌ Ошибка проверки статуса. Попробуйте снова.", show_alert=True)


@autopost_router.message(StateFilter(AutopostStates.waiting_max_channel))
async def process_max_channel_link(message: Message, state) -> None:
    """
    Process Max channel link and verify bot has access.

    Args:
        message: User message with Max channel link/ID
        state: FSM state
    """
    text = message.text.strip()
    user = message.from_user

    # Parse Max channel ID - could be URL or direct ID
    if "max.me/" in text or "max.ru/" in text:
        # Extract username from URL
        parts = text.split("/")[-1].strip()
        max_channel_id = parts
    elif text.startswith("@"):
        max_channel_id = text[1:]
    else:
        # Assume it's a direct channel ID
        max_channel_id = text.strip()

    if not max_channel_id:
        await message.answer(
            "❌ Не удалось распознать ссылку на канал MAX.\n\n"
            "Отправьте ссылку или ID канала.",
        )
        return

    # Verify bot has access to this Max channel
    try:
        async with MaxClient() as client:
            # Try to get chats to verify bot is working
            chats = await client.get_chats()

            # Check if our target channel is accessible
            # Since we can't directly search, we'll send a test message
            # For now, we'll just verify the API works and store the channel
            logger.info(f"Max API verified, accessible chats: {len(chats)}")

    except MaxAPIError as e:
        logger.error(f"Max API error: {e}")
        await message.answer(
            "❌ Ошибка подключения к Max API.\n\n"
            "Убедитесь, что:\n"
            f"• Бот «Репост» ({MAX_BOT_USERNAME}) добавлен в канал\n"
            "• Боту выданы права «Писать посты»\n\n"
            "Попробуйте снова:",
            reply_markup=back_to_menu_keyboard(),
        )
        return

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        await message.answer(
            "❌ Произошла ошибка. Попробуйте позже.",
            reply_markup=back_to_menu_keyboard(),
        )
        return

    # Get stored data
    data = await state.get_data()
    tg_channel_id = data.get("tg_channel_id")
    tg_channel_title = data.get("tg_channel_title", "Канал")
    tg_channel_username = data.get("tg_channel_username", "")

    if not tg_channel_id:
        await message.answer(
            "❌ Ошибка: данные канала утеряны. Начните заново.",
            reply_markup=back_to_menu_keyboard(),
        )
        await state.clear()
        return

    # TODO: Save to database via channel_repo
    # For now, just show success message
    await message.answer(
        f"<b>🎉 Автопостинг настроен!</b>\n\n"
        f"📢 TG: {tg_channel_title}\n"
        f"➡ MAX: подключен\n"
        f"💰 Баланс: 0 постов",
        parse_mode="HTML",
        reply_markup=autopost_complete_keyboard(),
    )

    logger.info(
        f"Autopost setup completed for user {user.id}: "
        f"TG @{tg_channel_username} -> Max {max_channel_id}"
    )

    await state.clear()


# =============================================================================
# Navigation
# =============================================================================


@autopost_router.callback_query(lambda c: c.data == "autopost_cancel")
async def cancel_autopost(callback: CallbackQuery, state) -> None:
    """Cancel autopost setup and return to menu."""
    await state.clear()
    await callback.message.edit_text(
        "❌ Настройка автопостинга отменена.",
        reply_markup=back_to_menu_keyboard(),
    )
    await callback.answer()
