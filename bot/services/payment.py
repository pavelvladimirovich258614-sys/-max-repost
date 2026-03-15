"""Payment service using YooKassa."""

from config.settings import settings


class PaymentService:
    """
    Service for processing payments via YooKassa.

    This is a stub class - business logic will be implemented later.
    """

    def __init__(
        self,
        shop_id: str | None = None,
        secret_key: str | None = None,
    ) -> None:
        """
        Initialize PaymentService.

        Args:
            shop_id: YooKassa shop ID
            secret_key: YooKassa secret key
        """
        self.shop_id = shop_id or settings.yookassa_shop_id
        self.secret_key = secret_key or settings.yookassa_secret_key

    async def create_payment(
        self,
        amount: int,
        description: str,
        user_id: int,
    ) -> str:
        """
        Create a new payment.

        Args:
            amount: Payment amount in rubles
            description: Payment description
            user_id: User ID for metadata

        Returns:
            Payment confirmation URL
        """
        pass

    async def check_payment(self, payment_id: str) -> bool:
        """
        Check payment status.

        Args:
            payment_id: Payment ID to check

        Returns:
            True if payment is successful, False otherwise
        """
        pass
