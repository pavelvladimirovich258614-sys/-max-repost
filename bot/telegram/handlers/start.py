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

# Welcome message
WELCOME_MESSAGE = """
<b>Добро пожаловать в Max-Repost Bot! 🚀</b>

Я помогу переносить посты из Telegram-каналов в мессенджер Max.

Выберите действие:
"""

# Help message
HELP_MESSAGE = """
<b>ℹ️ Помощь - Как использовать бота</b>

<b>📥 Настроить перенос</b> — подключите канал и переносите посты вручную

<b>🔄 Настроить автопостинг</b> — новые посты будут появляться в Max автоматически

<b>💎 Баланс</b> — 1 пост = 1 рубль. Пополните через Юкасса

<b>🎁 Бонус</b> — получите 10 бесплатных постов за подписку на канал

<b>🎟 Промокод</b> — активируйте промокод для получения бонусных постов

<b>📢 Мои каналы</b> — управляйте подключенными каналами

<i>По всем вопросам: @support</i>
"""


@start_router.message(Command("start"))
async def cmd_start(message: Message, user_repo) -> None:
    """
    Handle /start command.

    Register user if new, send welcome message with start keyboard.

    Args:
        message: Telegram message
        user_repo: User repository from middleware
    """
    # Register user (get or create)
    user, _ = await user_repo.get_or_create(message.from_user.id)

    await message.answer(
        WELCOME_MESSAGE,
        reply_markup=start_keyboard(),
    )


@start_router.message(Command("menu"))
async def cmd_menu(message: Message, user_repo) -> None:
    """
    Handle /menu command.

    Show personal cabinet with user stats.

    Args:
        message: Telegram message
        user_repo: User repository from middleware
    """
    user = await user_repo.get_by_telegram_id(message.from_user.id)

    if user is None:
        # Fallback to get_or_create if user not found
        user, _ = await user_repo.get_or_create(message.from_user.id)

    # Count channels (we'll use channel_repo when implemented)
    channel_count = 0  # Placeholder

    menu_text = (
        f"<b>👤 Личный кабинет</b>\n\n"
        f"📢 Каналов: {channel_count}\n"
        f"💎 Баланс: {user.balance} постов"
    )

    await message.answer(
        menu_text,
        reply_markup=menu_keyboard(),
    )


@start_router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """
    Handle /help command.

    Send help text with back button.

    Args:
        message: Telegram message
    """
    await message.answer(
        HELP_MESSAGE,
        reply_markup=back_to_menu_keyboard(),
    )


# Start screen callbacks
@start_router.callback_query(lambda c: c.data == "start_check_sub")
async def callback_check_sub(callback: CallbackQuery) -> None:
    """Handle 'Check subscription' button - placeholder."""
    await callback.message.edit_text(
        "<b>🚧 В разработке</b>",
        reply_markup=back_to_menu_keyboard(),
    )
    await callback.answer()


@start_router.callback_query(lambda c: c.data == "start_setup_transfer")
async def callback_setup_transfer(callback: CallbackQuery) -> None:
    """Handle 'Setup transfer' button - placeholder."""
    await callback.message.edit_text(
        "<b>🚧 В разработке</b>",
        reply_markup=back_to_menu_keyboard(),
    )
    await callback.answer()


@start_router.callback_query(lambda c: c.data == "start_setup_autopost")
async def callback_setup_autopost(callback: CallbackQuery) -> None:
    """Handle 'Setup auto-posting' button - placeholder."""
    await callback.message.edit_text(
        "<b>🚧 В разработке</b>",
        reply_markup=back_to_menu_keyboard(),
    )
    await callback.answer()


# Menu callbacks
@start_router.callback_query(lambda c: c.data == "menu_channels")
async def callback_channels(callback: CallbackQuery) -> None:
    """Handle 'My channels' button - placeholder."""
    await callback.message.edit_text(
        "<b>🚧 В разработке</b>",
        reply_markup=back_to_menu_keyboard(),
    )
    await callback.answer()


@start_router.callback_query(lambda c: c.data == "menu_balance")
async def callback_balance(callback: CallbackQuery, user_repo) -> None:
    """Handle 'Check balance' button - show balance."""
    user = await user_repo.get_by_telegram_id(callback.from_user.id)

    balance_text = f"<b>💎 Ваш баланс: {user.balance} постов</b>"

    await callback.message.edit_text(
        balance_text,
        reply_markup=back_to_menu_keyboard(),
    )
    await callback.answer()


@start_router.callback_query(lambda c: c.data == "menu_bonus")
async def callback_bonus(callback: CallbackQuery) -> None:
    """Handle 'Bonus posts' button - placeholder."""
    await callback.message.edit_text(
        "<b>🚧 В разработке</b>",
        reply_markup=back_to_menu_keyboard(),
    )
    await callback.answer()


@start_router.callback_query(lambda c: c.data == "menu_promo")
async def callback_promo(callback: CallbackQuery) -> None:
    """Handle 'Activate promo' button - placeholder."""
    await callback.message.edit_text(
        "<b>🚧 В разработке</b>",
        reply_markup=back_to_menu_keyboard(),
    )
    await callback.answer()


@start_router.callback_query(lambda c: c.data == "menu_help")
async def callback_help(callback: CallbackQuery) -> None:
    """Handle 'Help' button - same as /help."""
    await callback.message.edit_text(
        HELP_MESSAGE,
        reply_markup=back_to_menu_keyboard(),
    )
    await callback.answer()


# Navigation callbacks
@start_router.callback_query(lambda c: c.data == "nav_goto_menu")
async def callback_goto_menu(callback: CallbackQuery, user_repo) -> None:
    """Handle 'Back to menu' navigation."""
    user = await user_repo.get_by_telegram_id(callback.from_user.id)

    channel_count = 0  # Placeholder
    menu_text = (
        f"<b>👤 Личный кабинет</b>\n\n"
        f"📢 Каналов: {channel_count}\n"
        f"💎 Баланс: {user.balance} постов"
    )

    await callback.message.edit_text(
        menu_text,
        reply_markup=menu_keyboard(),
    )
    await callback.answer()
