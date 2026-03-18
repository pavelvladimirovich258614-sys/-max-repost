"""Payment handlers for YooKassa integration."""

from decimal import Decimal

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from loguru import logger

from bot.payments.yookassa_client import YooKassaClient
from bot.database.repositories.yookassa_payment import YooKassaPaymentRepository
from bot.database.repositories.balance import UserBalanceRepository, BalanceTransactionRepository
from bot.database.repositories.user import UserRepository
from bot.telegram.keyboards.payment import (
    payment_amount_keyboard,
    payment_confirmation_keyboard,
    payment_history_keyboard,
    back_to_balance_keyboard,
    email_confirm_keyboard,
    email_input_keyboard,
)

payment_router = Router(name="payment")

# Initialize YooKassa client
yookassa_client = YooKassaClient()


class PaymentStates(StatesGroup):
    """FSM states for payment flow."""
    waiting_for_amount = State()
    waiting_email = State()  # NEW: for email input


# =============================================================================
# Deposit Entry Point
# =============================================================================

@payment_router.callback_query(lambda c: c.data == "balance_deposit")
async def callback_deposit(callback: CallbackQuery) -> None:
    """Handle 'Deposit' button - show amount selection."""
    await callback.answer()
    
    await callback.message.edit_text(
        "<b>💰 Пополнение баланса</b>\n\n"
        "Выберите сумму для пополнения:\n"
        "• 100₽ — примерно 33 поста\n"
        "• 300₽ — примерно 100 постов\n"
        "• 500₽ — примерно 166 постов\n"
        "• 1000₽ — примерно 333 поста\n\n"
        "<i>Стоимость 1 поста = 3₽</i>",
        parse_mode="HTML",
        reply_markup=payment_amount_keyboard(),
    )


# =============================================================================
# Fixed Amount Payment
# =============================================================================

@payment_router.callback_query(lambda c: c.data.startswith("pay_amount:"))
async def callback_pay_amount(
    callback: CallbackQuery,
    state: FSMContext,
    user_repo: UserRepository,
) -> None:
    """Handle fixed amount payment selection."""
    await callback.answer()
    
    # Parse amount
    amount_str = callback.data.split(":")[1]
    amount = Decimal(amount_str)
    
    user_id = callback.from_user.id
    
    # Save amount to FSM
    await state.update_data(amount=amount)
    
    # Check if user has email
    email = await user_repo.get_email(user_id)
    
    if email:
        # Show confirmation keyboard with existing email
        await callback.message.edit_text(
            f"<b>📧 Email для чека</b>\n\n"
            f"Для отправки чека по 54-ФЗ используем email:\n"
            f"<code>{email}</code>\n\n"
            f"Хотите продолжить с этим email?",
            parse_mode="HTML",
            reply_markup=email_confirm_keyboard(email),
        )
    else:
        # Ask for email input
        await callback.message.edit_text(
            "<b>📧 Введите email для чека</b>\n\n"
            "По закону 54-ФЗ нам нужен ваш email для отправки чека.\n"
            "Введите ваш email адрес:",
            parse_mode="HTML",
            reply_markup=email_input_keyboard(),
        )
        await state.set_state(PaymentStates.waiting_email)


# =============================================================================
# Custom Amount Input
# =============================================================================

@payment_router.callback_query(lambda c: c.data == "pay_custom")
async def callback_pay_custom(callback: CallbackQuery, state: FSMContext) -> None:
    """Handle custom amount selection - ask for input."""
    await callback.answer()
    
    await callback.message.edit_text(
        "<b>✏️ Введите сумму</b>\n\n"
        "Введите сумму пополнения в рублях (только число):\n"
        "• Минимум: 10₽\n"
        "• Максимум: 10000₽\n\n"
        "<i>Например: 250</i>",
        parse_mode="HTML",
    )
    
    await state.set_state(PaymentStates.waiting_for_amount)


