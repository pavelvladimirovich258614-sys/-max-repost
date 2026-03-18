"""Database models using SQLAlchemy ORM."""

from datetime import datetime
from decimal import Decimal
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
from sqlalchemy.types import ARRAY, DECIMAL


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
        free_posts_used: Number of free trial posts already used (max 5)
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
    free_posts_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)

    # Constants
    FREE_POSTS_LIMIT = 5  # Maximum free posts per user

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


class MaxChannelBinding(Base):
        """
        Storage for TG -> Max channel bindings for transfer feature.
        
        Allows users to quickly reuse previously configured Max channels.
        
        Attributes:
            id: Primary key
            user_id: Telegram user ID
            tg_channel: Telegram channel username/link
            tg_channel_id: Telegram channel ID (numeric)
            max_chat_id: Max channel chat_id (numeric)
            max_channel_name: Optional name of Max channel
            created_at: When binding was created
            last_used_at: When binding was last used
        """
        
        __tablename__ = "max_channel_bindings"
        
        id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
        user_id: Mapped[int] = mapped_column(
            Integer,
            nullable=False,
            index=True,
        )
        tg_channel: Mapped[str] = mapped_column(
            String(100),
            nullable=False,
            index=True,
        )
        tg_channel_id: Mapped[str] = mapped_column(String(100), nullable=False)
        max_chat_id: Mapped[str] = mapped_column(String(100), nullable=False)
        max_channel_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
        created_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False,
        )
        last_used_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
        )
        
        # Index for fast lookup by user + tg_channel
        __table_args__ = (
            UniqueConstraint("user_id", "tg_channel", "max_chat_id", name="uq_user_tg_max"),
            Index("ix_max_bindings_user_lookup", "user_id", "tg_channel", "last_used_at"),
        )
        
        def __repr__(self) -> str:
            return f"<MaxChannelBinding(user_id={self.user_id}, tg={self.tg_channel}, max={self.max_chat_id})>"


class VerifiedChannel(Base):
    """
    Storage for verified Telegram channels.
    
    Users who have verified channel ownership don't need to verify again.
    
    Attributes:
        id: Primary key
        user_id: Telegram user ID
        tg_channel: Telegram channel username
        tg_channel_id: Telegram channel numeric ID
        verified_at: When verification was completed
    """
    
    __tablename__ = "verified_channels"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )
    tg_channel: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    tg_channel_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    
    # Unique constraint: one verification per user per channel
    __table_args__ = (
        UniqueConstraint("user_id", "tg_channel", name="uq_user_verified_channel"),
        Index("ix_verified_channels_user_lookup", "user_id", "verified_at"),
    )
    
    def __repr__(self) -> str:
        return f"<VerifiedChannel(user_id={self.user_id}, tg={self.tg_channel})>"


class TransferredPost(Base):
    """
    Track transferred posts to prevent duplicates.
    
    When a post is transferred from TG to Max, record it here.
    On subsequent transfers, skip already-transferred posts.
    
    Attributes:
        id: Primary key
        user_id: Telegram user ID who performed the transfer
        tg_channel: Telegram channel username
        max_chat_id: Max channel chat_id (as string)
        tg_message_id: Telegram message ID that was transferred
        transferred_at: When the transfer occurred
    """
    
    __tablename__ = "transferred_posts"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )
    tg_channel: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    max_chat_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    tg_message_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    transferred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    
    # Unique constraint: one record per post per channel combination
    __table_args__ = (
        UniqueConstraint("tg_channel", "max_chat_id", "tg_message_id", name="uq_transferred_post"),
        Index("ix_transferred_posts_lookup", "tg_channel", "max_chat_id", "tg_message_id"),
    )
    
    def __repr__(self) -> str:
        return f"<TransferredPost(tg={self.tg_channel}, msg_id={self.tg_message_id}, max={self.max_chat_id})>"


