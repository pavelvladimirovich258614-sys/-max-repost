"""Start router with /start, /menu, /help handlers and navigation callbacks."""

import asyncio
from decimal import Decimal

from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from loguru import logger

from bot.telegram.keyboards.main import (
    start_keyboard,
    menu_keyboard,
    back_to_menu_keyboard,
    balance_keyboard,
)
from bot.database.repositories.balance import UserBalanceRepository, BalanceTransactionRepository
from bot.database.repositories.autopost_subscription import AutopostSubscriptionRepository
from config.settings import settings

# Referral bonus amount in rubles
REFERRAL_BONUS = Decimal("10.00")

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
async def cmd_start(
    message: Message,
    user_repo,
    balance_repo: UserBalanceRepository,
    transaction_repo: BalanceTransactionRepository,
    bot: Bot,
) -> None:
    """
    Handle /start command.

    Register user if new, send welcome sticker and message with start keyboard.
    First-time users see the welcome with 3 main actions.
    
    Also handles referral codes via deep linking: /start ref_ABC12345

    Args:
        message: Telegram message
        user_repo: User repository from middleware
        balance_repo: Balance repository for referral bonuses
        transaction_repo: Transaction repository for recording bonuses
        bot: Bot instance for sending notifications
    """
    # Get command arguments (referral code)
    command_args = message.text.split() if message.text else []
    referral_code = None
    
    if len(command_args) > 1 and command_args[1].startswith("ref_"):
        referral_code = command_args[1][4:]  # Remove "ref_" prefix
    
    # Register user (get or create)
    user, is_new = await user_repo.get_or_create(message.from_user.id)
    
    # Handle referral if new user and valid referral code
    if is_new and referral_code:
        await _process_referral(
            user, 
            referral_code, 
            user_repo, 
            balance_repo, 
            transaction_repo, 
            bot
        )
    
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


async def _process_referral(
    new_user,
    referral_code: str,
    user_repo,
    balance_repo: UserBalanceRepository,
    transaction_repo: BalanceTransactionRepository,
    bot: Bot,
) -> None:
    """
    Process referral registration.
    
    Args:
        new_user: The newly registered user
        referral_code: Referral code from the start command
        user_repo: User repository
        balance_repo: Balance repository
        transaction_repo: Transaction repository
        bot: Bot instance
    """
    try:
        # Find referrer by code
        referrer = await user_repo.get_by_referral_code(referral_code)
        
        if referrer is None:
            logger.info(f"Invalid referral code: {referral_code}")
            return
        
        # Check: cannot refer yourself
        if referrer.telegram_id == new_user.telegram_id:
            logger.info(f"User {new_user.telegram_id} tried to refer themselves")
            return
        
        # Check: referred_by can only be set once
        if new_user.referred_by is not None:
            logger.info(f"User {new_user.telegram_id} already has a referrer")
            return
        
        # Set referred_by
        updated_user = await user_repo.set_referred_by(new_user.id, referrer.telegram_id)
        if updated_user is None:
            logger.warning(f"Could not set referrer for user {new_user.telegram_id}")
            return
        
        # Award bonus to new user
        new_user_balance, _ = await balance_repo.get_or_create(new_user.telegram_id)
        await balance_repo.update_balance(
            new_user.telegram_id,
            REFERRAL_BONUS,
            is_deposit=True,
        )
        await transaction_repo.create_transaction(
            user_id=new_user.telegram_id,
            amount=REFERRAL_BONUS,
            transaction_type="referral_bonus",
            description=f"Бонус за регистрацию по реферальной ссылке",
        )
        
        # Award bonus to referrer
        referrer_balance, _ = await balance_repo.get_or_create(referrer.telegram_id)
        await balance_repo.update_balance(
            referrer.telegram_id,
            REFERRAL_BONUS,
            is_deposit=True,
        )
        await transaction_repo.create_transaction(
            user_id=referrer.telegram_id,
            amount=REFERRAL_BONUS,
            transaction_type="referral_bonus",
            description=f"Бонус за приглашение пользователя #{new_user.telegram_id}",
        )
        
        # Notify referrer
        try:
            await bot.send_message(
                referrer.telegram_id,
                "🎉 По вашей ссылке зарегистрировался новый пользователь! +10₽",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"Could not notify referrer {referrer.telegram_id}: {e}")
        
        logger.info(
            f"Referral processed: {new_user.telegram_id} referred by {referrer.telegram_id}"
        )
        
    except Exception as e:
        logger.error(f"Error processing referral: {e}")