@payment_router.message(PaymentStates.waiting_for_amount)
async def process_custom_amount(
    message: Message,
    state: FSMContext,
    user_repo: UserRepository,
) -> None:
    """Process custom amount input."""
    # Validate input
    text = message.text.strip()
    
    try:
        amount = Decimal(text)
    except:
        await message.answer(
            "❌ Введите только число (например: 250)",
        )
        return
    
    # Check limits
    if amount < 10:
        await message.answer(
            "❌ Минимальная сумма — 10₽",
        )
        return
    
    if amount > 10000:
        await message.answer(
            "❌ Максимальная сумма — 10000₽",
        )
        return
    
    user_id = message.from_user.id
    
    # Save amount to FSM
    await state.update_data(amount=amount)
    
    # Check if user has email
    email = await user_repo.get_email(user_id)
    
    if email:
        # Show confirmation keyboard with existing email
        await message.answer(
            f"<b>📧 Email для чека</b>\n\n"
            f"Для отправки чека по 54-ФЗ используем email:\n"
            f"<code>{email}</code>\n\n"
            f"Хотите продолжить с этим email?",
            parse_mode="HTML",
            reply_markup=email_confirm_keyboard(email),
        )
    else:
        # Ask for email input
        await message.answer(
            "<b>📧 Введите email для чека</b>\n\n"
            "По закону 54-ФЗ нам нужен ваш email для отправки чека.\n"
            "Введите ваш email адрес:",
            parse_mode="HTML",
            reply_markup=email_input_keyboard(),
        )
        await state.set_state(PaymentStates.waiting_email)


@payment_router.message(PaymentStates.waiting_email)
async def process_email_input(
    message: Message,
    state: FSMContext,
    user_repo: UserRepository,
    yookassa_payment_repo: YooKassaPaymentRepository,
) -> None:
    """Process email input."""
    email = message.text.strip()
    
    # Validate email with regex
    import re
    if not re.match(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$', email):
        await message.answer("❌ Некорректный email. Попробуйте ещё раз.")
        return
    
    user_id = message.from_user.id
    await user_repo.set_email(user_id, email)
    
    # Get amount from FSM
    data = await state.get_data()
    amount = data.get("amount")
    
    await state.clear()
    
    # Proceed to payment creation
    await create_payment_and_show(message, user_id, amount, email, yookassa_payment_repo)


async def create_payment_and_show(
    message_or_callback: Message | CallbackQuery,
    user_id: int,
    amount: Decimal,
    email: str,
    yookassa_payment_repo: YooKassaPaymentRepository,
) -> None:
    """Create payment and show confirmation."""
    # Determine the reply function based on message type
    if isinstance(message_or_callback, Message):
        reply_func = message_or_callback.answer
    else:
        reply_func = lambda text, **kwargs: message_or_callback.message.edit_text(text, **kwargs)
    
    # Create payment in YooKassa with email
    payment_id, confirmation_url = await yookassa_client.create_payment(
        user_id=user_id,
        amount_rub=amount,
        description=f"Пополнение баланса на {amount}₽",
        email=email,
    )
    
    if not payment_id or not confirmation_url:
        await reply_func(
            "❌ <b>Ошибка создания платежа</b>\n\n"
            "Не удалось создать платёж. Попробуйте позже.",
            parse_mode="HTML",
            reply_markup=back_to_balance_keyboard(),
        )
        return
    
    # Save to database
    await yookassa_payment_repo.create_payment(
        user_id=user_id,
        payment_id=payment_id,
        amount=amount,
    )
    
    # Show payment instructions
    await reply_func(
        f"<b>💳 Оплата {amount}₽</b>\n\n"
        f"Email для чека: <code>{email}</code>\n\n"
        f"Нажмите кнопку «Оплатить» ниже для перехода к оплате.\n\n"
        f"После оплаты вернитесь в бот и нажмите «Проверить оплату».\n\n"
        f"<i>Платёж действителен 24 часа</i>",
        parse_mode="HTML",
        reply_markup=payment_confirmation_keyboard(payment_id, confirmation_url),
    )


# =============================================================================
# Email Confirmation Callbacks
# =============================================================================

@payment_router.callback_query(lambda c: c.data == "email_confirm")
async def callback_email_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    user_repo: UserRepository,
    yookassa_payment_repo: YooKassaPaymentRepository,
) -> None:
    """Handle email confirmation - proceed with saved email."""
    await callback.answer()
    
    user_id = callback.from_user.id
    email = await user_repo.get_email(user_id)
    
    if not email:
        await callback.message.edit_text(
            "❌ Email не найден. Пожалуйста, введите email.",
            reply_markup=email_input_keyboard(),
        )
        await state.set_state(PaymentStates.waiting_email)
        return
    
    # Get amount from FSM
    data = await state.get_data()
    amount = data.get("amount")
    
    if not amount:
        await callback.message.edit_text(
            "❌ Ошибка: сумма не найдена. Начните заново.",
            reply_markup=back_to_balance_keyboard(),
        )
        await state.clear()
        return
    
    await state.clear()
    
    # Proceed to payment creation
    await create_payment_and_show(callback, user_id, amount, email, yookassa_payment_repo)