class AutopostSubscription(Base):
    """
    Autopost subscription for TG -> Max channel pairs.
    
    Tracks active autoposting subscriptions with statistics.
    
    Attributes:
        id: Primary key
        user_id: Telegram user ID
        tg_channel: Telegram channel username
        tg_channel_id: Telegram channel numeric ID
        max_chat_id: Max channel chat_id
        max_channel_name: Optional Max channel name
        is_active: Whether autoposting is currently active
        posts_transferred: Total number of posts transferred
        created_at: When subscription was created
        updated_at: Last update timestamp
        last_post_at: When last post was transferred
        paused_reason: Reason for pausing (e.g., 'insufficient_funds')
    """
    
    __tablename__ = "autopost_subscriptions"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )
    tg_channel: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    tg_channel_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    max_chat_id: Mapped[str] = mapped_column(String(100), nullable=False)
    max_channel_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    posts_transferred: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
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
    last_post_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    paused_reason: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    # Balance-related fields
    cost_per_post: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 2),
        default=Decimal("3.00"),
        nullable=False,
    )
    total_spent: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 2),
        default=Decimal("0.00"),
        nullable=False,
    )
    last_post_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    
    # Unique constraint: one subscription per user per TG channel per Max channel
    __table_args__ = (
        UniqueConstraint(
            "user_id", "tg_channel", "max_chat_id",
            name="uq_autopost_user_tg_max"
        ),
        Index("ix_autopost_user_lookup", "user_id", "created_at"),
        Index("ix_autopost_active", "user_id", "is_active"),
    )
    
    def __repr__(self) -> str:
        return (
            f"<AutopostSubscription(user_id={self.user_id}, "
            f"tg={self.tg_channel}, max={self.max_chat_id}, "
            f"active={self.is_active})>"
        )


class YooKassaPayment(Base):
    """
    YooKassa payment tracking for balance top-ups.
    
    Attributes:
        id: Primary key
        user_id: Telegram user ID
        payment_id: YooKassa payment ID (unique)
        amount: Amount in rubles (Decimal)
        status: pending/succeeded/canceled
        created_at: When payment was created
        updated_at: When payment was last updated
    """
    
    __tablename__ = "yookassa_payments"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )
    payment_id: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        index=True,
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 2),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        nullable=False,
        index=True,
    )
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
    
    __table_args__ = (
        Index("ix_yookassa_payments_user_created", "user_id", "created_at"),
    )


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


class UserBalance(Base, TimestampMixin):
    """
    User balance model for tracking ruble balance.
    
    Attributes:
        id: Primary key
        user_id: Telegram user ID (not internal user ID)
        balance: Current balance in rubles
        total_deposited: Total amount deposited
        total_spent: Total amount spent
        created_at: When balance record was created
        updated_at: When balance record was last updated
    """
    
    __tablename__ = "user_balances"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        unique=True,
        index=True,
        nullable=False,
    )
    balance: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 2),
        default=Decimal("0.00"),
        nullable=False,
    )
    total_deposited: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 2),
        default=Decimal("0.00"),
        nullable=False,
    )
    total_spent: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 2),
        default=Decimal("0.00"),
        nullable=False,
    )
    
    def __repr__(self) -> str:
        return f"<UserBalance(user_id={self.user_id}, balance={self.balance})>"


class BalanceTransaction(Base):
    """
    Balance transaction history model.
    
    Attributes:
        id: Primary key
        user_id: Telegram user ID
        amount: Transaction amount (positive for deposit, negative for charge)
        transaction_type: Type of transaction (deposit, autopost_charge, admin_topup, refund)
        description: Human-readable description of the transaction
        created_at: When transaction was created
    """
    
    __tablename__ = "balance_transactions"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        index=True,
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 2),
        nullable=False,
    )
    transaction_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )
    description: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    
    # Indexes
    __table_args__ = (
        Index("ix_balance_transactions_user_created", "user_id", "created_at"),
    )
    
    def __repr__(self) -> str:
        return f"<BalanceTransaction(user_id={self.user_id}, amount={self.amount}, type={self.transaction_type})>"
