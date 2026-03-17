"""Repository for TransferredPost entity operations."""

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import TransferredPost
from bot.database.repositories.base import BaseRepository


class TransferredPostRepository(BaseRepository[TransferredPost]):
    """Repository for tracking transferred posts to prevent duplicates."""

    def __init__(self, session: AsyncSession) -> None:
        """
        Initialize TransferredPost repository.

        Args:
            session: Async database session
        """
        super().__init__(TransferredPost, session)

    async def is_post_transferred(
        self,
        tg_channel: str,
        max_chat_id: str,
        tg_message_id: int,
    ) -> bool:
        """
        Check if a post has already been transferred.

        Args:
            tg_channel: Telegram channel username
            max_chat_id: Max channel chat_id
            tg_message_id: Telegram message ID

        Returns:
            True if post was already transferred, False otherwise
        """
        stmt = select(TransferredPost).where(
            and_(
                TransferredPost.tg_channel == tg_channel,
                TransferredPost.max_chat_id == max_chat_id,
                TransferredPost.tg_message_id == tg_message_id,
            )
        )
        result = await self._session.execute(stmt)
        return result.scalars().first() is not None

    async def record_transfer(
        self,
        user_id: int,
        tg_channel: str,
        max_chat_id: str,
        tg_message_id: int,
    ) -> TransferredPost:
        """
        Record a successful post transfer.

        Args:
            user_id: Telegram user ID who performed the transfer
            tg_channel: Telegram channel username
            max_chat_id: Max channel chat_id
            tg_message_id: Telegram message ID that was transferred

        Returns:
            Created TransferredPost instance
        """
        transferred = TransferredPost(
            user_id=user_id,
            tg_channel=tg_channel,
            max_chat_id=max_chat_id,
            tg_message_id=tg_message_id,
        )
        self._session.add(transferred)
        await self._session.flush()
        return transferred

    async def get_transferred_count(
        self,
        tg_channel: str,
        max_chat_id: str,
    ) -> int:
        """
        Get count of transferred posts for a channel pair.

        Args:
            tg_channel: Telegram channel username
            max_chat_id: Max channel chat_id

        Returns:
            Number of transferred posts
        """
        stmt = select(TransferredPost).where(
            and_(
                TransferredPost.tg_channel == tg_channel,
                TransferredPost.max_chat_id == max_chat_id,
            )
        )
        result = await self._session.execute(stmt)
        return len(result.scalars().all())
