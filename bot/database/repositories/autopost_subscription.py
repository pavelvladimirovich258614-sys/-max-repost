"""Autopost subscription repository for managing autopost subscriptions."""

from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import AutopostSubscription
from bot.database.repositories.base import BaseRepository


class AutopostSubscriptionRepository(BaseRepository[AutopostSubscription]):
    """Repository for AutopostSubscription entity operations."""

    def __init__(self, session: AsyncSession) -> None:
        """
        Initialize AutopostSubscription repository.

        Args:
            session: Async database session
        """
        super().__init__(AutopostSubscription, session)

    async def get_active_subscriptions(self) -> list[AutopostSubscription]:
        """Get all active subscriptions.
        
        Returns:
            List of active subscription instances
        """
        stmt = select(self._model).where(self._model.is_active == True)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_user_subscriptions(self, user_id: int) -> list[AutopostSubscription]:
        """Get all subscriptions for a user.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            List of subscription instances
        """
        stmt = select(self._model).where(self._model.user_id == user_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_channel(
        self, 
        user_id: int, 
        tg_channel: str
    ) -> AutopostSubscription | None:
        """Find subscription by user and channel.
        
        Args:
            user_id: Telegram user ID
            tg_channel: Telegram channel username
            
        Returns:
            Subscription instance or None
        """
        # Normalize channel name
        if tg_channel.startswith('@'):
            tg_channel = tg_channel[1:]
            
        stmt = select(self._model).where(
            self._model.user_id == user_id,
            self._model.tg_channel == tg_channel
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def increment_posts_count(
        self, 
        subscription_id: int, 
        amount: Decimal
    ) -> AutopostSubscription | None:
        """Increment posts counter and total spent.
        
        Args:
            subscription_id: Subscription ID
            amount: Amount to add to total_spent
            
        Returns:
            Updated subscription instance or None
        """
        stmt = (
            update(self._model)
            .where(self._model.id == subscription_id)
            .values(
                posts_transferred=self._model.posts_transferred + 1,
                total_spent=self._model.total_spent + amount
            )
            .returning(self._model)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def pause_subscription(
        self, 
        subscription_id: int, 
        reason: str
    ) -> AutopostSubscription | None:
        """Pause subscription with reason.
        
        Args:
            subscription_id: Subscription ID
            reason: Pause reason (e.g., "insufficient_funds")
            
        Returns:
            Updated subscription instance or None
        """
        stmt = (
            update(self._model)
            .where(self._model.id == subscription_id)
            .values(
                is_active=False,
                paused_reason=reason
            )
            .returning(self._model)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def resume_subscription(
        self, 
        subscription_id: int
    ) -> AutopostSubscription | None:
        """Resume subscription.
        
        Args:
            subscription_id: Subscription ID
            
        Returns:
            Updated subscription instance or None
        """
        stmt = (
            update(self._model)
            .where(self._model.id == subscription_id)
            .values(
                is_active=True,
                paused_reason=None
            )
            .returning(self._model)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def update_last_post_id(
        self,
        subscription_id: int,
        post_id: int
    ) -> AutopostSubscription | None:
        """Update last transferred post ID.
        
        Args:
            subscription_id: Subscription ID
            post_id: Last transferred post ID
            
        Returns:
            Updated subscription instance or None
        """
        stmt = (
            update(self._model)
            .where(self._model.id == subscription_id)
            .values(last_post_id=post_id)
            .returning(self._model)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()
