"""Background task for checking pending YooKassa payments."""

import asyncio

from aiogram import Bot
from loguru import logger

from bot.payments.yookassa_client import YooKassaClient
from bot.database.repositories.yookassa_payment import YooKassaPaymentRepository
from bot.database.repositories.balance import UserBalanceRepository, BalanceTransactionRepository
from bot.database.connection import get_session


async def check_pending_payments(
    yookassa_client: YooKassaClient,
    bot: Bot,
) -> None:
    """
    Background task to check pending payments.
    
    Runs every 30 seconds and checks all pending payments older than 1 minute.
    If payment succeeded, adds balance and notifies user.
    
    Args:
        yookassa_client: YooKassa client instance
        bot: Aiogram bot instance for notifications
    """
    logger.info("Payment checker task started")
    
    while True:
        try:
            await asyncio.sleep(30)  # Check every 30 seconds
            
            async with get_session() as session:
                payment_repo = YooKassaPaymentRepository(session)
                balance_repo = UserBalanceRepository(session)
                transaction_repo = BalanceTransactionRepository(session)
                
                # Get pending payments older than 1 minute
                pending = await payment_repo.get_all_pending(
                    older_than_minutes=1,
                    younger_than_hours=24,
                )
                
                if not pending:
                    continue
                
                logger.debug(f"Checking {len(pending)} pending payments")
                
                for payment in pending:
                    try:
                        # Check status in YooKassa
                        status = yookassa_client.check_payment(payment.payment_id)
                        
                        if status == "succeeded":
                            # Update payment status
                            await payment_repo.update_status(
                                payment.payment_id, "succeeded"
                            )
                            
                            # Ensure balance record exists before updating
                            await balance_repo.get_or_create(payment.user_id)
                            
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
                                description=f"Пополнение через YooKassa (платёж {payment.payment_id})",
                            )
                            
                            # Notify user
                            try:
                                await bot.send_message(
                                    payment.user_id,
                                    f"✅ <b>Оплата прошла успешно!</b>\n\n"
                                    f"Сумма: {int(payment.amount)}₽\n"
                                    f"Баланс пополнен автоматически.",
                                    parse_mode="HTML",
                                )
                                logger.info(f"Auto-confirmed payment {payment.payment_id} for user {payment.user_id}")
                            except Exception as e:
                                logger.warning(f"Could not notify user {payment.user_id}: {e}")
                        

                            # Notify admins
                            try:
                                from config.settings import settings
                                for admin_id in settings.admin_ids:
                                    try:
                                        await bot.send_message(
                                            admin_id,
                                            f"\U0001f4b0 <b>\u041d\u043e\u0432\u0430\u044f \u043e\u043f\u043b\u0430\u0442\u0430!</b>\n\n"
                                            f"User: <code>{payment.user_id}</code>\n"
                                            f"\u0421\u0443\u043c\u043c\u0430: {int(payment.amount)}\u20bd\n"
                                            f"\u041f\u043b\u0430\u0442\u0451\u0436: <code>{payment.payment_id}</code>",
                                            parse_mode="HTML",
                                        )
                                    except Exception:
                                        pass
                            except Exception as e:
                                logger.warning(f"Could not notify admins: {e}")

                        elif status == "canceled":
                            await payment_repo.update_status(
                                payment.payment_id, "canceled"
                            )
                            logger.info(f"Payment {payment.payment_id} marked as canceled")
                            
                    except Exception as e:
                        logger.error(f"Error checking payment {payment.payment_id}: {e}")
                        
        except asyncio.CancelledError:
            logger.info("Payment checker task cancelled")
            break
        except Exception as e:
            logger.error(f"Payment checker error: {e}")
            await asyncio.sleep(30)
    
    logger.info("Payment checker task stopped")
