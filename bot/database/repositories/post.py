"""Post repository for post-related database operations."""

from datetime import datetime
from typing import Any

from sqlalchemy import select, update, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Post, PostStatus
from bot.database.repositories.base import BaseRepository


class PostRepository(BaseRepository[Post]):
    """Repository for Post entity operations."""

    def __init__(self, session: AsyncSession) -> None:
        """
        Initialize Post repository.

        Args:
            session: Async database session
        """
        super().__init__(Post, session)

    async def get_by_telegram_id(
        self,
        channel_id: int,
        telegram_post_id: str,
    ) -> Post | None:
        """
        Get post by Telegram post ID (for duplicate checking).

        Args:
            channel_id: Channel ID
            telegram_post_id: Telegram post ID

        Returns:
            Post instance or None
        """
        stmt = select(Post).where(
            and_(
                Post.channel_id == channel_id,
                Post.telegram_post_id == telegram_post_id,
            )
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def get_by_channel(
        self,
        channel_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Post]:
        """
        Get posts for a channel.

        Args:
            channel_id: Channel ID
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            List of posts
        """
        stmt = (
            select(Post)
            .where(Post.channel_id == channel_id)
            .order_by(Post.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_status(
        self,
        status: PostStatus | str,
        limit: int = 100,
    ) -> list[Post]:
        """
        Get posts by status.

        Args:
            status: Post status
            limit: Maximum number of results

        Returns:
            List of posts with given status
        """
        if isinstance(status, PostStatus):
            status = status.value

        stmt = (
            select(Post)
            .where(Post.status == status)
            .order_by(Post.created_at.asc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_pending_posts(self, limit: int = 50) -> list[Post]:
        """
        Get pending posts for processing.

        Args:
            limit: Maximum number of results

        Returns:
            List of pending posts
        """
        return await self.get_by_status(PostStatus.PENDING, limit)

    async def get_failed_posts(
        self,
        hours: int = 24,
        limit: int = 50,
    ) -> list[Post]:
        """
        Get recently failed posts for retry.

        Args:
            hours: Lookback period in hours
            limit: Maximum number of results

        Returns:
            List of failed posts
        """
        since = datetime.utcnow() - __import__("datetime").timedelta(hours=hours)

        stmt = (
            select(Post)
            .where(
                and_(
                    Post.status == PostStatus.FAILED.value,
                    Post.updated_at >= since,
                )
            )
            .order_by(Post.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_status(
        self,
        post_id: int,
        status: PostStatus | str,
    ) -> Post | None:
        """
        Update post status.

        Args:
            post_id: Post ID
            status: New status

        Returns:
            Updated post instance or None
        """
        if isinstance(status, PostStatus):
            status = status.value

        stmt = (
            update(Post)
            .where(Post.id == post_id)
            .values(status=status)
            .returning(Post)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def mark_as_sent(
        self,
        post_id: int,
        max_post_id: str,
    ) -> Post | None:
        """
        Mark post as successfully sent.

        Args:
            post_id: Post ID
            max_post_id: Resulting Max post ID

        Returns:
            Updated post instance or None
        """
        stmt = (
            update(Post)
            .where(Post.id == post_id)
            .values(
                status=PostStatus.SENT.value,
                max_post_id=max_post_id,
            )
            .returning(Post)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def mark_as_failed(self, post_id: int) -> Post | None:
        """
        Mark post as failed.

        Args:
            post_id: Post ID

        Returns:
            Updated post instance or None
        """
        return await self.update_status(post_id, PostStatus.FAILED)

    async def count_by_channel(self, channel_id: int) -> int:
        """
        Count posts for a channel.

        Args:
            channel_id: Channel ID

        Returns:
            Number of posts
        """
        stmt = select(Post).where(
            Post.channel_id == channel_id,
        ).count()
        result = await self._session.execute(stmt)
        return result.scalar() or 0

    async def count_by_status(
        self,
        status: PostStatus | str,
        channel_id: int | None = None,
    ) -> int:
        """
        Count posts by status (optionally filtered by channel).

        Args:
            status: Post status
            channel_id: Optional channel ID filter

        Returns:
            Number of posts
        """
        if isinstance(status, PostStatus):
            status = status.value

        stmt = select(Post).where(Post.status == status)
        if channel_id is not None:
            stmt = stmt.where(Post.channel_id == channel_id)

        stmt = stmt.count()
        result = await self._session.execute(stmt)
        return result.scalar() or 0
