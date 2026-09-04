"""add patient room admission fields (room_number, admitted_at, discharged_at, is_admitted)

Revision ID: c4d5e6f7a8b9
Revises: b7c8d9e0f1a2
Create Date: 2026-09-01 00:00:00.000000

Prompt 7: statsionar davolash uchun palata tizimi. Patient jadvaliga
to'rtta yangi ustun qo'shiladi:
  - room_number   — bemor hozir (yoki oxirgi marta) yotgan palata raqami
  - admitted_at   — palataga yotqizilgan vaqt
  - discharged_at — palatadan chiqarilgan vaqt
  - is_admitted   — bemor HOZIR palatada yotibdimi (True/False)

is_admitted server_default="false" bilan qo'shiladi, shunday qilib
mavjud (eski) bemor yozuvlari avtomatik "palatada emas" holatiga
o'tadi — hech kim "yashirincha yotqizilgan" bo'lib qolmaydi.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, Sequence[str], None] = 'b7c8d9e0f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("patients") as batch_op:
        batch_op.add_column(sa.Column("room_number", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("admitted_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("discharged_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "is_admitted", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("patients") as batch_op:
        batch_op.drop_column("is_admitted")
        batch_op.drop_column("discharged_at")
        batch_op.drop_column("admitted_at")
        batch_op.drop_column("room_number")
