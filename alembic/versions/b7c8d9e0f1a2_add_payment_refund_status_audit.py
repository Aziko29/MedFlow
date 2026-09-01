"""add payment status + two-step refund audit fields (cancelled_by, refunded_by)

Revision ID: b7c8d9e0f1a2
Revises: f2a3b4c5d6e7
Create Date: 2026-09-01 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, Sequence[str], None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("payments") as batch_op:
        batch_op.add_column(
            sa.Column("status", sa.String(), nullable=False, server_default="completed")
        )
        batch_op.add_column(sa.Column("cancelled_by_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("cancelled_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("refunded_by_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("refunded_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("refund_reason", sa.String(), nullable=True))
        batch_op.create_foreign_key(
            "fk_payments_cancelled_by_id_users", "users", ["cancelled_by_id"], ["id"]
        )
        batch_op.create_foreign_key(
            "fk_payments_refunded_by_id_users", "users", ["refunded_by_id"], ["id"]
        )
    # Mavjud eski qaytarim yozuvlari (is_refund=True) va ular tegishli
    # bo'lgan asl to'lovlar allaqachon "yakunlangan" hisoblanadi — status
    # ustuni server_default="completed" bilan avtomatik to'g'ri qiymat oladi.


def downgrade() -> None:
    with op.batch_alter_table("payments") as batch_op:
        batch_op.drop_constraint("fk_payments_refunded_by_id_users", type_="foreignkey")
        batch_op.drop_constraint("fk_payments_cancelled_by_id_users", type_="foreignkey")
        batch_op.drop_column("refund_reason")
        batch_op.drop_column("refunded_at")
        batch_op.drop_column("refunded_by_id")
        batch_op.drop_column("cancelled_at")
        batch_op.drop_column("cancelled_by_id")
        batch_op.drop_column("status")
