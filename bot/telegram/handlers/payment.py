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
from bot.telegram.keyboards.payment import (
    payment_amount_keyboard,
    payment_confirmation_keyboard,
    payment_history_keyboard,
    back_to_balance_keyboard,
)

payment_router = Router(name="payment")

# Initialize YooKassa client
yookassa_client = YooKassaClient()


class PaymentStates(StatesGroup):
    """FSM states for custom amount input."""
    waiting_for_amount = State()


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
    payment_repo: YooKassaPaymentRepository,
) -> None:
    """Handle fixed amount payment selection."""
    await callback.answer()
    
    # Parse amount
    amount_str = callback.data.split(":")[1]
    amount = Decimal(amount_str)
    
    user_id = callback.from_user.id
    
    # Create payment in YooKassa
    payment_id, confirmation_url = await yookassa_client.create_payment(
        user_id=user_id,
        amount_rub=amount,
        description=f"Пополнение баланса на {amount}₽",
    )
    
    if not payment_id or not confirmation_url:
        await callback.message.edit_text(
            "❌ <b>Ошибка создания платежа</b>\n\n"
            "Не удалось создать платёж. Попробуйте позже.",
            parse_mode="HTML",
            reply_markup=back_to_balance_keyboard(),
        )
        return
    
    # Save to database
    await payment_repo.create_payment(
        user_id=user_id,
        payment_id=payment_id,
        amount=amount,
    )
    
    # Show payment instructions
    await callback.message.edit_text(
        f"<b>💳 Оплата {amount}₽</b>\n\n"
        f"Нажмите кнопку «Оплатить» ниже для перехода к оплате.\n\n"
        f"После оплаты вернитесь в бот и нажмите «Проверить оплату».\n\n"
        f"<i>Платёж действителен 24 часа</i>",
        parse_mode="HTML",
        reply_markup=payment_confirmation_keyboard(payment_id, confirmation_url),
    )


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
    payment_repo: YooKassaPaymentRepository,
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
    
    # Clear state
    await state.clear()
    
    user_id = message.from_user.id
    
    # Create payment in YooKassa
    payment_id, confirmation_url = await yookassa_client.create_payment(
        user_id=user_id,
        amount_rub=amount,
        description=f"Пополнение баланса на {amount}₽",
    )
    
    if not payment_id or not confirmation_url:
        await message.answer(
            "❌ <b>Ошибка создания платежа</b>\n\n"
            "Не удалось создать платёж. Попробуйте позже.",
            parse_mode="HTML",
            reply_markup=back_to_balance_keyboard(),
        )
        return
    
    # Save to database
    await payment_repo.create_payment(
        user_id=user_id,
        payment_id=payment_id,
        amount=amount,
    )
    
    # Show payment instructions
    await message.answer(
        f"<b>💳 Оплата {amount}₽</b>\n\n"
        f"Нажмите кнопку «Оплатить» ниже для перехода к оплате.\n\n"
        f"После оплаты вернитесь в бот и нажмите «Проверить оплату».\n\n"
        f"<i>Платёж действителен 24 часа</i>",
        parse_mode="HTML",
        reply_markup=payment_confirmation_keyboard(payment_id, confirmation_url),
    )


# =============================================================================
# Check Payment Status
# =============================================================================

@payment_router.callback_query(lambda c: c.data.startswith("pay_check:"))
async def callback_check_payment(
    callback: CallbackQuery,
    payment_repo: YooKassaPaymentRepository,
    balance_repo: UserBalanceRepository,
    transaction_repo: BalanceTransactionRepository,
) -> None:
    """Check payment status."""
    await callback.answer("⏳ Проверяю оплату...")
    
    payment_id = callback.data.split(":")[1]
    
    # Get payment from DB
    payment = await payment_repo.get_by_payment_id(payment_id)
    if not payment:
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
            await payment_repo.update_status(payment_id, "succeeded")
            
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
            user_balance = await balance_repo.get_by_user_id(payment.user_id)
            new_balance = int(user_balance.balance) if user_balance else 0
            
            await callback.message.edit_text(
                f"✅ <b>Оплата уже была обработана</b>\n\n"
                f"Сумма: {int(payment.amount)}₽\n"
                f"Баланс: {new_balance}₽",
                parse_mode="HTML",
                reply_markup=back_to_balance_keyboard(),
            )
            
    elif status == "pending":
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
        await payment_repo.update_status(payment_id, "canceled")
        await callback.message.edit_text(
            "❌ <b>Платёж отменён</b>",
            parse_mode="HTML",
            reply_markup=back_to_balance_keyboard(),
        )
        
    else:
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
    payment_repo: YooKassaPaymentRepository,
) -> None:
    """Cancel payment."""
    await callback.answer()
    
    payment_id = callback.data.split(":")[1]
    
    # Update status in DB
    await payment_repo.update_status(payment_id, "canceled")
    
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
    payment_repo: YooKassaPaymentRepository,
) -> None:
    """Show payment history."""
    await callback.answer()
    
    user_id = callback.from_user.id
    
    # Get last 10 payments
    payments = await payment_repo.get_history(user_id, limit=10)
    
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
