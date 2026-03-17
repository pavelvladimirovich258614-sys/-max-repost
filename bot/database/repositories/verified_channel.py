"""Repository for VerifiedChannel entity operations."""

from datetime import datetime
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import VerifiedChannel
from bot.database.repositories.base import BaseRepository


class VerifiedChannelRepository(BaseRepository[VerifiedChannel]):
    """Repository for verified Telegram channel operations."""

    def __init__(self, session: AsyncSession) -> None:
        """
        Initialize VerifiedChannel repository.

        Args:
            session: Async database session
        """
        super().__init__(VerifiedChannel, session)

    async def is_channel_verified(
        self,
        user_id: int,
        tg_channel: str,
    ) -> bool:
        """
        Check if a channel is already verified for a user.

        Args:
            user_id: Telegram user ID
            tg_channel: Telegram channel username

        Returns:
            True if channel is verified, False otherwise
        """
        stmt = select(VerifiedChannel).where(
            VerifiedChannel.user_id == user_id,
            VerifiedChannel.tg_channel == tg_channel,
        )
        result = await self._session.execute(stmt)
        return result.scalars().first() is not None

    async def get_verified_channel(
        self,
        user_id: int,
        tg_channel: str,
    ) -> VerifiedChannel | None:
        """
        Get verified channel record for a user and channel.

        Args:
            user_id: Telegram user ID
            tg_channel: Telegram channel username

        Returns:
            VerifiedChannel instance or None
        """
        stmt = select(VerifiedChannel).where(
            VerifiedChannel.user_id == user_id,
            VerifiedChannel.tg_channel == tg_channel,
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def get_user_verified_channels(
        self,
        user_id: int,
    ) -> list[VerifiedChannel]:
        """
        Get all verified channels for a user.

        Args:
            user_id: Telegram user ID

        Returns:
            List of VerifiedChannel instances ordered by verified_at (most recent first)
        """
        stmt = (
            select(VerifiedChannel)
            .where(VerifiedChannel.user_id == user_id)
            .order_by(VerifiedChannel.verified_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def verify_channel(
        self,
        user_id: int,
        tg_channel: str,
        tg_channel_id: str | None = None,
    ) -> VerifiedChannel:
        """
        Mark a channel as verified for a user.
        
        If already verified, updates the verified_at timestamp.

        Args:
            user_id: Telegram user ID
            tg_channel: Telegram channel username
            tg_channel_id: Telegram channel numeric ID

        Returns:
            VerifiedChannel instance
        """
        existing = await self.get_verified_channel(user_id, tg_channel)
        
        if existing:
            # Update verified_at timestamp
            existing.verified_at = datetime.utcnow()
            await self._session.flush()
            return existing
        else:
            # Create new record
            verified = VerifiedChannel(
                user_id=user_id,
                tg_channel=tg_channel,
                tg_channel_id=tg_channel_id,
            )
            self._session.add(verified)
            await self._session.flush()
            return verified

    async def delete_verification(
        self,
        user_id: int,
        tg_channel: str,
    ) -> bool:
        """
        Delete verification record for a user and channel.

        Args:
            user_id: User ID
            tg_channel: Telegram channel username

        Returns:
            True if deleted, False otherwise
        """
        stmt = delete(VerifiedChannel).where(
            VerifiedChannel.user_id == user_id,
            VerifiedChannel.tg_channel == tg_channel,
        )
        result = await self._session.execute(stmt)
        return result.rowcount > 0
