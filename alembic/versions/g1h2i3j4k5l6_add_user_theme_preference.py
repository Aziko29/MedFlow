"""add theme_preference to users (light/dark/auto UI mode, Prompt 21)

Revision ID: g1h2i3j4k5l6
Revises: b7c8d9e0f1a2
Create Date: 2026-09-03 00:00:00.000000

⚠️ MUHIM ESLATMA (bu migratsiyaga aloqasi yo'q, mavjud muammo):
alembic/versions/ papkasida ikkita ALOHIDA fayl bir xil revision ID'ga
ega ('b7c8d9e0f1a2' — add_login_lockout_fields.py va
add_payment_refund_status_audit.py), bu esa `alembic history`/`upgrade
head`ni noaniq holatga keltiradi. Bu — oldingi promptlardan qolgan,
ushbu (Prompt 21, UI/UX) vazifasiga aloqasi yo'q nosozlik, shuning
uchun bu yerda TUZATILMAGAN (alohida chain-fix migratsiyasi/prompt
talab qilinadi). Quyidagi down_revision ikkala faylda ham bir xil ID
ishlatilgani uchun ATAYLAB shu (ko'proq keyingi, "payment_refund")
tarmoqqa ulangan.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'g1h2i3j4k5l6'
down_revision: Union[str, Sequence[str], None] = 'b7c8d9e0f1a2'
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
