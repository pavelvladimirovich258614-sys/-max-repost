"""Admin panel handlers."""

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from loguru import logger

from bot.telegram.keyboards.admin import (
    admin_main_keyboard,
    admin_stats_keyboard,
    admin_users_keyboard,
    admin_finance_keyboard,
)
from bot.telegram.keyboards.main import menu_keyboard
from bot.telegram.states import AdminStates
from bot.database.repositories.user import UserRepository
from bot.database.repositories.autopost_subscription import AutopostSubscriptionRepository
from bot.database.repositories.transferred_post import TransferredPostRepository
from bot.database.repositories.yookassa_payment import YooKassaPaymentRepository
from bot.database.balance import admin_add_balance
from config.settings import settings

# Create router
admin_router = Router(name="admin")

# Constants
USERS_PER_PAGE = 10


def is_admin(telegram_id: int) -> bool:
    """
    Check if user is admin.

    Args:
        telegram_id: Telegram user ID

    Returns:
        True if user is admin, False otherwise
    """
    return telegram_id == settings.admin_telegram_id


# =============================================================================
# Commands
# =============================================================================


@admin_router.message(Command("admin"))
async def cmd_admin(
    message: Message,
    user_repo: UserRepository,
) -> None:
    """
    Handle /admin command.

    Shows admin panel for authorized users only.

    Args:
        message: Telegram message
        user_repo: User repository from middleware
    """
    # Check admin rights
    if not is_admin(message.from_user.id):
        await message.answer(
            "❌ У вас нет доступа к админ-панели.",
            reply_markup=menu_keyboard(),
        )
        return

    await message.answer(
        "<b>👑 Админ-панель</b>\n\nВыберите раздел:",
        parse_mode="HTML",
        reply_markup=admin_main_keyboard(),
    )


@admin_router.message(Command("addbalance"))
async def cmd_addbalance(message: Message) -> None:
    """
    Handle /addbalance command.

    Prompts admin for user_id and amount.

    Args:
        message: Telegram message
    """
    # Check admin rights
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав для этой команды.")
        return

    await message.answer(
        "💳 <b>Начисление баланса</b>\n\n"
        "Введите <code>telegram_id</code> и сумму через пробел.\n\n"
        "Пример: <code>7707646318 50</code>\n\n"
        "Для отмены введите /cancel",
        parse_mode="HTML",
    )
    await AdminStates.waiting_add_balance_input.set()


# =============================================================================
# Main Admin Menu Callbacks
# =============================================================================


@admin_router.callback_query(lambda c: c.data == "admin_main")
async def callback_admin_main(callback: CallbackQuery) -> None:
    """
    Handle 'Back to admin main' callback.

    Shows main admin menu.
    """
    # Answer callback immediately to prevent timeout
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass

    # Check admin rights
    if not is_admin(callback.from_user.id):
        return

    await callback.message.edit_text(
        "<b>👑 Админ-панель</b>\n\nВыберите раздел:",
        parse_mode="HTML",
        reply_markup=admin_main_keyboard(),
    )


@admin_router.callback_query(lambda c: c.data == "admin_add_balance")
async def callback_admin_add_balance(callback: CallbackQuery) -> None:
    """
    Handle 'Add Balance' callback from admin panel.

    Prompts for user_id and amount.
    """
    # Check admin rights
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return

    try:
        await callback.answer()
    except TelegramBadRequest:
        pass

    await callback.message.edit_text(
        "💳 <b>Начисление баланса</b>\n\n"
        "Введите <code>telegram_id</code> и сумму через пробел.\n\n"
        "Пример: <code>7707646318 50</code>\n\n"
        "Для отмены введите /cancel",
        parse_mode="HTML",
    )
    await AdminStates.waiting_add_balance_input.set()


