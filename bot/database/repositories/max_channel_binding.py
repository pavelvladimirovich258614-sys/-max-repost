"""Repository for MaxChannelBinding entity operations."""

from datetime import datetime
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import MaxChannelBinding
from bot.database.repositories.base import BaseRepository


class MaxChannelBindingRepository(BaseRepository[MaxChannelBinding]):
    """Repository for MaxChannelBinding entity operations."""

    def __init__(self, session: AsyncSession) -> None:
        """
        Initialize MaxChannelBinding repository.

        Args:
            session: Async database session
        """
        super().__init__(MaxChannelBinding, session)

    async def get_by_user_and_tg_channel(
        self,
        user_id: int,
        tg_channel: str,
    ) -> list[MaxChannelBinding]:
        """
        Get all Max channel bindings for a user and TG channel.
        
        Returns bindings ordered by last_used_at (most recent first).

        Args:
            user_id: Telegram user ID
            tg_channel: Telegram channel username

        Returns:
            List of MaxChannelBinding instances
        """
        stmt = (
            select(MaxChannelBinding)
            .where(
                MaxChannelBinding.user_id == user_id,
                MaxChannelBinding.tg_channel == tg_channel,
            )
            .order_by(MaxChannelBinding.last_used_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_user(
        self,
        user_id: int,
    ) -> list[MaxChannelBinding]:
        """
        Get all Max channel bindings for a user.

        Args:
            user_id: Telegram user ID

        Returns:
            List of MaxChannelBinding instances
        """
        stmt = (
            select(MaxChannelBinding)
            .where(MaxChannelBinding.user_id == user_id)
            .order_by(MaxChannelBinding.last_used_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def find_binding(
        self,
        user_id: int,
        tg_channel: str,
        max_chat_id: str,
    ) -> MaxChannelBinding | None:
        """
        Find specific binding by user, TG channel and Max chat_id.

        Args:
            user_id: Telegram user ID
            tg_channel: Telegram channel username
            max_chat_id: Max channel chat_id

        Returns:
            MaxChannelBinding instance or None
        """
        stmt = select(MaxChannelBinding).where(
            MaxChannelBinding.user_id == user_id,
            MaxChannelBinding.tg_channel == tg_channel,
            MaxChannelBinding.max_chat_id == max_chat_id,
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def create_or_update(
        self,
        user_id: int,
        tg_channel: str,
        tg_channel_id: str,
        max_chat_id: str,
        max_channel_name: str | None = None,
    ) -> MaxChannelBinding:
        """
        Create new binding or update existing one (update last_used_at).
        
        If binding with same (user_id, tg_channel, max_chat_id) exists,
        updates last_used_at and max_channel_name.
        Otherwise creates new binding.

        Args:
            user_id: Telegram user ID
            tg_channel: Telegram channel username
            tg_channel_id: Telegram channel ID
            max_chat_id: Max channel chat_id
            max_channel_name: Optional Max channel name

        Returns:
            MaxChannelBinding instance
        """
        # Try to find existing binding
        existing = await self.find_binding(user_id, tg_channel, max_chat_id)
        
        if existing:
            # Update existing
            stmt = (
                update(MaxChannelBinding)
                .where(MaxChannelBinding.id == existing.id)
                .values(
                    last_used_at=datetime.utcnow(),
                    max_channel_name=max_channel_name or existing.max_channel_name,
                )
                .returning(MaxChannelBinding)
            )
            result = await self._session.execute(stmt)
            return result.scalars().first()
        else:
            # Create new
            binding = MaxChannelBinding(
                user_id=user_id,
                tg_channel=tg_channel,
                tg_channel_id=tg_channel_id,
                max_chat_id=max_chat_id,
                max_channel_name=max_channel_name,
            )
            self._session.add(binding)
            await self._session.flush()
            return binding

    async def update_last_used(
        self,
        binding_id: int,
    ) -> MaxChannelBinding | None:
        """
        Update last_used_at timestamp for a binding.

        Args:
            binding_id: Binding ID

        Returns:
            Updated MaxChannelBinding instance or None
        """
        stmt = (
            update(MaxChannelBinding)
            .where(MaxChannelBinding.id == binding_id)
            .values(last_used_at=datetime.utcnow())
            .returning(MaxChannelBinding)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def delete_binding(
        self,
        user_id: int,
        binding_id: int,
    ) -> bool:
        """
        Delete a binding if it belongs to the user.

        Args:
            user_id: User ID
            binding_id: Binding ID

        Returns:
            True if deleted, False otherwise
        """
        stmt = delete(MaxChannelBinding).where(
            MaxChannelBinding.id == binding_id,
            MaxChannelBinding.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    async def delete_by_user_and_tg_channel(
        self,
        user_id: int,
        tg_channel: str,
    ) -> int:
        """
        Delete all bindings for a user and TG channel.

        Args:
            user_id: User ID
            tg_channel: Telegram channel username

        Returns:
            Number of deleted bindings
        """
        stmt = delete(MaxChannelBinding).where(
            MaxChannelBinding.user_id == user_id,
            MaxChannelBinding.tg_channel == tg_channel,
        )
        result = await self._session.execute(stmt)
        return result.rowcount
