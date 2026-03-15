"""Payment repository for payment-related database operations."""

from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Payment, PaymentStatus
from bot.database.repositories.base import BaseRepository


class PaymentRepository(BaseRepository[Payment]):
    """Repository for Payment entity operations."""

    def __init__(self, session: AsyncSession) -> None:
        """
        Initialize Payment repository.

        Args:
            session: Async database session
        """
        super().__init__(Payment, session)

    async def get_by_yookassa_id(self, yookassa_payment_id: str) -> Payment | None:
        """
        Get payment by YooKassa payment ID.

        Args:
            yookassa_payment_id: YooKassa payment ID

        Returns:
            Payment instance or None
        """
        stmt = select(Payment).where(
            Payment.yookassa_payment_id == yookassa_payment_id
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def get_by_user(
        self,
        user_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Payment]:
        """
        Get payments for a user.

        Args:
            user_id: User ID
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            List of user's payments
        """
        stmt = (
            select(Payment)
            .where(Payment.user_id == user_id)
            .order_by(Payment.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_status(
        self,
        payment_id: int,
        status: PaymentStatus | str,
    ) -> Payment | None:
        """
        Update payment status.

        Args:
            payment_id: Payment ID
            status: New status

        Returns:
            Updated payment instance or None
        """
        if isinstance(status, PaymentStatus):
            status = status.value

        stmt = (
            update(Payment)
            .where(Payment.id == payment_id)
            .values(status=status)
            .returning(Payment)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def mark_as_paid(
        self,
        yookassa_payment_id: str,
    ) -> Payment | None:
        """
        Mark payment as paid by YooKassa ID.

        Args:
            yookassa_payment_id: YooKassa payment ID

        Returns:
            Updated payment instance or None
        """
        stmt = (
            update(Payment)
            .where(Payment.yookassa_payment_id == yookassa_payment_id)
            .values(status=PaymentStatus.PAID.value)
            .returning(Payment)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def get_pending_payments(self, limit: int = 50) -> list[Payment]:
        """
        Get pending payments for status checking.

        Args:
            limit: Maximum number of results

        Returns:
            List of pending payments
        """
        stmt = (
            select(Payment)
            .where(Payment.status == PaymentStatus.PENDING.value)
            .order_by(Payment.created_at.asc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_user(self, user_id: int) -> int:
        """
        Count payments for a user.

        Args:
            user_id: User ID

        Returns:
            Number of payments
        """
        stmt = select(Payment).where(Payment.user_id == user_id).count()
        result = await self._session.execute(stmt)
        return result.scalar() or 0

    async def get_total_spent(self, user_id: int) -> int:
        """
        Get total amount spent by user (in kopecks).

        Args:
            user_id: User ID

        Returns:
            Total amount in kopecks
        """
        stmt = select(Payment.amount).where(
            and_(
                Payment.user_id == user_id,
                Payment.status == PaymentStatus.PAID.value,
            )
        )
        result = await self._session.execute(stmt)
        amounts = result.scalars().all()
        return sum(amounts) if amounts else 0

    async def get_total_posts_purchased(self, user_id: int) -> int:
        """
        Get total posts purchased by user.

        Args:
            user_id: User ID

        Returns:
            Total number of posts purchased
        """
        stmt = select(Payment.posts_count).where(
            and_(
                Payment.user_id == user_id,
                Payment.status == PaymentStatus.PAID.value,
            )
        )
        result = await self._session.execute(stmt)
        counts = result.scalars().all()
        return sum(counts) if counts else 0