@payment_router.callback_query(lambda c: c.data == "email_change")
async def callback_email_change(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Handle email change - ask for new email."""
    await callback.answer()
    
    await callback.message.edit_text(
        "<b>📧 Введите новый email</b>\n\n"
        "Введите новый email адрес для отправки чека:",
        parse_mode="HTML",
        reply_markup=email_input_keyboard(),
    )
    await state.set_state(PaymentStates.waiting_email)


@payment_router.callback_query(lambda c: c.data == "email_cancel")
async def callback_email_cancel(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Handle email cancellation - abort payment flow."""
    await callback.answer()
    
    await state.clear()
    
    await callback.message.edit_text(
        "❌ Оплата отменена.",
        reply_markup=back_to_balance_keyboard(),
    )


# =============================================================================
# Check Payment Status
# =============================================================================

@payment_router.callback_query(lambda c: c.data.startswith("pay_check:"))
async def callback_check_payment(
    callback: CallbackQuery,
    yookassa_payment_repo: YooKassaPaymentRepository,
    balance_repo: UserBalanceRepository,
    transaction_repo: BalanceTransactionRepository,
) -> None:
    """Check payment status."""
    await callback.answer("⏳ Проверяю оплату...")
    
    payment_id = callback.data.split(":")[1]
    
    # Get payment from DB
    payment = await yookassa_payment_repo.get_by_payment_id(payment_id)
    if not payment:
        await callback.answer("Платёж не найден", show_alert=True)
        await callback.message.edit_text(
            "❌ Платёж не найден",
            reply_markup=back_to_balance_keyboard(),
        )
        return
    
    # Check status in YooKassa
    status = yookassa_client.check_payment(payment_id)
    
    if status == "succeeded":
        # Payment successful
        if payment.status != "succeeded":
            # Update payment status
            await yookassa_payment_repo.update_status(payment_id, "succeeded")
            
            # Add balance
            await balance_repo.update_balance(
                user_id=payment.user_id,
                amount=payment.amount,
                is_deposit=True,
            )
            
            # Create transaction record
            await transaction_repo.create_transaction(
                user_id=payment.user_id,
                amount=payment.amount,
                transaction_type="deposit",
                description=f"Пополнение через YooKassa (платёж {payment_id})",
            )
            
            # Get new balance
            user_balance = await balance_repo.get_by_user_id(payment.user_id)
            new_balance = int(user_balance.balance) if user_balance else 0
            
            await callback.message.edit_text(
                f"✅ <b>Оплата прошла успешно!</b>\n\n"
                f"Сумма: {int(payment.amount)}₽\n"
                f"Новый баланс: {new_balance}₽\n\n"
                f"Спасибо за оплату!",
                parse_mode="HTML",
                reply_markup=back_to_balance_keyboard(),
            )
        else:
            # Already processed
            await callback.answer("Оплата уже зачислена!", show_alert=True)
            user_balance = await balance_repo.get_by_user_id(payment.user_id)
            new_balance = int(user_balance.balance) if user_balance else 0
            
            await callback.message.edit_text(
                f"✅ Оплата уже зачислена!\n"
                f"Сумма: {int(payment.amount)}₽\n"
                f"Баланс: {new_balance}₽",
                parse_mode="HTML",
                reply_markup=back_to_balance_keyboard(),
            )
            
    elif status == "pending":
        await callback.answer("Оплата ещё обрабатывается", show_alert=True)
        await callback.message.edit_text(
            f"⏳ <b>Оплата в обработке</b>\n\n"
            f"Платёж ещё не прошёл.\n"
            f"Пожалуйста, завершите оплату по ссылке и нажмите «Проверить оплату» снова.\n\n"
            f"<i>Сумма: {int(payment.amount)}₽</i>",
            parse_mode="HTML",
            reply_markup=payment_confirmation_keyboard(
                payment_id,
                yookassa_client.get_payment_info(payment_id).get("confirmation", {}).get("confirmation_url", "")
                if yookassa_client.get_payment_info(payment_id) else ""
            ),
        )
        
    elif status == "canceled":
        await callback.answer("Оплата была отменена", show_alert=True)
        await yookassa_payment_repo.update_status(payment_id, "canceled")
        await callback.message.edit_text(
            "❌ <b>Платёж отменён</b>",
            parse_mode="HTML",
            reply_markup=back_to_balance_keyboard(),
        )
        
    else:
        await callback.answer("Не удалось проверить статус", show_alert=True)
        await callback.message.edit_text(
            "❌ Не удалось проверить статус платежа. Попробуйте позже.",
            reply_markup=back_to_balance_keyboard(),
        )


# =============================================================================
# Cancel Payment
# =============================================================================

@payment_router.callback_query(lambda c: c.data.startswith("pay_cancel:"))
async def callback_cancel_payment(
    callback: CallbackQuery,
    yookassa_payment_repo: YooKassaPaymentRepository,
) -> None:
    """Cancel payment."""
    await callback.answer()
    
    payment_id = callback.data.split(":")[1]
    
    # Update status in DB
    await yookassa_payment_repo.update_status(payment_id, "canceled")
    
    await callback.message.edit_text(
        "❌ Платёж отменён.",
        reply_markup=back_to_balance_keyboard(),
    )


# =============================================================================
# Payment History
# =============================================================================

@payment_router.callback_query(lambda c: c.data == "balance_history")
async def callback_payment_history(
    callback: CallbackQuery,
    yookassa_payment_repo: YooKassaPaymentRepository,
) -> None:
    """Show payment history."""
    await callback.answer()
    
    user_id = callback.from_user.id
    
    # Get last 10 payments
    payments = await yookassa_payment_repo.get_history(user_id, limit=10)
    
    if not payments:
        await callback.message.edit_text(
            "📋 <b>История платежей</b>\n\n"
            "У вас пока нет платежей.",
            parse_mode="HTML",
            reply_markup=payment_history_keyboard(),
        )
        return
    
    # Build history text
    lines = ["📋 <b>История платежей</b>\n"]
    
    for p in payments:
        status_emoji = {
            "succeeded": "✅",
            "pending": "⏳",
            "canceled": "❌",
        }.get(p.status, "❓")
        
        date_str = p.created_at.strftime("%d.%m.%Y %H:%M") if p.created_at else "—"
        lines.append(f"{status_emoji} {int(p.amount)}₽ — {date_str}")
    
    history_text = "\n".join(lines)
    
    await callback.message.edit_text(
        history_text,
        parse_mode="HTML",
        reply_markup=payment_history_keyboard(),
    )
