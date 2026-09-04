"""add last_test_status/last_tested_at to gov_integration_settings (Prompt 22: provider select + connection test)

Revision ID: h7i8j9k0l1m2
Revises: g1h2i3j4k5l6
Create Date: 2026-09-04 00:00:00.000000

Bemor "Davlat integratsiyasi provayder nomi" endi erkin matn emas,
oldindan belgilangan ro'yxatdan (`schemas.GOV_INTEGRATION_PROVIDERS`)
tanlanadi (yoki "Boshqa" orqali qo'lda kiritiladi) — bu yozilish
farqlaridan ("OneID"/"Oneid"/"one id") kelib chiqadigan
nomuvofiqlikning oldini oladi.

Bu bilan bir qatorda yangi xususiyat: "Ulanishni tekshirish" tugmasi
(admin/profile) — natija shu ikki ustunda saqlanadi.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'h7i8j9k0l1m2'
down_revision: Union[str, Sequence[str], None] = 'g1h2i3j4k5l6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("gov_integration_settings") as batch_op:
        batch_op.add_column(
            sa.Column("last_test_status", sa.String(length=20), nullable=True)
        )
        batch_op.add_column(
            sa.Column("last_tested_at", sa.DateTime(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("gov_integration_settings") as batch_op:
        batch_op.drop_column("last_tested_at")
        batch_op.drop_column("last_test_status")
