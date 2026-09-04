"""add telegram_chat_id, reminder flags, cancel_token

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
Create Date: 2026-08-27 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("patients") as batch_op:
        batch_op.add_column(sa.Column("telegram_chat_id", sa.String(length=32), nullable=True))
        batch_op.create_unique_constraint("uq_patients_telegram_chat_id", ["telegram_chat_id"])

    with op.batch_alter_table("appointments") as batch_op:
        batch_op.add_column(
            sa.Column("reminder_sent_24h", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("reminder_sent_2h", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column("cancel_token", sa.String(length=64), nullable=True))
        batch_op.create_unique_constraint("uq_appointments_cancel_token", ["cancel_token"])

    # Mavjud (eski) navbatlar uchun cancel_token backfill — bo'lmasa
    # eski appointment'lar Telegram linkida bekor qilinolmaydi.
    import secrets

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id FROM appointments WHERE cancel_token IS NULL")).fetchall()
    for row in rows:
        bind.execute(
            sa.text("UPDATE appointments SET cancel_token = :tok WHERE id = :id"),
            {"tok": secrets.token_urlsafe(32), "id": row.id},
        )


def downgrade() -> None:
    with op.batch_alter_table("appointments") as batch_op:
        batch_op.drop_constraint("uq_appointments_cancel_token", type_="unique")
        batch_op.drop_column("cancel_token")
        batch_op.drop_column("reminder_sent_2h")
        batch_op.drop_column("reminder_sent_24h")

    with op.batch_alter_table("patients") as batch_op:
        batch_op.drop_constraint("uq_patients_telegram_chat_id", type_="unique")
        batch_op.drop_column("telegram_chat_id")
