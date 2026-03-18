"""Start router with /start, /menu, /help handlers and navigation callbacks."""

import asyncio

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from loguru import logger

from bot.telegram.keyboards.main import (
    start_keyboard,
    menu_keyboard,
    back_to_menu_keyboard,
    balance_keyboard,
)
from bot.database.repositories.balance import UserBalanceRepository

# Create router
start_router = Router(name="start")

# Bot configuration
TG_BOT_USERNAME = "maxx_repost_bot"
MAX_BOT_NAME = "Репост"
MAX_BOT_USERNAME = "id752703975446_1_bot"
MAX_BOT_LINK = "https://max.ru/id752703975446_1_bot"
SUPPORT_BOT = "@maxx_repost_support"

# Welcome sticker
WELCOME_STICKER = "CAACAgIAAxkBAAIhSmm5Iq9RaarKBrdOXPkDrOKyC-ROAALwFwACKWWpSS1UtcEXnRxkOgQ"


async def _delete_after_delay(msg, seconds: int = 5) -> None:
    """Delete message after specified delay."""
    await asyncio.sleep(seconds)
    try:
        await msg.delete()
    except Exception:
        pass


def get_welcome_message(first_name: str) -> str:
    """Generate personalized welcome message."""
    return f"""<b>👋 Привет, {first_name}!</b>

Я бот для переноса постов из Telegram в Max.

<b>Что я умею:</b>
📦 Перенести все ваши посты из TG-канала в Max
🔄 Настроить автопостинг новых постов

🎁 <b>Новые пользователи получают 5 бесплатных постов!</b>

Выберите действие:"""

# Help / Instruction message
INSTRUCTION_MESSAGE = """
<b>📖 Как пользоваться ботом</b>

<b>📥 Перенос контента:</b>
1. Нажмите «Настроить перенос»
2. Отправьте ссылку на ваш Telegram-канал
3. Подтвердите права владения (код в описание канала)
4. Добавьте бота в канал Max как администратора
5. Выберите количество постов
6. Дождитесь завершения переноса

<b>⚡ Автопостинг:</b>
Новые посты из Telegram автоматически появляются в Max 
через несколько секунд после публикации.

<b>💰 Стоимость:</b>
• Перенос: 3₽ за пост
• Автопостинг: бесплатно после переноса

<b>✅ Поддерживается:</b>
✅ Текст с форматированием
✅ Фото и видео
✅ Аудио и голосовые
✅ Документы и файлы (до 4 ГБ)
✅ Ссылки

<b>❓ Поддержка:</b> @maxx_repost_support
"""

# Alias for backwards compatibility
HELP_MESSAGE = INSTRUCTION_MESSAGE


# =============================================================================
# Commands
# =============================================================================


@start_router.message(Command("start"))
async def cmd_start(message: Message, user_repo) -> None:
    """
    Handle /start command.

    Register user if new, send welcome sticker and message with start keyboard.
    First-time users see the welcome with 3 main actions.

    Args:
        message: Telegram message
        user_repo: User repository from middleware
    """
    # Register user (get or create)
    user, _ = await user_repo.get_or_create(message.from_user.id)
    
    # Get user's first name
    first_name = message.from_user.first_name or "друг"
    
    # Send welcome sticker and delete after 5 seconds
    try:
        sticker_msg = await message.answer_sticker(WELCOME_STICKER)
        asyncio.create_task(_delete_after_delay(sticker_msg, 5))
    except Exception as e:
        logger.debug(f"Could not send welcome sticker: {e}")

    await message.answer(
        get_welcome_message(first_name),
        parse_mode="HTML",
        reply_markup=start_keyboard(),
    )


@start_router.message(Command("menu"))
async def cmd_menu(
    message: Message,
    user_repo,
    channel_repo,
    verified_channel_repo,
    transferred_post_repo,
) -> None:
    """
    Handle /menu command.

    Show personal cabinet with user stats and full menu.
    Returning users see the menu with all options.

    Args:
        message: Telegram message
        user_repo: User repository from middleware
        channel_repo: Channel repository for counting channels
        verified_channel_repo: Repository for verified channels
        transferred_post_repo: Repository for transferred posts
    """
    user = await user_repo.get_by_telegram_id(message.from_user.id)

    if user is None:
        # Fallback to get_or_create if user not found
        user, _ = await user_repo.get_or_create(message.from_user.id)

    # Get stats
    verified_count = 0
    total_transferred = 0
    
    try:
        # Count verified channels
        verified_channels = await verified_channel_repo.get_user_verified_channels(message.from_user.id)
        verified_count = len(verified_channels)
    except Exception as e:
        logger.debug(f"Could not get verified channels count: {e}")
    
    # Calculate free remaining
    free_remaining = max(0, 5 - user.free_posts_used)

    menu_text = (
        f"<b>👤 Личный кабинет</b>\n\n"
        f"📢 Каналов: {verified_count}\n"
        f"🎁 Баланс: {free_remaining} бесплатных постов\n"
    )

    await message.answer(
        menu_text,
        parse_mode="HTML",
        reply_markup=menu_keyboard(),
    )


