"""YooKassa payment repository for tracking payments."""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import YooKassaPayment
from bot.database.repositories.base import BaseRepository


class YooKassaPaymentRepository(BaseRepository[YooKassaPayment]):
    """Repository for YooKassa payment operations."""
    
    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository."""
        super().__init__(YooKassaPayment, session)
    
    async def create_payment(
        self,
        user_id: int,
        payment_id: str,
        amount: Decimal,
    ) -> YooKassaPayment:
        """
        Create a new payment record.
        
        Args:
            user_id: Telegram user ID
            payment_id: YooKassa payment ID
            amount: Amount in rubles
            
        Returns:
            Created YooKassaPayment instance
        """
        return await self.create(
            user_id=user_id,
            payment_id=payment_id,
            amount=amount,
            status="pending",
        )
    
    async def update_status(
        self,
        payment_id: str,
        status: str,
    ) -> YooKassaPayment | None:
        """
        Update payment status.
        
        Args:
            payment_id: YooKassa payment ID
            status: New status (pending/succeeded/canceled)
            
        Returns:
            Updated YooKassaPayment or None if not found
        """
        stmt = (
            update(YooKassaPayment)
            .where(YooKassaPayment.payment_id == payment_id)
            .values(status=status)
            .returning(YooKassaPayment)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()
    
    async def get_by_payment_id(
        self,
        payment_id: str,
    ) -> YooKassaPayment | None:
        """
        Get payment by YooKassa payment ID.
        
        Args:
            payment_id: YooKassa payment ID
            
        Returns:
            YooKassaPayment or None
        """
        stmt = select(YooKassaPayment).where(
            YooKassaPayment.payment_id == payment_id
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()
    
    async def get_pending_by_user(
        self,
        user_id: int,
    ) -> list[YooKassaPayment]:
        """
        Get all pending payments for a user.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            List of pending YooKassaPayment instances
        """
        stmt = (
            select(YooKassaPayment)
            .where(
                YooKassaPayment.user_id == user_id,
                YooKassaPayment.status == "pending"
            )
            .order_by(YooKassaPayment.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def get_history(
        self,
        user_id: int,
        limit: int = 20,
    ) -> list[YooKassaPayment]:
        """
        Get payment history for a user.
        
        Args:
            user_id: Telegram user ID
            limit: Maximum number of results
            
        Returns:
            List of YooKassaPayment instances
        """
        stmt = (
            select(YooKassaPayment)
            .where(YooKassaPayment.user_id == user_id)
            .order_by(YooKassaPayment.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def get_all_pending(
        self,
        older_than_minutes: int = 1,
        younger_than_hours: int = 24,
    ) -> list[YooKassaPayment]:
        """
        Get all pending payments for background checker.
        
        Args:
            older_than_minutes: Only payments older than this (to avoid race conditions)
            younger_than_hours: Only payments younger than this (ignore very old)
            
        Returns:
            List of pending YooKassaPayment instances
        """
        now = datetime.utcnow()
        min_time = now - timedelta(minutes=older_than_minutes)
        max_time = now - timedelta(hours=younger_than_hours)
        
        stmt = (
            select(YooKassaPayment)
            .where(
                YooKassaPayment.status == "pending",
                YooKassaPayment.created_at <= min_time,
                YooKassaPayment.created_at >= max_time,
            )
            .order_by(YooKassaPayment.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
