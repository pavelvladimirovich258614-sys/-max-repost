"""Log repository for logging user actions."""

from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Log
from bot.database.repositories.base import BaseRepository


class LogRepository(BaseRepository[Log]):
    """Repository for Log entity operations."""

    def __init__(self, session: AsyncSession) -> None:
        """
        Initialize Log repository.

        Args:
            session: Async database session
        """
        super().__init__(Log, session)

    async def log_action(
        self,
        user_id: int | None,
        action: str,
        details: dict | None = None,
    ) -> Log:
        """
        Log a user action.

        Args:
            user_id: User ID (None for anonymous actions)
            action: Action description
            details: Additional details as JSON

        Returns:
            Created Log entry
        """
        return await self.create(
            user_id=user_id,
            action=action,
            details=details or {},
        )
