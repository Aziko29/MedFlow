"""add theme_preference to users (light/dark/auto UI mode, Prompt 21)

Revision ID: g1h2i3j4k5l6
Revises: b7c8d9e0f1a3
Create Date: 2026-09-03 00:00:00.000000

⚠️ ESLATMA YANGILANDI (2026-09-04): bu faylning down_revision'i avval
'b7c8d9e0f1a2' edi — o'sha ID ikkita alohida faylda takrorlangani
uchun `alembic upgrade head` "Multiple head revisions" xatosi bilan
to'xtar edi (qarang b7c8d9e0f1a3_add_login_lockout_fields.py'dagi
izoh). Nosozlik manbasi — 'add_login_lockout_fields.py' (2026-09-03,
Prompt 19) 'b7c8d9e0f1a2'ni endi 'b7c8d9e0f1a3'ga o'zgartirdi (chunki
u ID'ni ikkinchi bo'lib, 'add_payment_refund_status_audit.py'dan
[2026-09-01] keyin, tasodifan qayta ishlatgan edi). Ushbu (Prompt 21)
migratsiya sana/prompt-tartib bo'yicha aynan login-lockout'dan keyin
kelgani uchun endi down_revision shu yangi ID'ga ('b7c8d9e0f1a3')
to'g'irlandi — zanjir endi bitta, chiziqli va tekshiruvdan o'tgan.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'g1h2i3j4k5l6'
down_revision: Union[str, Sequence[str], None] = 'b7c8d9e0f1a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("theme_preference", sa.String(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("theme_preference")
