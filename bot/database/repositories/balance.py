"""Balance repositories for user balance operations."""

from decimal import Decimal
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import UserBalance, BalanceTransaction
from bot.database.repositories.base import BaseRepository


class UserBalanceRepository(BaseRepository[UserBalance]):
    """Repository for user balance operations."""
    
    def __init__(self, session: AsyncSession) -> None:
        """
        Initialize UserBalance repository.
        
        Args:
            session: Async database session
        """
        super().__init__(UserBalance, session)
    
    async def get_by_user_id(self, user_id: int) -> UserBalance | None:
        """
        Get balance by Telegram user ID.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            UserBalance instance or None
        """
        stmt = select(UserBalance).where(UserBalance.user_id == user_id)
        result = await self._session.execute(stmt)
        return result.scalars().first()
    
    async def get_or_create(self, user_id: int) -> tuple[UserBalance, bool]:
        """
        Get existing balance or create new one.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            Tuple of (UserBalance instance, created flag)
        """
        balance = await self.get_by_user_id(user_id)
        if balance is not None:
            return balance, False
        
        balance = await self.create(
            user_id=user_id,
            balance=Decimal("0.00"),
            total_deposited=Decimal("0.00"),
            total_spent=Decimal("0.00"),
        )
        return balance, True
    
    async def update_balance(
        self,
        user_id: int,
        amount: Decimal,
        is_deposit: bool = True,
    ) -> UserBalance | None:
        """
        Update user balance atomically.
        
        Args:
            user_id: Telegram user ID
            amount: Amount to add (positive) or subtract (negative)
            is_deposit: True for deposit, False for charge
            
        Returns:
            Updated UserBalance instance or None
        """
        balance = await self.get_by_user_id(user_id)
        if balance is None:
            return None
        
        if is_deposit:
            balance.balance += amount
            balance.total_deposited += amount
        else:
            balance.balance -= amount
            balance.total_spent += amount
        
        await self._session.flush()
        await self._session.refresh(balance)
        return balance
    
    async def get_balance(self, user_id: int) -> Decimal:
        """
        Get current balance for user.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            Current balance in rubles
        """
        balance = await self.get_by_user_id(user_id)
        if balance is None:
            return Decimal("0.00")
        return balance.balance
    
    async def has_sufficient_balance(
        self,
        user_id: int,
        amount: Decimal,
    ) -> bool:
        """
        Check if user has sufficient balance.
        
        Args:
            user_id: Telegram user ID
            amount: Required amount
            
        Returns:
            True if balance is sufficient, False otherwise
        """
        balance = await self.get_balance(user_id)
        return balance >= amount


class BalanceTransactionRepository(BaseRepository[BalanceTransaction]):
    """Repository for balance transaction operations."""
    
    def __init__(self, session: AsyncSession) -> None:
        """
        Initialize BalanceTransaction repository.
        
        Args:
            session: Async database session
        """
        super().__init__(BalanceTransaction, session)
    
    async def get_by_user_id(
        self,
        user_id: int,
        limit: int = 20,
        offset: int = 0,
    ) -> list[BalanceTransaction]:
        """
        Get transactions for a user.
        
        Args:
            user_id: Telegram user ID
            limit: Maximum number of results
            offset: Number of results to skip
            
        Returns:
            List of BalanceTransaction instances
        """
        stmt = (
            select(BalanceTransaction)
            .where(BalanceTransaction.user_id == user_id)
            .order_by(BalanceTransaction.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def get_by_type(
        self,
        transaction_type: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[BalanceTransaction]:
        """
        Get transactions by type.
        
        Args:
            transaction_type: Transaction type (deposit, autopost_charge, etc.)
            limit: Maximum number of results
            offset: Number of results to skip
            
        Returns:
            List of BalanceTransaction instances
        """
        stmt = (
            select(BalanceTransaction)
            .where(BalanceTransaction.transaction_type == transaction_type)
            .order_by(BalanceTransaction.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
    
    async def create_transaction(
        self,
        user_id: int,
        amount: Decimal,
        transaction_type: str,
        description: str | None = None,
    ) -> BalanceTransaction:
        """
        Create a new transaction record.
        
        Args:
            user_id: Telegram user ID
            amount: Transaction amount (positive for deposit, negative for charge)
            transaction_type: Type of transaction
            description: Optional description
            
        Returns:
            Created BalanceTransaction instance
        """
        return await self.create(
            user_id=user_id,
            amount=amount,
            transaction_type=transaction_type,
            description=description,
        )
    
    async def get_user_stats(self, user_id: int) -> dict[str, Any]:
        """
        Get transaction statistics for a user.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            Dictionary with transaction statistics
        """
        stmt = select(BalanceTransaction).where(BalanceTransaction.user_id == user_id)
        result = await self._session.execute(stmt)
        transactions = result.scalars().all()
        
        deposits = [t for t in transactions if t.amount > 0]
        charges = [t for t in transactions if t.amount < 0]
        
        return {
            "total_transactions": len(transactions),
            "total_deposits": len(deposits),
            "total_charges": len(charges),
            "total_deposited": sum(t.amount for t in deposits),
            "total_charged": sum(abs(t.amount) for t in charges),
        }
    
    async def get_referral_earnings(self, user_id: int) -> Decimal:
        """
        Get total referral earnings for a user.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            Total referral earnings in rubles
        """
        stmt = select(func.sum(BalanceTransaction.amount)).where(
            BalanceTransaction.user_id == user_id,
            BalanceTransaction.transaction_type == "referral_bonus"
        )
        result = await self._session.execute(stmt)
        total = result.scalar()
        return total if total is not None else Decimal("0.00")
