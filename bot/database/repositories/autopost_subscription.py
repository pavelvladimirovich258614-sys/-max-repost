"""Autopost subscription repository for managing autopost subscriptions."""

from decimal import Decimal

from sqlalchemy import select, update, delete
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

    async def get_by_id_and_user(
        self,
        subscription_id: int,
        user_id: int
    ) -> AutopostSubscription | None:
        """Get subscription by ID and verify user owns it.
        
        Args:
            subscription_id: Subscription ID
            user_id: Telegram user ID
            
        Returns:
            Subscription instance or None if not found or user doesn't own it
        """
        stmt = select(self._model).where(
            self._model.id == subscription_id,
            self._model.user_id == user_id
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def toggle_status(
        self,
        subscription_id: int,
        user_id: int
    ) -> AutopostSubscription | None:
        """Toggle is_active status for a user's subscription.
        
        Args:
            subscription_id: Subscription ID
            user_id: Telegram user ID
            
        Returns:
            Updated subscription instance or None if not found
        """
        # First get the subscription to check current status
        sub = await self.get_by_id_and_user(subscription_id, user_id)
        if not sub:
            return None
        
        # Toggle the status
        new_status = not sub.is_active
        paused_reason = None if new_status else sub.paused_reason
        
        stmt = (
            update(self._model)
            .where(
                self._model.id == subscription_id,
                self._model.user_id == user_id
            )
            .values(
                is_active=new_status,
                paused_reason=paused_reason
            )
            .returning(self._model)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def delete_by_user(
        self,
        subscription_id: int,
        user_id: int
    ) -> bool:
        """Delete subscription if user owns it.
        
        Args:
            subscription_id: Subscription ID
            user_id: Telegram user ID
            
        Returns:
            True if deleted, False if not found or user doesn't own it
        """
        stmt = (
            delete(self._model)
            .where(
                self._model.id == subscription_id,
                self._model.user_id == user_id
            )
        )
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    async def get_by_tg_channel(
        self,
        user_id: int,
        tg_channel: str
    ) -> AutopostSubscription | None:
        """Get subscription by TG channel and user.
        
        Similar to get_by_channel but explicitly named for handler usage.
        
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
