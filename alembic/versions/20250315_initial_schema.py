"""Initial schema

Revision ID: 001
Revises:
Create Date: 2025-03-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all tables."""

    # Create users table
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_id", sa.Integer(), nullable=False),
        sa.Column("balance", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "bonus_received",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_telegram_id"), "users", ["telegram_id"], unique=True)

    # Create channels table
    op.create_table(
        "channels",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("telegram_channel_id", sa.String(length=100), nullable=False),
        sa.Column("telegram_channel_name", sa.String(length=255), nullable=False),
        sa.Column("max_channel_id", sa.String(length=100), nullable=False),
        sa.Column("settings", postgresql.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "auto_repost", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("last_post_id", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "telegram_channel_id", name="uq_user_tg_channel"),
    )
    op.create_index(op.f("ix_channels_user_id"), "channels", ["user_id"])
    op.create_index(
        op.f("ix_channels_telegram_channel_id"),
        "channels",
        ["telegram_channel_id"],
    )
    op.create_index(
        op.f("ix_channels_user_auto_repost"), "channels", ["user_id", "auto_repost"]
    )

    # Create posts table
    op.create_table(
        "posts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "channel_id",
            sa.Integer(),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("telegram_post_id", sa.String(length=100), nullable=False),
        sa.Column("max_post_id", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("media_urls", postgresql.JSON(), nullable=False, server_default="[]"),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("channel_id", "telegram_post_id", name="uq_channel_post"),
    )
    op.create_index(op.f("ix_posts_channel_id"), "posts", ["channel_id"])
    op.create_index(op.f("ix_posts_telegram_post_id"), "posts", ["telegram_post_id"])
    op.create_index(op.f("ix_posts_status"), "posts", ["status"])
    op.create_index(op.f("ix_posts_channel_status"), "posts", ["channel_id", "status"])

    # Create payments table
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("yookassa_payment_id", sa.String(length=100), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("posts_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(op.f("ix_payments_user_id"), "payments", ["user_id"])
    op.create_index(
        op.f("ix_payments_yookassa_payment_id"),
        "payments",
        ["yookassa_payment_id"],
        unique=True,
    )
    op.create_index(op.f("ix_payments_status"), "payments", ["status"])

    # Create promo_codes table
    op.create_table(
        "promo_codes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("posts_bonus", sa.Integer(), nullable=False),
        sa.Column("max_activations", sa.Integer(), nullable=False),
        sa.Column(
            "activated_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "activated_count <= max_activations", name="ck_activations_limit"
        ),
    )
    op.create_index(op.f("ix_promo_codes_code"), "promo_codes", ["code"], unique=True)

    # Create promo_activations table
    op.create_table(
        "promo_activations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "promo_code_id",
            sa.Integer(),
            sa.ForeignKey("promo_codes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "activated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["promo_code_id"], ["promo_codes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("promo_code_id", "user_id", name="uq_promo_user"),
    )

    # Create logs table
    op.create_table(
        "logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("details", postgresql.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(op.f("ix_logs_user_id"), "logs", ["user_id"])
    op.create_index(op.f("ix_logs_action"), "logs", ["action"])
    op.create_index(op.f("ix_logs_created_at"), "logs", ["created_at"])
    op.create_index(op.f("ix_logs_user_created"), "logs", ["user_id", "created_at"])
    op.create_index(op.f("ix_logs_action_created"), "logs", ["action", "created_at"])


def downgrade() -> None:
    """Drop all tables."""
    op.drop_index(op.f("ix_logs_action_created"), table_name="logs")
    op.drop_index(op.f("ix_logs_user_created"), table_name="logs")
    op.drop_index(op.f("ix_logs_created_at"), table_name="logs")
    op.drop_index(op.f("ix_logs_action"), table_name="logs")
    op.drop_index(op.f("ix_logs_user_id"), table_name="logs")
    op.drop_table("logs")

    op.drop_table("promo_activations")

    op.drop_index(op.f("ix_promo_codes_code"), table_name="promo_codes")
    op.drop_table("promo_codes")

    op.drop_index(op.f("ix_payments_status"), table_name="payments")
    op.drop_index(op.f("ix_payments_yookassa_payment_id"), table_name="payments")
    op.drop_index(op.f("ix_payments_user_id"), table_name="payments")
    op.drop_table("payments")

    op.drop_index(op.f("ix_posts_channel_status"), table_name="posts")
    op.drop_index(op.f("ix_posts_status"), table_name="posts")
    op.drop_index(op.f("ix_posts_telegram_post_id"), table_name="posts")
    op.drop_index(op.f("ix_posts_channel_id"), table_name="posts")
    op.drop_table("posts")

    op.drop_index(op.f("ix_channels_user_auto_repost"), table_name="channels")
    op.drop_index(op.f("ix_channels_telegram_channel_id"), table_name="channels")
    op.drop_index(op.f("ix_channels_user_id"), table_name="channels")
    op.drop_table("channels")

    op.drop_index(op.f("ix_users_telegram_id"), table_name="users")
    op.drop_table("users")
