"""Database models using SQLAlchemy ORM."""

from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    String,
    Integer,
    Boolean,
    DateTime,
    Text,
    ForeignKey,
    UniqueConstraint,
    Index,
    JSON,
    func,
    CheckConstraint,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)
from sqlalchemy.types import ARRAY


class Base(DeclarativeBase):
    """Base class for all database models."""

    pass


class PostStatus(PyEnum):
    """Status of a reposted post."""

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class PaymentStatus(PyEnum):
    """Status of a payment."""

    PENDING = "pending"
    PAID = "paid"
    CANCELED = "canceled"
    REFUNDED = "refunded"


# Mixin for timestamps
class TimestampMixin:
    """Mixin for created_at and updated_at timestamps."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class User(Base, TimestampMixin):
    """
    User model representing Telegram users.

    Attributes:
        id: Primary key
        telegram_id: Unique Telegram user ID
        balance: Current balance in posts (internal currency)
        bonus_received: Whether user received bonus for channel subscription
        is_admin: Admin privileges flag
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(
        Integer,
        unique=True,
        index=True,
        nullable=False,
    )
    balance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bonus_received: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    channels: Mapped[list["Channel"]] = relationship(
        "Channel",
        back_populates="user",
        cascade="all, delete",
        passive_deletes=True,
    )
    payments: Mapped[list["Payment"]] = relationship(
        "Payment",
        back_populates="user",
        cascade="all, delete",
        passive_deletes=True,
    )
    promo_activations: Mapped[list["PromoActivation"]] = relationship(
        "PromoActivation",
        back_populates="user",
        cascade="all, delete",
        passive_deletes=True,
    )
    logs: Mapped[list["Log"]] = relationship(
        "Log",
        back_populates="user",
        cascade="all, delete",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, telegram_id={self.telegram_id}, balance={self.balance})>"


class Channel(Base, TimestampMixin):
    """
    Channel model for TG -> Max bindings.

    Attributes:
        id: Primary key
        user_id: Foreign key to user
        telegram_channel_id: Telegram channel ID (as string for large IDs)
        telegram_channel_name: Channel username/title
        max_channel_id: Max messenger channel ID
        settings: JSONB settings for repost behavior
        auto_repost: Enable automatic reposting
        last_post_id: Last processed post ID
    """

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    telegram_channel_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    telegram_channel_name: Mapped[str] = mapped_column(String(255), nullable=False)
    max_channel_id: Mapped[str] = mapped_column(String(100), nullable=False)
    settings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    auto_repost: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_post_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="channels")
    posts: Mapped[list["Post"]] = relationship(
        "Post",
        back_populates="channel",
        cascade="all, delete",
        passive_deletes=True,
    )

    # Indexes
    __table_args__ = (
        Index("ix_channels_user_auto_repost", "user_id", "auto_repost"),
        UniqueConstraint("user_id", "telegram_channel_id", name="uq_user_tg_channel"),
    )

    def __repr__(self) -> str:
        return f"<Channel(id={self.id}, tg_id={self.telegram_channel_id}, max_id={self.max_channel_id})>"


class Post(Base, TimestampMixin):
    """
    Post model for tracking reposted content.

    Attributes:
        id: Primary key
        channel_id: Foreign key to channel
        telegram_post_id: Original Telegram post ID (unique per channel)
        max_post_id: Resulting Max messenger post ID
        status: Post processing status
        media_urls: JSONB array of media URLs
        content: Post text content
    """

    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    telegram_post_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    max_post_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        default=PostStatus.PENDING.value,
        nullable=False,
        index=True,
    )
    media_urls: Mapped[dict] = mapped_column(JSON, default=list, nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    channel: Mapped["Channel"] = relationship("Channel", back_populates="posts")

    # Indexes
    __table_args__ = (
        UniqueConstraint("channel_id", "telegram_post_id", name="uq_channel_post"),
        Index("ix_posts_channel_status", "channel_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<Post(id={self.id}, tg_post_id={self.telegram_post_id}, status={self.status})>"


class Payment(Base, TimestampMixin):
    """
    Payment model for YooKassa transactions.

    Attributes:
        id: Primary key
        user_id: Foreign key to user
        yookassa_payment_id: YooKassa payment ID
        amount: Payment amount in kopecks (integer)
        posts_count: Number of posts purchased
        status: Payment status
    """

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    yookassa_payment_id: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        index=True,
        nullable=False,
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)  # in kopecks
    posts_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default=PaymentStatus.PENDING.value,
        nullable=False,
        index=True,
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="payments")

    def __repr__(self) -> str:
        return f"<Payment(id={self.id}, yookassa_id={self.yookassa_payment_id}, status={self.status})>"


class PromoCode(Base, TimestampMixin):
    """
    Promo code model for bonus posts.

    Attributes:
        id: Primary key
        code: Unique promo code string
        posts_bonus: Number of bonus posts
        max_activations: Maximum times code can be used
        activated_count: Current activation count
        expires_at: Expiration timestamp
    """

    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        index=True,
        nullable=False,
    )
    posts_bonus: Mapped[int] = mapped_column(Integer, nullable=False)
    max_activations: Mapped[int] = mapped_column(Integer, nullable=False)
    activated_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    activations: Mapped[list["PromoActivation"]] = relationship(
        "PromoActivation",
        back_populates="promo_code",
        cascade="all, delete",
        passive_deletes=True,
    )

    # Constraints
    __table_args__ = (
        CheckConstraint("activated_count <= max_activations", name="ck_activations_limit"),
    )

    def __repr__(self) -> str:
        return f"<PromoCode(code={self.code}, bonus={self.posts_bonus})>"


class PromoActivation(Base):
    """
    Promo code activation tracking.

    Attributes:
        id: Primary key
        promo_code_id: Foreign key to promo code
        user_id: Foreign key to user
        activated_at: Activation timestamp
    """

    __tablename__ = "promo_activations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    promo_code_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("promo_codes.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    promo_code: Mapped["PromoCode"] = relationship("PromoCode", back_populates="activations")
    user: Mapped["User"] = relationship("User", back_populates="promo_activations")

    # Unique constraint: one activation per user per promo code
    __table_args__ = (
        UniqueConstraint("promo_code_id", "user_id", name="uq_promo_user"),
    )

    def __repr__(self) -> str:
        return f"<PromoActivation(id={self.id}, promo_id={self.promo_code_id}, user_id={self.user_id})>"


class Log(Base):
    """
    Audit log for user actions.

    Attributes:
        id: Primary key
        user_id: Foreign key to user (nullable for system logs)
        action: Action type/identifier
        details: JSONB details of the action
        created_at: Timestamp
    """

    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="logs")

    # Index for querying recent logs
    __table_args__ = (
        Index("ix_logs_user_created", "user_id", "created_at"),
        Index("ix_logs_action_created", "action", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Log(id={self.id}, action={self.action}, user_id={self.user_id})>"
