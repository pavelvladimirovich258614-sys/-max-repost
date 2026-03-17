"""Add max_channel_bindings table

Revision ID: 002
Revises: 001
Create Date: 2025-03-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create max_channel_bindings table."""
    op.create_table(
        "max_channel_bindings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tg_channel", sa.String(100), nullable=False),
        sa.Column("tg_channel_id", sa.String(100), nullable=False),
        sa.Column("max_chat_id", sa.String(100), nullable=False),
        sa.Column("max_channel_name", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "tg_channel", "max_chat_id",
            name="uq_user_tg_max"
        ),
    )
    
    # Create indexes
    op.create_index(
        "ix_max_bindings_user_id",
        "max_channel_bindings",
        ["user_id"],
    )
    op.create_index(
        "ix_max_bindings_tg_channel",
        "max_channel_bindings",
        ["tg_channel"],
    )
    op.create_index(
        "ix_max_bindings_user_lookup",
        "max_channel_bindings",
        ["user_id", "tg_channel", "last_used_at"],
    )


def downgrade() -> None:
    """Drop max_channel_bindings table."""
    op.drop_index("ix_max_bindings_user_lookup", table_name="max_channel_bindings")
    op.drop_index("ix_max_bindings_tg_channel", table_name="max_channel_bindings")
    op.drop_index("ix_max_bindings_user_id", table_name="max_channel_bindings")
    op.drop_table("max_channel_bindings")
