"""Promo code repository for promo-related database operations."""

from datetime import datetime
from typing import Any

from sqlalchemy import select, update, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import PromoCode, PromoActivation
from bot.database.repositories.base import BaseRepository


class PromoCodeRepository(BaseRepository[PromoCode]):
    """Repository for PromoCode entity operations."""

    def __init__(self, session: AsyncSession) -> None:
        """
        Initialize PromoCode repository.

        Args:
            session: Async database session
        """
        super().__init__(PromoCode, session)

    async def get_by_code(self, code: str) -> PromoCode | None:
        """
        Get promo code by code string.

        Args:
            code: Promo code string

        Returns:
            PromoCode instance or None
        """
        stmt = select(PromoCode).where(PromoCode.code == code)
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def is_valid(self, code: str) -> tuple[bool, str | None]:
        """
        Check if promo code is valid (exists, not expired, not maxed out).

        Args:
            code: Promo code string

        Returns:
            Tuple of (is_valid, error_message)
        """
        promo = await self.get_by_code(code)
        if promo is None:
            return False, "Promo code not found"

        if promo.expires_at and promo.expires_at < datetime.utcnow():
            return False, "Promo code has expired"

        if promo.activated_count >= promo.max_activations:
            return False, "Promo code has reached maximum activations"

        return True, None

    async def increment_activation_count(
        self,
        promo_code_id: int,
    ) -> PromoCode | None:
        """
        Increment the activation count for a promo code.

        Args:
            promo_code_id: Promo code ID

        Returns:
            Updated promo code instance or None
        """
        stmt = (
            update(PromoCode)
            .where(PromoCode.id == promo_code_id)
            .values(activated_count=PromoCode.activated_count + 1)
            .returning(PromoCode)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def get_active_promo_codes(self) -> list[PromoCode]:
        """
        Get all active (non-expired) promo codes.

        Returns:
            List of active promo codes
        """
        stmt = select(PromoCode).where(
            or_(
                PromoCode.expires_at == None,  # noqa: E711
                PromoCode.expires_at > datetime.utcnow(),
            )
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class PromoActivationRepository(BaseRepository[PromoActivation]):
    """Repository for PromoActivation entity operations."""

    def __init__(self, session: AsyncSession) -> None:
        """
        Initialize PromoActivation repository.

        Args:
            session: Async database session
        """
        super().__init__(PromoActivation, session)

    async def is_used_by_user(
        self,
        promo_code_id: int,
        user_id: int,
    ) -> bool:
        """
        Check if promo code was already used by a user.

        Args:
            promo_code_id: Promo code ID
            user_id: User ID

        Returns:
            True if already used, False otherwise
        """
        stmt = select(PromoActivation).where(
            and_(
                PromoActivation.promo_code_id == promo_code_id,
                PromoActivation.user_id == user_id,
            )
        )
        result = await self._session.execute(stmt)
        return result.scalars().first() is not None

    async def activate(
        self,
        promo_code_id: int,
        user_id: int,
    ) -> PromoActivation | None:
        """
        Activate a promo code for a user.

        Args:
            promo_code_id: Promo code ID
            user_id: User ID

        Returns:
            Created activation instance or None
        """
        # Check if already used
        if await self.is_used_by_user(promo_code_id, user_id):
            return None

        activation = await self.create(
            promo_code_id=promo_code_id,
            user_id=user_id,
        )
        return activation

    async def get_user_activations(
        self,
        user_id: int,
    ) -> list[PromoActivation]:
        """
        Get all promo activations for a user.

        Args:
            user_id: User ID

        Returns:
            List of user's activations
        """
        stmt = (
            select(PromoActivation)
            .where(PromoActivation.user_id == user_id)
            .order_by(PromoActivation.activated_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_promo_activations(
        self,
        promo_code_id: int,
    ) -> list[PromoActivation]:
        """
        Get all activations for a promo code.

        Args:
            promo_code_id: Promo code ID

        Returns:
            List of activations
        """
        stmt = select(PromoActivation).where(
            PromoActivation.promo_code_id == promo_code_id
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_activations(self, promo_code_id: int) -> int:
        """
        Count activations for a promo code.

        Args:
            promo_code_id: Promo code ID

        Returns:
            Number of activations
        """
        stmt = select(PromoActivation).where(
            PromoActivation.promo_code_id == promo_code_id
        ).count()
        result = await self._session.execute(stmt)
        return result.scalar() or 0
