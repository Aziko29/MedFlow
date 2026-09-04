"""extend gov_integration_settings + add patient PINFL/passport fields (Prompt 10)

Revision ID: e1f2a3b4c5d6
Revises: d5e6f7a8b9c0
Create Date: 2026-09-01 00:00:00.000000

Prompt 10: OneID / DAVLAT RO'YXATI integratsiyasi.

1) `gov_integration_settings` — FAZA 3 SKELETON'dagi bo'sh
   provider_name/config_json ustunlari olib tashlanadi (hech qayerda
   ishlatilmagan edi, faqat joy egallab turgan edi) va o'rniga haqiqiy
   integratsiya uchun kerakli ustunlar qo'shiladi: integration_name,
   api_url, api_key (shifrlangan), api_secret (shifrlangan),
   organization_id, created_at. Mavjud qatorlar (agar bo'lsa) — bu
   jadval hozirgacha hech qanday real qiymat bilan to'ldirilmagan edi
   (is_enabled har doim False, provider_name/config_json har doim NULL),
   shuning uchun ma'lumot yo'qotish xavfi yo'q.

2) `patients` — pinfl/pinfl_bidx/passport_series/passport_number/
   is_verified ustunlari qo'shiladi. Bularning barchasi yangi va
   NULL/False bilan boshlanadi (mavjud bemorlar davlat tizimida hali
   tekshirilmagan), shuning uchun backfill kerak emas — a1b2c3d4e5f6
   (phone_bidx) migratsiyasidan farqli o'laroq, bu yerda ustunlar
   YANGI bo'sh maydonlar, mavjud plaintext ma'lumotni shifrlashga
   ehtiyoj yo'q.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'd5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── gov_integration_settings ────────────────────────────────────
    with op.batch_alter_table("gov_integration_settings") as batch_op:
        batch_op.add_column(sa.Column("integration_name", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("api_url", sa.String(length=500), nullable=True))
        # EncryptedString ustunlari DB darajasida oddiy String — shifrlash
        # butunlay Python/TypeDecorator darajasida (crypto_fields.py),
        # shuning uchun ustun turi shunchaki yetarlicha uzun String.
        batch_op.add_column(sa.Column("api_key", sa.String(length=1024), nullable=True))
        batch_op.add_column(sa.Column("api_secret", sa.String(length=1024), nullable=True))
        batch_op.add_column(sa.Column("organization_id", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("created_at", sa.DateTime(), nullable=True))
        batch_op.drop_column("provider_name")
        batch_op.drop_column("config_json")

    # ── patients: PINFL / pasport / tekshirilganlik ─────────────────
    # MUHIM: pinfl/passport_series/passport_number EncryptedString
    # orqali saqlanadi (models.py) — bazadagi qiymat plaintext emas,
    # balki base64(key_id + nonce + ciphertext + GCM tag), shuning
    # uchun ustun kengligi PLAINTEXT uzunligidan emas, balki
    # crypto_fields.EncryptedString'ning minimal kengligidan (512,
    # qarang crypto_fields.py: max(length*2, 512)) kelib chiqadi —
    # xuddi a1b2c3d4e5f6 migratsiyasida `phone` ustuni 512ga
    # kengaytirilgani kabi. Kichikroq ustun (masalan 64) SQLite'da
    # jim ishlagandek ko'rinsa ham, PostgreSQL'da shifrlangan qiymat
    # kesilib/xato berardi.
    with op.batch_alter_table("patients") as batch_op:
        batch_op.add_column(sa.Column("pinfl", sa.String(length=512), nullable=True))
        batch_op.add_column(sa.Column("pinfl_bidx", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("passport_series", sa.String(length=512), nullable=True))
        batch_op.add_column(sa.Column("passport_number", sa.String(length=512), nullable=True))
        batch_op.add_column(
            sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.create_unique_constraint("uq_patients_pinfl_bidx", ["pinfl_bidx"])


def downgrade() -> None:
    with op.batch_alter_table("patients") as batch_op:
        batch_op.drop_constraint("uq_patients_pinfl_bidx", type_="unique")
        batch_op.drop_column("is_verified")
        batch_op.drop_column("passport_number")
        batch_op.drop_column("passport_series")
        batch_op.drop_column("pinfl_bidx")
        batch_op.drop_column("pinfl")

    with op.batch_alter_table("gov_integration_settings") as batch_op:
        batch_op.add_column(sa.Column("provider_name", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("config_json", sa.Text(), nullable=True))
        batch_op.drop_column("created_at")
        batch_op.drop_column("organization_id")
        batch_op.drop_column("api_secret")
        batch_op.drop_column("api_key")
        batch_op.drop_column("api_url")
        batch_op.drop_column("integration_name")
