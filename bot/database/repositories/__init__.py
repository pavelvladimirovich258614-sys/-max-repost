"""Database repositories for data access."""

from bot.database.repositories.base import BaseRepository
from bot.database.repositories.user import UserRepository
from bot.database.repositories.channel import ChannelRepository
from bot.database.repositories.post import PostRepository
from bot.database.repositories.payment import PaymentRepository
from bot.database.repositories.promo import PromoCodeRepository, PromoActivationRepository
from bot.database.repositories.log import LogRepository
from bot.database.repositories.max_channel_binding import MaxChannelBindingRepository

__all__ = [
    "BaseRepository",
    "UserRepository",
    "ChannelRepository",
    "PostRepository",
    "PaymentRepository",
    "PromoCodeRepository",
    "PromoActivationRepository",
    "LogRepository",
    "MaxChannelBindingRepository",
]
