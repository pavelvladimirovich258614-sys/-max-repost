"""User repository for user-related database operations."""

import random
import string
from typing import Any

from sqlalchemy import select, update, func
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

    async def get_email(self, telegram_id: int) -> str | None:
        """
        Get user email by Telegram ID.
        
        Args:
            telegram_id: Telegram user ID
            
        Returns:
            User email or None
        """
        user = await self.get_by_telegram_id(telegram_id)
        return user.email if user else None

    async def set_email(self, telegram_id: int, email: str) -> User | None:
        """
        Set user email.
        
        Args:
            telegram_id: Telegram user ID
            email: Email to set
            
        Returns:
            Updated user instance or None
        """
        stmt = (
            update(User)
            .where(User.telegram_id == telegram_id)
            .values(email=email)
            .returning(User)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    def _generate_referral_code(self) -> str:
        """
        Generate a unique 8-character referral code.
        
        Returns:
            Unique referral code (uppercase letters + digits)
        """
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    
    async def _generate_unique_referral_code(self) -> str:
        """
        Generate a unique referral code and ensure it doesn't exist.
        
        Returns:
            Unique referral code
        """
        max_attempts = 10
        for _ in range(max_attempts):
            code = self._generate_referral_code()
            existing = await self.get_by_referral_code(code)
            if existing is None:
                return code
        # Fallback: add timestamp to ensure uniqueness
        import time
        return f"{self._generate_referral_code()}{int(time.time()) % 100}"
    
    async def get_by_referral_code(self, code: str) -> User | None:
        """
        Get user by referral code.
        
        Args:
            code: Referral code
            
        Returns:
            User instance or None
        """
        stmt = select(User).where(User.referral_code == code)
        result = await self._session.execute(stmt)
        return result.scalars().first()
    
    async def set_referral_code(self, user_id: int, code: str) -> User | None:
        """
        Set referral code for user.
        
        Args:
            user_id: User ID
            code: Referral code to set
            
        Returns:
            Updated user instance or None
        """
        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(referral_code=code)
            .returning(User)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()
    
    async def set_referred_by(self, user_id: int, referrer_id: int) -> User | None:
        """
        Set referrer for user (can only be set once).
        
        Args:
            user_id: User ID (the new user being referred)
            referrer_id: Telegram ID of referrer
            
        Returns:
            Updated user instance or None if already set
        """
        # Check if already has a referrer
        stmt = select(User).where(User.id == user_id)
        result = await self._session.execute(stmt)
        user = result.scalars().first()
        
        if user is None or user.referred_by is not None:
            return None
        
        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(referred_by=referrer_id)
            .returning(User)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()
    
    async def count_referrals(self, referrer_id: int) -> int:
        """
        Count number of users referred by a user.
        
        Args:
            referrer_id: Telegram ID of referrer
            
        Returns:
            Number of referrals
        """
        stmt = select(func.count(User.id)).where(User.referred_by == referrer_id)
        result = await self._session.execute(stmt)
        return result.scalar() or 0
    
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

        # Generate unique referral code for new user
        referral_code = await self._generate_unique_referral_code()
        user = await self.create(telegram_id=telegram_id, referral_code=referral_code)
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

    async def count_all(self) -> int:
        """
        Count all users.

        Returns:
            Number of users
        """
        stmt = select(func.count(User.id))
        result = await self._session.execute(stmt)
        return result.scalar() or 0

    async def get_all_paginated(
        self,
        page: int,
        per_page: int,
    ) -> list[User]:
        """
        Get all users with pagination.

        Args:
            page: Page number (1-based)
            per_page: Number of items per page

        Returns:
            List of User instances
        """
        stmt = (
            select(User)
            .order_by(User.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