@start_router.message(Command("menu"))
async def cmd_menu(
    message: Message,
    user_repo,
    channel_repo,
    verified_channel_repo,
    transferred_post_repo,
    autopost_sub_repo: AutopostSubscriptionRepository,
    balance_repo: UserBalanceRepository,
) -> None:
    """
    Handle /menu command.

    Show personal cabinet with user stats and full menu.
    Returning users see the menu with all options.

    Args:
        message: Telegram message
        user_repo: User repository from counting channels
        verified_channel_repo: Repository for verified channels
        transferred_post_repo: Repository for transferred posts
        autopost_sub_repo: Repository for autopost subscriptions
        balance_repo: Repository for user balance
    """
    user = await user_repo.get_by_telegram_id(message.from_user.id)

    if user is None:
        # Fallback to get_or_create if user not found
        user, _ = await user_repo.get_or_create(message.from_user.id)

    # Get stats
    verified_count = 0
    active_autopost_count = 0
    
    try:
        # Count verified channels
        verified_channels = await verified_channel_repo.get_user_verified_channels(message.from_user.id)
        verified_count = len(verified_channels)
    except Exception as e:
        logger.debug(f"Could not get verified channels count: {e}")
    
    try:
        # Count active autopost subscriptions
        subscriptions = await autopost_sub_repo.get_user_subscriptions(message.from_user.id)
        active_autopost_count = len([s for s in subscriptions if s.is_active])
    except Exception as e:
        logger.debug(f"Could not get autopost subscriptions count: {e}")
    
    # Check if user is admin
    is_admin = message.from_user.id in settings.ADMIN_IDS
    
    if is_admin:
        # Admin view
        menu_text = (
            f"<b>👤 Личный кабинет (👑 Админ)</b>\n\n"
            f"📢 Каналов: {verified_count}\n"
            f"♾️ Безлимитный перенос и автопостинг\n"
            f"⚡ Автопостингов: {active_autopost_count}"
        )
    else:
        # Regular user view
        # Get balance
        balance = 0
        try:
            user_balance, _ = await balance_repo.get_or_create(message.from_user.id)
            balance = int(user_balance.balance)
        except Exception as e:
            logger.debug(f"Could not get user balance: {e}")
        
        menu_text = (
            f"<b>👤 Личный кабинет</b>\n\n"
            f"📢 Каналов: {verified_count}\n"
            f"💰 Баланс: {balance}₽\n"
            f"⚡ Автопостингов: {active_autopost_count}"
        )

    await message.answer(
        menu_text,
        parse_mode="HTML",
        reply_markup=menu_keyboard(is_admin=is_admin),
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
    user_balance, _ = await balance_repo.get_or_create(user_id)
    
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


@start_router.callback_query(lambda c: c.data == "menu_referral")
async def callback_referral(
    callback: CallbackQuery,
    user_repo,
    balance_repo: UserBalanceRepository,
    transaction_repo: BalanceTransactionRepository,
) -> None:
    """
    Handle 'Invite friend' button - show referral info.
    
    Displays referral link, invited count, and earned amount.
    """
    # Answer callback FIRST before any async operations
    await callback.answer("⏳")
    
    user = await user_repo.get_by_telegram_id(callback.from_user.id)
    
    if user is None or user.referral_code is None:
        # Generate code if missing
        if user is not None:
            referral_code = await user_repo._generate_unique_referral_code()
            await user_repo.set_referral_code(user.id, referral_code)
            user.referral_code = referral_code
        else:
            await callback.message.edit_text(
                "Ошибка загрузки данных пользователя.",
                reply_markup=back_to_menu_keyboard(),
            )
            return
    
    # Get referral stats
    referral_count = await user_repo.count_referrals(callback.from_user.id)
    referral_earnings = await transaction_repo.get_referral_earnings(callback.from_user.id)
    
    # Build referral text
    referral_link = f"https://t.me/{TG_BOT_USERNAME}?start=ref_{user.referral_code}"
    
    referral_text = (
        f"<b>👥 Пригласить друга</b>\n\n"
        f"<b>Ваша реферальная ссылка:</b>\n"
        f"<code>{referral_link}</code>\n\n"
        f"Приглашено: {referral_count} чел.\n"
        f"Заработано: {int(referral_earnings)}₽\n\n"
        f"<i>За каждого приглашённого друга вы получаете +10₽, друг тоже получает +10₽!</i>"
    )
    
    await callback.message.edit_text(
        referral_text,
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
    autopost_sub_repo: AutopostSubscriptionRepository,
    balance_repo: UserBalanceRepository,
) -> None:
    """Handle 'Back to menu' navigation."""
    # Answer callback FIRST before any async operations
    await callback.answer("⏳")
    
    user = await user_repo.get_by_telegram_id(callback.from_user.id)

    if user is None:
        user, _ = await user_repo.get_or_create(callback.from_user.id)

    # Get stats
    verified_count = 0
    active_autopost_count = 0
    
    try:
        # Count verified channels
        verified_channels = await verified_channel_repo.get_user_verified_channels(callback.from_user.id)
        verified_count = len(verified_channels)
    except Exception as e:
        logger.debug(f"Could not get verified channels count: {e}")
    
    try:
        # Count active autopost subscriptions
        subscriptions = await autopost_sub_repo.get_user_subscriptions(callback.from_user.id)
        active_autopost_count = len([s for s in subscriptions if s.is_active])
    except Exception as e:
        logger.debug(f"Could not get autopost subscriptions count: {e}")
    
    # Check if user is admin
    is_admin = callback.from_user.id in settings.ADMIN_IDS
    
    if is_admin:
        # Admin view
        menu_text = (
            f"<b>👤 Личный кабинет (👑 Админ)</b>\n\n"
            f"📢 Каналов: {verified_count}\n"
            f"♾️ Безлимитный перенос и автопостинг\n"
            f"⚡ Автопостингов: {active_autopost_count}"
        )
    else:
        # Regular user view
        # Get balance
        balance = 0
        try:
            user_balance, _ = await balance_repo.get_or_create(callback.from_user.id)
            balance = int(user_balance.balance)
        except Exception as e:
            logger.debug(f"Could not get user balance: {e}")
        
        menu_text = (
            f"<b>👤 Личный кабинет</b>\n\n"
            f"📢 Каналов: {verified_count}\n"
            f"💰 Баланс: {balance}₽\n"
            f"⚡ Автопостингов: {active_autopost_count}"
        )

    await callback.message.edit_text(
        menu_text,
        parse_mode="HTML",
        reply_markup=menu_keyboard(is_admin=is_admin),
    )