@admin_router.message(AdminStates.waiting_add_balance_input)
async def process_add_balance_input(
    message: Message,
    user_repo: UserRepository,
    autopost_sub_repo: AutopostSubscriptionRepository,
) -> None:
    """
    Process balance top-up input.

    Expects format: "telegram_id amount"

    Args:
        message: Telegram message
        user_repo: User repository
        autopost_sub_repo: Autopost subscription repository
    """
    # Check admin rights
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав для этой команды.")
        await AdminStates.waiting_add_balance_input.reset()
        return

    text = message.text.strip()

    # Try to parse "user_id amount"
    parts = text.split()
    if len(parts) != 2:
        await message.answer(
            "❌ Неверный формат. Введите два числа через пробел:\n"
            "<code>telegram_id сумма</code>\n\n"
            "Пример: <code>7707646318 50</code>",
            parse_mode="HTML",
        )
        return

    try:
        target_user_id = int(parts[0])
        amount = int(parts[1])
    except ValueError:
        await message.answer(
            "❌ Неверный формат. telegram_id и сумма должны быть числами.\n\n"
            "Пример: <code>7707646318 50</code>",
            parse_mode="HTML",
        )
        return

    if amount <= 0:
        await message.answer("❌ Сумма должна быть положительным числом.")
        return

    # Check if target user exists
    target_user = await user_repo.get_by_telegram_id(target_user_id)
    if target_user is None:
        await message.answer(f"❌ Пользователь с telegram_id={target_user_id} не найден.")
        await AdminStates.waiting_add_balance_input.reset()
        return

    # Get old balance
    old_balance = int(target_user.balance)

    # Add balance using admin_add_balance (creates transaction record)
    new_balance = await admin_add_balance(
        user_repo._session,
        target_user_id,
        amount,
        description=f"Admin top-up by {message.from_user.id}",
    )

    if new_balance is None:
        await message.answer("❌ Ошибка при начислении баланса.")
        await AdminStates.waiting_add_balance_input.reset()
        return

    # Check if user has paused subscriptions due to insufficient funds and resume them
    paused_subs = await autopost_sub_repo.get_user_subscriptions(target_user_id)
    resumed_count = 0
    for sub in paused_subs:
        if sub.paused_reason == "insufficient_funds":
            await autopost_sub_repo.resume_subscription(sub.id)
            resumed_count += 1
            logger.info(f"Resumed autopost subscription {sub.id} for user {target_user_id} after balance top-up")

    # Build response message
    response = (
        f"✅ Начислено {amount}₽ пользователю {target_user_id}\n"
        f"💰 Баланс: {old_balance}₽ → {new_balance}₽"
    )

    if resumed_count > 0:
        response += f"\n\n🔄 Возобновлено подписок: {resumed_count}"

    await message.answer(
        response,
        reply_markup=admin_main_keyboard(),
    )
    await AdminStates.waiting_add_balance_input.reset()

    # Notify user about balance top-up
    try:
        await message.bot.send_message(
            target_user_id,
            f"💰 Вам начислено {amount}₽!\n"
            f"Текущий баланс: {new_balance}₽\n\n"
            f"Автоподписки возобновлены."
        )
    except Exception as e:
        logger.warning(f"Could not notify user {target_user_id} about balance top-up: {e}")


# =============================================================================
# Statistics Callback
# =============================================================================


@admin_router.callback_query(lambda c: c.data == "admin_stats")
async def callback_admin_stats(
    callback: CallbackQuery,
    user_repo: UserRepository,
    autopost_sub_repo: AutopostSubscriptionRepository,
    transferred_post_repo: TransferredPostRepository,
) -> None:
    """
    Handle 'Statistics' callback.

    Shows bot statistics.
    """
    # Check admin rights
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return

    # Answer callback immediately with loading indicator
    try:
        await callback.answer("⏳")
    except TelegramBadRequest:
        pass

    try:
        # Get statistics
        total_users = await user_repo.count_all()
        active_subs = await autopost_sub_repo.count_active()

        # Count posts transferred today
        from datetime import datetime, timedelta
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

        # Get total transferred posts
        total_posts = await transferred_post_repo.count()

        # Note: transferred_post_repo doesn't have count_since method
        # We'll use a simple query via the session
        from sqlalchemy import select, func
        from bot.database.models import TransferredPost

        stmt = select(func.count(TransferredPost.id)).where(
            TransferredPost.transferred_at >= today_start
        )
        result = await transferred_post_repo._session.execute(stmt)
        posts_today = result.scalar() or 0

        stats_text = (
            f"<b>📊 Статистика бота</b>\n\n"
            f"👥 Всего пользователей: {total_users}\n"
            f"⚡ Активных подписок: {active_subs}\n"
            f"📤 Постов перенесено сегодня: {posts_today}\n"
            f"📤 Постов перенесено всего: {total_posts}"
        )

        await callback.message.edit_text(
            stats_text,
            parse_mode="HTML",
            reply_markup=admin_stats_keyboard(),
        )
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        await callback.message.edit_text(
            "❌ Ошибка при получении статистики",
            reply_markup=admin_stats_keyboard(),
        )


# =============================================================================
# Users Callback
# =============================================================================


@admin_router.callback_query(lambda c: c.data == "admin_users")
async def callback_admin_users(
    callback: CallbackQuery,
    user_repo: UserRepository,
    autopost_sub_repo: AutopostSubscriptionRepository,
) -> None:
    """
    Handle 'Users' callback.

    Shows paginated list of users.
    """
    # Check admin rights
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return

    # Answer callback immediately with loading indicator
    try:
        await callback.answer("⏳")
    except TelegramBadRequest:
        pass
    await _show_users_page(callback, user_repo, autopost_sub_repo, page=1)


