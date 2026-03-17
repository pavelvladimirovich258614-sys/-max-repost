"""User repository for user-related database operations."""

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert

from bot.database.models import User
from bot.database.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """Repository for User entity operations."""

    def __init__(self, session: AsyncSession) -> None:
        """
        Initialize User repository.

        Args:
            session: Async database session
        """
        super().__init__(User, session)

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        """
        Get user by Telegram ID.

        Args:
            telegram_id: Telegram user ID

        Returns:
            User instance or None
        """
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def get_or_create(self, telegram_id: int) -> tuple[User, bool]:
        """
        Get existing user or create new one.

        Args:
            telegram_id: Telegram user ID

        Returns:
            Tuple of (User instance, created flag)
        """
        user = await self.get_by_telegram_id(telegram_id)
        if user is not None:
            return user, False

        user = await self.create(telegram_id=telegram_id)
        return user, True

    async def update_balance(
        self,
        user_id: int,
        delta: int,
    ) -> User | None:
        """
        Atomically update user balance by increment/decrement.

        Args:
            user_id: User ID
            delta: Balance change (positive or negative)

        Returns:
            Updated user instance or None
        """
        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(balance=User.balance + delta)
            .returning(User)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def add_balance(
        self,
        telegram_id: int,
        amount: int,
    ) -> User | None:
        """
        Add posts to user balance.

        Args:
            telegram_id: Telegram user ID
            amount: Number of posts to add

        Returns:
            Updated user instance or None
        """
        stmt = (
            update(User)
            .where(User.telegram_id == telegram_id)
            .values(balance=User.balance + amount)
            .returning(User)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def consume_post(self, user_id: int) -> User | None:
        """
        Consume one post from user balance.

        Args:
            user_id: User ID

        Returns:
            Updated user instance or None if insufficient balance
        """
        stmt = (
            update(User)
            .where(User.id == user_id, User.balance > 0)
            .values(balance=User.balance - 1)
            .returning(User)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def set_bonus_received(self, user_id: int) -> User | None:
        """
        Mark user as having received the subscription bonus.

        Args:
            user_id: User ID

        Returns:
            Updated user instance or None
        """
        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(bonus_received=True)
            .returning(User)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def get_all_admins(self) -> list[User]:
        """
        Get all admin users.

        Returns:
            List of admin users
        """
        stmt = select(User).where(User.is_admin == True)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def add_free_posts_used(
        self,
        user_id: int,
        count: int,
    ) -> User | None:
        """
        Add to the count of free posts used by user.
        Ensures the total doesn't exceed FREE_POSTS_LIMIT.

        Args:
            user_id: User ID
            count: Number of free posts to add to the used count

        Returns:
            Updated user instance or None
        """
        from sqlalchemy import func

        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(free_posts_used=func.least(User.free_posts_used + count, User.FREE_POSTS_LIMIT))
            .returning(User)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()
