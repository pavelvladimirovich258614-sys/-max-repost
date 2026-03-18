"""YooKassa payment client for processing payments."""

from decimal import Decimal
from typing import Any

from loguru import logger
from yookassa import Configuration, Payment

from config.settings import settings


class YooKassaClient:
    """Client for YooKassa payment processing."""
    
    def __init__(self) -> None:
        """Initialize YooKassa with credentials from settings."""
        Configuration.account_id = settings.yookassa_shop_id
        Configuration.secret_key = settings.yookassa_secret_key
        logger.info(f"YooKassa initialized with shop_id: {settings.yookassa_shop_id}")
    
    async def create_payment(
        self,
        user_id: int,
        amount_rub: Decimal,
        description: str = "Пополнение баланса",
        email: str | None = None,
    ) -> tuple[str | None, str | None]:
        """
        Create a new payment with 54-FZ receipt.
        
        Args:
            user_id: Telegram user ID
            amount_rub: Amount in rubles
            description: Payment description
            email: Customer email for receipt (optional, uses default if not provided)
            
        Returns:
            Tuple of (payment_id, confirmation_url) or (None, None) on error
        """
        try:
            # Use provided email or default from settings
            customer_email = email or settings.receipt_email
            
            payment = Payment.create({
                "amount": {
                    "value": str(amount_rub),
                    "currency": "RUB"
                },
                "capture": True,
                "confirmation": {
                    "type": "redirect",
                    "return_url": settings.yookassa_return_url
                },
                "description": description,
                "metadata": {
                    "user_id": str(user_id),
                    "amount": str(amount_rub)
                },
                # 54-FZ receipt (required in production mode)
                "receipt": {
                    "customer": {
                        "email": customer_email
                    },
                    "items": [
                        {
                            "description": "Пополнение баланса бота Max-Repost",
                            "quantity": "1.00",
                            "amount": {
                                "value": str(amount_rub),
                                "currency": "RUB"
                            },
                            "vat_code": 1,  # 1 = без НДС
                            "payment_mode": "full_payment",
                            "payment_subject": "service"
                        }
                    ]
                }
            })
            
            payment_id = payment.id
            confirmation_url = payment.confirmation.confirmation_url
            
            logger.info(f"Created payment {payment_id} for user {user_id}, amount {amount_rub}₽")
            return payment_id, confirmation_url
            
        except Exception as e:
            logger.error(f"Failed to create payment: {e}")
            return None, None
    
    def check_payment(self, payment_id: str) -> str:
        """
        Check payment status.
        
        Args:
            payment_id: YooKassa payment ID
            
        Returns:
            Payment status: "pending", "succeeded", "canceled", or "unknown"
        """
        try:
            payment = Payment.find_one(payment_id)
            status = payment.status
            logger.debug(f"Payment {payment_id} status: {status}")
            return status
        except Exception as e:
            logger.error(f"Failed to check payment {payment_id}: {e}")
            return "unknown"
    
    def get_payment_info(self, payment_id: str) -> dict[str, Any] | None:
        """
        Get full payment information.
        
        Args:
            payment_id: YooKassa payment ID
            
        Returns:
            Payment info dict or None on error
        """
        try:
            payment = Payment.find_one(payment_id)
            return {
                "id": payment.id,
                "status": payment.status,
                "amount": payment.amount.value,
                "currency": payment.amount.currency,
                "description": payment.description,
                "metadata": payment.metadata,
                "created_at": payment.created_at,
            }
        except Exception as e:
            logger.error(f"Failed to get payment info {payment_id}: {e}")
            return None
