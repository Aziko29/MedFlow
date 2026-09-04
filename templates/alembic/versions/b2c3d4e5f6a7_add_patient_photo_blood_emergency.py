"""add patient photo_path, blood_type, emergency contact fields

Revision ID: b2c3d4e5f6a7
Revises: f1a2b3c4d5e6
Create Date: 2026-08-30 00:00:00.000000

Bemor moduliga to'rtta yangi ustun qo'shiladi:

  - photo_path              — oddiy matn (shifrlanmagan). Fayl tizimidagi
                               static/uploads/patients/{id}.jpg ga ishora
                               qiluvchi URL yo'l; o'zi maxfiy ma'lumot emas.
  - blood_type               — oddiy matn, String(3) (masalan "A+", "O-").
                               Ataylab shifrlanmagan: favqulodda holatda
                               barcha rollarga (admin/reception/doctor/
                               cashier) tezkor ko'rinishi kerak.
  - emergency_contact_name    — EncryptedString(64, aad_context=
  - emergency_contact_phone     "patient.emergency_name"/"patient.emergency_phone")
                               — bular shaxsiy ma'lumot (PII), shuning uchun
                               patients.phone/address bilan bir xil pattern
                               (crypto_fields.py). DB ustuni sifatida
                               String(512) — EncryptedString(64)/EncryptedString(32)
                               ikkalasi ham impl uzunligini max(length*2, 512)
                               qilib belgilaydi (crypto_fields.EncryptedString),
                               a1b2c3d4e5f6 migratsiyasidagi patients.phone/
                               address ustunlari bilan bir xil hisob-kitob.

Mavjud bemorlarda bu to'rtta maydon hozircha bo'sh (hech qanday eski
ma'lumot yo'q), shuning uchun a1b2c3d4e5f6'dagi kabi backfill/qayta
shifrlash kerak emas — ustunlar to'g'ridan-to'g'ri NULL bilan qo'shiladi.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("patients") as batch_op:
        batch_op.add_column(sa.Column("photo_path", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("blood_type", sa.String(length=3), nullable=True))
        batch_op.add_column(
            sa.Column("emergency_contact_name", sa.String(length=512), nullable=True)
        )
        batch_op.add_column(
            sa.Column("emergency_contact_phone", sa.String(length=512), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("patients") as batch_op:
        batch_op.drop_column("emergency_contact_phone")
        batch_op.drop_column("emergency_contact_name")
        batch_op.drop_column("blood_type")
        batch_op.drop_column("photo_path")
