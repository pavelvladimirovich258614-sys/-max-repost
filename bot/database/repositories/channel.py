"""Channel repository for channel-related database operations."""

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert
from typing import Any

from bot.database.models import Channel
from bot.database.repositories.base import BaseRepository


class ChannelRepository(BaseRepository[Channel]):
    """Repository for Channel entity operations."""

    def __init__(self, session: AsyncSession) -> None:
        """
        Initialize Channel repository.

        Args:
            session: Async database session
        """
        super().__init__(Channel, session)

    async def get_by_telegram_id(
        self,
        telegram_channel_id: str,
    ) -> Channel | None:
        """
        Get channel by Telegram channel ID.

        Args:
            telegram_channel_id: Telegram channel ID

        Returns:
            Channel instance or None
        """
        stmt = select(Channel).where(Channel.telegram_channel_id == telegram_channel_id)
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def get_by_user(self, user_id: int) -> list[Channel]:
        """
        Get all channels for a user.

        Args:
            user_id: User ID

        Returns:
            List of user's channels
        """
        stmt = select(Channel).where(Channel.user_id == user_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_all_active_autopost(self) -> list[Channel]:
        """
        Get all channels with auto-repost enabled.

        Returns:
            List of channels with auto_repost=True
        """
        stmt = select(Channel).where(
            Channel.auto_repost == True,
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_active_autopost_by_user(
        self,
        user_id: int,
    ) -> list[Channel]:
        """
        Get user's channels with auto-repost enabled.

        Args:
            user_id: User ID

        Returns:
            List of user's channels with auto_repost=True
        """
        stmt = select(Channel).where(
            Channel.user_id == user_id,
            Channel.auto_repost == True,
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_settings(
        self,
        channel_id: int,
        settings: dict[str, Any],
    ) -> Channel | None:
        """
        Merge settings into channel settings (JSONB).

        Args:
            channel_id: Channel ID
            settings: Settings dict to merge

        Returns:
            Updated channel instance or None
        """
        channel = await self.get(channel_id)
        if channel is None:
            return None

        # Merge settings
        merged_settings = {**(channel.settings or {}), **settings}

        stmt = (
            update(Channel)
            .where(Channel.id == channel_id)
            .values(settings=merged_settings)
            .returning(Channel)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def toggle_autopost(
        self,
        channel_id: int,
        enabled: bool,
    ) -> Channel | None:
        """
        Toggle auto-repost for a channel.

        Args:
            channel_id: Channel ID
            enabled: Auto-repost state

        Returns:
            Updated channel instance or None
        """
        stmt = (
            update(Channel)
            .where(Channel.id == channel_id)
            .values(auto_repost=enabled)
            .returning(Channel)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def update_last_post(
        self,
        channel_id: int,
        post_id: str,
    ) -> Channel | None:
        """
        Update the last processed post ID for a channel.

        Args:
            channel_id: Channel ID
            post_id: Last processed post ID

        Returns:
            Updated channel instance or None
        """
        stmt = (
            update(Channel)
            .where(Channel.id == channel_id)
            .values(last_post_id=post_id)
            .returning(Channel)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def delete_by_user(
        self,
        user_id: int,
        channel_id: int,
    ) -> bool:
        """
        Delete a channel if it belongs to the user.

        Args:
            user_id: User ID
            channel_id: Channel ID

        Returns:
            True if deleted, False otherwise
        """
        stmt = delete(Channel).where(
            Channel.id == channel_id,
            Channel.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    async def find_binding(
        self,
        telegram_channel_id: str,
        user_id: int,
    ) -> Channel | None:
        """
        Find existing channel binding for user.

        Args:
            telegram_channel_id: Telegram channel ID
            user_id: User ID

        Returns:
            Channel instance or None
        """
        stmt = select(Channel).where(
            Channel.telegram_channel_id == telegram_channel_id,
            Channel.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()