@start_router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """
    Handle /help command.

    Send full help text with instructions.

    Args:
        message: Telegram message
    """
    await message.answer(
        INSTRUCTION_MESSAGE,
        parse_mode="HTML",
        reply_markup=back_to_menu_keyboard(),
    )


# =============================================================================
# Menu Callbacks
# =============================================================================
# Note: start_setup_transfer and start_setup_autopost are handled by
# transfer_router and autopost_router respectively
# Note: menu_channels, menu_new_transfer, menu_new_autopost are handled by
# their respective routers (channels_router, transfer_router, autopost_router)


@start_router.callback_query(lambda c: c.data == "menu_balance")
async def callback_balance(
    callback: CallbackQuery,
    balance_repo: UserBalanceRepository,
) -> None:
    """
    Handle 'Check balance' button - show detailed balance info.
    
    Shows balance in rubles with statistics.
    """
    # Answer callback FIRST before any async operations
    await callback.answer("⏳")
    
    user_id = callback.from_user.id
    
    # Get user balance from UserBalanceRepository
    user_balance = await balance_repo.get_or_create(user_id)
    
    # Build balance text with rubles and statistics
    balance_text = (
        f"<b>💰 Ваш баланс: {int(user_balance.balance)}₽</b>\n\n"
        f"Пополнено: {int(user_balance.total_deposited)}₽\n"
        f"Потрачено: {int(user_balance.total_spent)}₽"
    )

    await callback.message.edit_text(
        balance_text,
        parse_mode="HTML",
        reply_markup=balance_keyboard(),
    )


@start_router.callback_query(lambda c: c.data == "menu_bonus")
async def callback_bonus(callback: CallbackQuery, user_repo) -> None:
    """Handle 'Bonus posts' button - show free posts info."""
    # Answer callback FIRST before any async operations
    await callback.answer("⏳")
    
    user = await user_repo.get_by_telegram_id(callback.from_user.id)
    
    # Calculate free remaining
    free_remaining = max(0, 5 - user.free_posts_used)
    
    await callback.message.edit_text(
        f"<b>🎁 Бонусные посты</b>\n\n"
        f"Каждый новый пользователь получает 5 бесплатных постов для ознакомления с сервисом.\n\n"
        f"<b>Ваш статус:</b> {free_remaining} из 5 бесплатных постов осталось\n\n"
        f"💰 После использования бесплатных постов — перенос стоит 3₽ за пост.",
        parse_mode="HTML",
        reply_markup=back_to_menu_keyboard(),
    )


@start_router.callback_query(lambda c: c.data == "menu_promo")
async def callback_promo(callback: CallbackQuery) -> None:
    """Handle 'Activate promo' button - placeholder."""
    # Answer callback FIRST before any async operations
    await callback.answer()
    
    await callback.message.edit_text(
        "<b>🎟 Активация промокода</b>\n\n"
        "🚧 В разработке\n\n"
        "Введите промокод для получения бонусных постов.",
        parse_mode="HTML",
        reply_markup=back_to_menu_keyboard(),
    )
    await callback.answer()


@start_router.callback_query(lambda c: c.data == "menu_help")
async def callback_help(callback: CallbackQuery) -> None:
    """Handle 'Help' button - same as /help."""
    # Answer callback FIRST before any async operations
    await callback.answer()
    
    await callback.message.edit_text(
        INSTRUCTION_MESSAGE,
        parse_mode="HTML",
        reply_markup=back_to_menu_keyboard(),
    )


# =============================================================================
# Navigation Callbacks
# =============================================================================


@start_router.callback_query(lambda c: c.data == "nav_goto_menu")
async def callback_goto_menu(
    callback: CallbackQuery,
    user_repo,
    verified_channel_repo,
) -> None:
    """Handle 'Back to menu' navigation."""
    # Answer callback FIRST before any async operations
    await callback.answer("⏳")
    
    user = await user_repo.get_by_telegram_id(callback.from_user.id)

    if user is None:
        user, _ = await user_repo.get_or_create(callback.from_user.id)

    # Get stats
    verified_count = 0
    
    try:
        # Count verified channels
        verified_channels = await verified_channel_repo.get_user_verified_channels(callback.from_user.id)
        verified_count = len(verified_channels)
    except Exception as e:
        logger.debug(f"Could not get verified channels count: {e}")
    
    # Calculate free remaining
    free_remaining = max(0, 5 - user.free_posts_used)

    menu_text = (
        f"<b>👤 Личный кабинет</b>\n\n"
        f"📢 Каналов: {verified_count}\n"
        f"🎁 Баланс: {free_remaining} бесплатных постов\n"
    )

    await callback.message.edit_text(
        menu_text,
        parse_mode="HTML",
        reply_markup=menu_keyboard(),
    )
