"""Start router with /start, /menu, /help handlers and navigation callbacks."""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from bot.telegram.keyboards.main import (
    start_keyboard,
    menu_keyboard,
    back_to_menu_keyboard,
)

# Create router
start_router = Router(name="start")

# Bot configuration
TG_BOT_USERNAME = "maxx_repost_bot"
MAX_BOT_NAME = "Репост"
MAX_BOT_USERNAME = "id752703975446_1_bot"
MAX_BOT_LINK = "https://max.ru/id752703975446_1_bot"
SUPPORT_BOT = "@NeuroCash_Support_Bot"

# Welcome message
WELCOME_MESSAGE = """
<b>👋 Добро пожаловать!</b>

Я бот <b>«Репост»</b> — помогу переносить посты из Telegram-каналов в мессенджер Max.

<b>Что я умею:</b>
• 🔄 Автоматически репостить новые посты
• 📥 Переносить архив постов
• 🎛 Управлять несколькими каналами

Выберите действие:
"""

# Help message
HELP_MESSAGE = f"""
<b>ℹ️ Помощь и инструкции</b>

<b>📥 Настроить перенос</b> — подключите канал и перенесите архив постов в Max. Посты переносятся полностью с фото и видео.

<b>🔄 Настроить автопостинг</b> — новые посты будут автоматически появляться в Max. Бот должен быть администратором канала.

<b>📢 Мои каналы</b> — управляйте подключенными каналами, включайте/выключайте автопостинг.

<b>💎 Баланс</b> — 1 пост = 1 рубль. Пополните баланс через ЮKassa.

<b>🎁 Бонус</b> — получите 10 бесплатных постов за подписку на канал.

<b>🎟 Промокод</b> — активируйте промокод для получения бонусных постов.

<b>📋 Инструкция по подключению:</b>

1. Добавьте бота @{TG_BOT_USERNAME} в администраторы Telegram-канала
2. Перейдите в Max: {MAX_BOT_LINK}
3. Добавьте бота «{MAX_BOT_NAME}» ({MAX_BOT_USERNAME}) в подписчики и администраторы Max-канала
4. Включите боту право «Писать посты»

<i>По всем вопросам пишите: {SUPPORT_BOT}</i>
"""


# =============================================================================
# Commands
# =============================================================================


@start_router.message(Command("start"))
async def cmd_start(message: Message, user_repo) -> None:
    """
    Handle /start command.

    Register user if new, send welcome message with start keyboard.
    First-time users see the welcome with 3 main actions.

    Args:
        message: Telegram message
        user_repo: User repository from middleware
    """
    # Register user (get or create)
    user, _ = await user_repo.get_or_create(message.from_user.id)

    await message.answer(
        WELCOME_MESSAGE,
        parse_mode="HTML",
        reply_markup=start_keyboard(),
    )


@start_router.message(Command("menu"))
async def cmd_menu(message: Message, user_repo, channel_repo) -> None:
    """
    Handle /menu command.

    Show personal cabinet with user stats and full menu.
    Returning users see the menu with all options.

    Args:
        message: Telegram message
        user_repo: User repository from middleware
        channel_repo: Channel repository for counting channels
    """
    user = await user_repo.get_by_telegram_id(message.from_user.id)

    if user is None:
        # Fallback to get_or_create if user not found
        user, _ = await user_repo.get_or_create(message.from_user.id)

    # Count channels
    try:
        channels = await channel_repo.get_by_user(user.id)
        channel_count = len(channels)
    except Exception:
        channel_count = 0

    menu_text = (
        f"<b>👤 Личный кабинет</b>\n\n"
        f"📢 Каналов: {channel_count}\n"
        f"💎 Баланс: {user.balance} постов"
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
        HELP_MESSAGE,
        parse_mode="HTML",
        reply_markup=back_to_menu_keyboard(),
    )


# =============================================================================
# Start Screen Callbacks
# =============================================================================


@start_router.callback_query(lambda c: c.data == "start_check_sub")
async def callback_check_sub(callback: CallbackQuery) -> None:
    """Handle 'Check subscription' button - placeholder."""
    await callback.message.edit_text(
        "<b>🚧 В разработке</b>\n\n"
        "Функция проверки подписки будет доступна в ближайшее время.",
        parse_mode="HTML",
        reply_markup=back_to_menu_keyboard(),
    )
    await callback.answer()


# =============================================================================
# Menu Callbacks
# =============================================================================
# Note: start_setup_transfer and start_setup_autopost are handled by
# transfer_router and autopost_router respectively
# Note: menu_channels, menu_new_transfer, menu_new_autopost are handled by
# their respective routers (channels_router, transfer_router, autopost_router)


@start_router.callback_query(lambda c: c.data == "menu_balance")
async def callback_balance(callback: CallbackQuery, user_repo) -> None:
    """Handle 'Check balance' button - show balance."""
    user = await user_repo.get_by_telegram_id(callback.from_user.id)

    balance_text = f"<b>💎 Ваш баланс: {user.balance} постов</b>"

    await callback.message.edit_text(
        balance_text,
        parse_mode="HTML",
        reply_markup=back_to_menu_keyboard(),
    )
    await callback.answer()


@start_router.callback_query(lambda c: c.data == "menu_bonus")
async def callback_bonus(callback: CallbackQuery) -> None:
    """Handle 'Bonus posts' button - placeholder."""
    await callback.message.edit_text(
        "<b>🎁 Бонусные посты</b>\n\n"
        "🚧 В разработке\n\n"
        "Получите 10 бесплатных постов за подписку на канал!",
        parse_mode="HTML",
        reply_markup=back_to_menu_keyboard(),
    )
    await callback.answer()


@start_router.callback_query(lambda c: c.data == "menu_promo")
async def callback_promo(callback: CallbackQuery) -> None:
    """Handle 'Activate promo' button - placeholder."""
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
    await callback.message.edit_text(
        HELP_MESSAGE,
        parse_mode="HTML",
        reply_markup=back_to_menu_keyboard(),
    )
    await callback.answer()


# =============================================================================
# Navigation Callbacks
# =============================================================================


@start_router.callback_query(lambda c: c.data == "nav_goto_menu")
async def callback_goto_menu(callback: CallbackQuery, user_repo, channel_repo) -> None:
    """Handle 'Back to menu' navigation."""
    user = await user_repo.get_by_telegram_id(callback.from_user.id)

    if user is None:
        user, _ = await user_repo.get_or_create(callback.from_user.id)

    # Count channels
    try:
        channels = await channel_repo.get_by_user(user.id)
        channel_count = len(channels)
    except Exception:
        channel_count = 0

    menu_text = (
        f"<b>👤 Личный кабинет</b>\n\n"
        f"📢 Каналов: {channel_count}\n"
        f"💎 Баланс: {user.balance} постов"
    )

    await callback.message.edit_text(
        menu_text,
        parse_mode="HTML",
        reply_markup=menu_keyboard(),
    )
    await callback.answer()
