"""Base repository with common CRUD operations."""

from typing import TypeVar, Generic, Type, Any

from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """
    Base repository with common CRUD operations.

    Usage:
        class UserRepo(BaseRepository[User]):
            def __init__(self, session: AsyncSession):
                super().__init__(User, session)
    """

    def __init__(self, model: Type[ModelType], session: AsyncSession) -> None:
        """
        Initialize repository.

        Args:
            model: SQLAlchemy model class
            session: Async database session
        """
        self._model = model
        self._session = session

    async def get(self, id: int) -> ModelType | None:
        """
        Get entity by ID.

        Args:
            id: Entity ID

        Returns:
            Entity instance or None
        """
        stmt = select(self._model).where(self._model.id == id)
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def get_all(
        self,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ModelType]:
        """
        Get all entities with pagination.

        Args:
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            List of entity instances
        """
        stmt = select(self._model).offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, **kwargs: Any) -> ModelType:
        """
        Create new entity.

        Args:
            **kwargs: Entity field values

        Returns:
            Created entity instance
        """
        entity = self._model(**kwargs)
        self._session.add(entity)
        await self._session.flush()
        await self._session.refresh(entity)
        return entity

    async def update(self, id: int, **kwargs: Any) -> ModelType | None:
        """
        Update entity by ID.

        Args:
            id: Entity ID
            **kwargs: Fields to update

        Returns:
            Updated entity instance or None
        """
        stmt = (
            update(self._model)
            .where(self._model.id == id)
            .values(**kwargs)
            .returning(self._model)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def delete(self, id: int) -> bool:
        """
        Delete entity by ID.

        Args:
            id: Entity ID

        Returns:
            True if deleted, False if not found
        """
        stmt = delete(self._model).where(self._model.id == id)
        result = await self._session.execute(stmt)
        return result.rowcount > 0

    async def count(self) -> int:
        """
        Count all entities.

        Returns:
            Number of entities
        """
        stmt = select(func.count()).select_from(self._model)
        result = await self._session.execute(stmt)
        return result.scalar() or 0

    async def exists(self, id: int) -> bool:
        """
        Check if entity exists by ID.

        Args:
            id: Entity ID

        Returns:
            True if exists, False otherwise
        """
        stmt = select(self._model.id).where(self._model.id == id)
        result = await self._session.execute(stmt)
        return result.scalar() is not None

    @property
    def session(self) -> AsyncSession:
        """Get the database session."""
        return self._session

    @property
    def model(self) -> Type[ModelType]:
        """Get the model class."""
        return self._model
