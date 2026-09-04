"""add failed_login_attempts/locked_until to users (Prompt 19 - login lockout)

Revision ID: b7c8d9e0f1a3
Revises: a2b3c4d5e6f7
Create Date: 2026-09-03 00:00:00.000000

⚠️ ID TUZATILDI (2026-09-04): bu fayl avval 'add_payment_refund_status_audit.py'
bilan bir xil 'b7c8d9e0f1a2' revision ID'ni ishlatgan (ikkalasi
mustaqil ravishda, bir-biridan bexabar, tasodifan bir xil ID bilan
yaratilgan — real hayotda ikki xil branch alohida ishlab, keyin merge
qilinganda yuz beradigan tipik holat). Yaratilgan sana (2026-09-03)
'payment_refund'nikidan (2026-09-01) keyinroq bo'lgani uchun — aynan
SHU fayl (keyinroq qo'shilgan nusxa) 'b7c8d9e0f1a3'ga o'zgartirildi;
'payment_refund_status_audit.py' o'z asl ID'sini ('b7c8d9e0f1a2')
saqlab qoldi. Bu yerdagi down_revision ('a2b3c4d5e6f7') o'zgarishsiz —
u har doim ham to'g'ri bo'lgan."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b7c8d9e0f1a3'
down_revision: Union[str, Sequence[str], None] = 'a2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("locked_until", sa.DateTime(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("locked_until")
        batch_op.drop_column("failed_login_attempts")
