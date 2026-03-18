"""Add user_balances and autopost_subscriptions tables

Revision ID: 003
Revises: 002
Create Date: 2025-03-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create user_balances and autopost_subscriptions tables."""
    
    # Create user_balances table
    op.create_table(
        "user_balances",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("balance_rub", sa.DECIMAL(precision=10, scale=2), nullable=False, server_default="0.00"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_user_balance"),
    )
    
    # Create indexes for user_balances
    op.create_index(
        "ix_user_balances_user_id",
        "user_balances",
        ["user_id"],
    )
    
    # Create autopost_subscriptions table
    op.create_table(
        "autopost_subscriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tg_channel", sa.String(100), nullable=False),
        sa.Column("tg_channel_id", sa.String(100), nullable=True),
        sa.Column("max_chat_id", sa.String(100), nullable=False),
        sa.Column("max_channel_name", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("posts_transferred", sa.Integer(), nullable=False, server_default="0"),
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
        sa.Column("last_post_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_reason", sa.String(50), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "tg_channel", "max_chat_id",
            name="uq_autopost_user_tg_max"
        ),
    )
    
    # Create indexes for autopost_subscriptions
    op.create_index(
        "ix_autopost_subscriptions_user_id",
        "autopost_subscriptions",
        ["user_id"],
    )
    op.create_index(
        "ix_autopost_subscriptions_tg_channel",
        "autopost_subscriptions",
        ["tg_channel"],
    )
    op.create_index(
        "ix_autopost_user_lookup",
        "autopost_subscriptions",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_autopost_active",
        "autopost_subscriptions",
        ["user_id", "is_active"],
    )


def downgrade() -> None:
    """Drop user_balances and autopost_subscriptions tables."""
    op.drop_index("ix_autopost_active", table_name="autopost_subscriptions")
    op.drop_index("ix_autopost_user_lookup", table_name="autopost_subscriptions")
    op.drop_index("ix_autopost_subscriptions_tg_channel", table_name="autopost_subscriptions")
    op.drop_index("ix_autopost_subscriptions_user_id", table_name="autopost_subscriptions")
    op.drop_table("autopost_subscriptions")
    
    op.drop_index("ix_user_balances_user_id", table_name="user_balances")
    op.drop_table("user_balances")
