"""Database middleware for injecting session and repositories into handlers."""

from collections.abc import Callable, Awaitable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.database.connection import get_session
from bot.database.repositories import (
    UserRepository,
    ChannelRepository,
    PostRepository,
    PaymentRepository,
    PromoCodeRepository,
    PromoActivationRepository,
    LogRepository,
    VerifiedChannelRepository,
)
from bot.database.repositories.balance import (
    UserBalanceRepository,
    BalanceTransactionRepository,
)
from bot.database.repositories.autopost_subscription import AutopostSubscriptionRepository
from bot.database.repositories.max_channel_binding import MaxChannelBindingRepository
from bot.database.repositories.yookassa_payment import YooKassaPaymentRepository
from bot.database.repositories.transferred_post import TransferredPostRepository


class DBMiddleware(BaseMiddleware):
    """
    Middleware that creates a database session and injects repositories.

    Session is automatically committed on success or rolled back on error.
    Repositories are available in handlers via data["repo_name"].
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """
        Process update with database session.

        Args:
            handler: Next handler/middleware in chain
            event: Telegram update object
            data: Context data passed to handlers

        Returns:
            Handler result
        """
        async with get_session() as session:
            # Inject session (both keys for compatibility)
            data["session"] = session
            data["db_session"] = session

            # Inject repositories
            data["user_repo"] = UserRepository(session)
            data["channel_repo"] = ChannelRepository(session)
            data["post_repo"] = PostRepository(session)
            data["payment_repo"] = PaymentRepository(session)
            data["promo_repo"] = PromoCodeRepository(session)
            data["promo_activation_repo"] = PromoActivationRepository(session)
            data["log_repo"] = LogRepository(session)
            data["verified_channel_repo"] = VerifiedChannelRepository(session)
            data["balance_repo"] = UserBalanceRepository(session)
            data["transaction_repo"] = BalanceTransactionRepository(session)
            data["autopost_sub_repo"] = AutopostSubscriptionRepository(session)
            data["max_binding_repo"] = MaxChannelBindingRepository(session)
            data["yookassa_payment_repo"] = YooKassaPaymentRepository(session)
            data["transferred_post_repo"] = TransferredPostRepository(session)

            return await handler(event, data)