@admin_router.callback_query(lambda c: c.data and c.data.startswith("admin_users_page:"))
async def callback_admin_users_page(
    callback: CallbackQuery,
    user_repo: UserRepository,
    autopost_sub_repo: AutopostSubscriptionRepository,
) -> None:
    """
    Handle users pagination callback.

    Shows specific page of users list.
    """
    # Answer callback immediately with loading indicator
    try:
        await callback.answer("⏳")
    except TelegramBadRequest:
        pass

    # Check admin rights
    if not is_admin(callback.from_user.id):
        return

    # Parse page number
    try:
        page = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        page = 1
    await _show_users_page(callback, user_repo, autopost_sub_repo, page=page)


async def _show_users_page(
    callback: CallbackQuery,
    user_repo: UserRepository,
    autopost_sub_repo: AutopostSubscriptionRepository,
    page: int,
) -> None:
    """
    Show users list for specific page.

    Args:
        callback: Telegram callback query
        user_repo: User repository
        autopost_sub_repo: Autopost subscription repository
        page: Page number (1-based)
    """
    try:
        # Get total count for pagination
        total_users = await user_repo.count_all()
        total_pages = (total_users + USERS_PER_PAGE - 1) // USERS_PER_PAGE

        # Ensure valid page
        if page < 1:
            page = 1
        if page > total_pages:
            page = max(1, total_pages)

        # Get users for current page
        users = await user_repo.get_all_paginated(page=page, per_page=USERS_PER_PAGE)

        # Build users list text
        lines = ["<b>👥 Пользователи</b>\n"]

        for user in users:
            # Count user subscriptions
            subs_count = await autopost_sub_repo.count_by_user(user.telegram_id)
            balance = int(user.balance)
            lines.append(
                f"{user.telegram_id} | Баланс: {balance}₽ | Подписок: {subs_count}"
            )

        if not users:
            lines.append("\n<i>Нет пользователей</i>")

        users_text = "\n".join(lines)

        await callback.message.edit_text(
            users_text,
            parse_mode="HTML",
            reply_markup=admin_users_keyboard(page=page, total_pages=total_pages),
        )
    except Exception as e:
        logger.error(f"Error getting users: {e}")
        await callback.message.edit_text(
            "❌ Ошибка при получении списка пользователей",
            reply_markup=admin_users_keyboard(page=1, total_pages=1),
        )


# =============================================================================
# Finance Callback
# =============================================================================


@admin_router.callback_query(lambda c: c.data == "admin_finance")
async def callback_admin_finance(
    callback: CallbackQuery,
    yookassa_payment_repo,
) -> None:
    """
    Handle 'Finances' callback.

    Shows financial summary.
    """
    # Check admin rights
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return

    # Answer callback immediately with loading indicator
    try:
        await callback.answer("⏳")
    except TelegramBadRequest:
        pass

    try:
        # Get financial statistics
        total_income = await yookassa_payment_repo.sum_succeeded()
        today_income = await yookassa_payment_repo.sum_by_period(days=1)
        week_income = await yookassa_payment_repo.sum_by_period(days=7)
        month_income = await yookassa_payment_repo.sum_by_period(days=30)
        pending_count = await yookassa_payment_repo.count_pending()

        finance_text = (
            f"<b>💰 Финансовая сводка</b>\n\n"
            f"💵 Общий доход: {int(total_income)}₽\n"
            f"💵 Доход за сегодня: {int(today_income)}₽\n"
            f"💵 Доход за 7 дней: {int(week_income)}₽\n"
            f"💵 Доход за 30 дней: {int(month_income)}₽\n"
            f"⏳ Pending платежей: {pending_count}"
        )

        await callback.message.edit_text(
            finance_text,
            parse_mode="HTML",
            reply_markup=admin_finance_keyboard(),
        )
    except Exception as e:
        logger.error(f"Error getting finance stats: {e}")
        await callback.message.edit_text(
            "❌ Ошибка при получении финансовой сводки",
            reply_markup=admin_finance_keyboard(),
        )


# =============================================================================
# Ignore Callback (for pagination placeholders)
# =============================================================================


@admin_router.callback_query(lambda c: c.data == "admin_ignore")
async def callback_admin_ignore(callback: CallbackQuery) -> None:
    """
    Handle ignore callback (for disabled pagination buttons).
    """
    # Answer callback immediately to prevent timeout
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass
