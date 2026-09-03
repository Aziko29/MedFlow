"""add patient soft delete fields (is_deleted, deleted_at)

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-09-02 00:00:00.000000

Prompt 6: Soft Delete. Patient jadvaliga ikkita yangi ustun qo'shiladi:
  - is_deleted — bemor "o'chirilganmi" (True bo'lsa, DELETE
    /api/patients/{id} shu bemorni belgilagan; qator DB'da jismonan
    saqlanib qoladi — bog'liq appointments/payments/lab_results tarixi
    yo'qolmaydi).
  - deleted_at — o'chirilgan (soft-delete qilingan) vaqt, audit/tozalash
    uchun.

is_deleted server_default="false" bilan qo'shiladi, shunday qilib
mavjud (eski) bemor yozuvlari avtomatik "faol" holatga o'tadi — hech
kim tasodifan "o'chirilgan" bo'lib qolmaydi. index=True — chunki faol
bemorlar ro'yxati (`WHERE is_deleted = false`) doimiy so'raladigan
filtr bo'ladi.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b4c5d6e7f8a9'
down_revision: Union[str, Sequence[str], None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("patients") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_patients_is_deleted"), ["is_deleted"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("patients") as batch_op:
        batch_op.drop_index(batch_op.f("ix_patients_is_deleted"))
        batch_op.drop_column("deleted_at")
        batch_op.drop_column("is_deleted")
