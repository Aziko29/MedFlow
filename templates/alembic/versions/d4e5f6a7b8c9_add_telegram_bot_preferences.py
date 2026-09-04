"""add telegram bot preferences (language, mute, custom reminder hours)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-27 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("patients") as batch_op:
        batch_op.add_column(
            sa.Column("telegram_language", sa.String(length=2), nullable=False, server_default="uz")
        )
        batch_op.add_column(
            sa.Column("telegram_muted", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("reminder_first_hours", sa.Integer(), nullable=True, server_default="24")
        )
        batch_op.add_column(
            sa.Column("reminder_second_hours", sa.Integer(), nullable=True, server_default="2")
        )


def downgrade() -> None:
    with op.batch_alter_table("patients") as batch_op:
        batch_op.drop_column("reminder_second_hours")
        batch_op.drop_column("reminder_first_hours")
        batch_op.drop_column("telegram_muted")
        batch_op.drop_column("telegram_language")
