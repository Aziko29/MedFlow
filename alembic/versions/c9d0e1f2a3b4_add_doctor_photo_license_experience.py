"""add doctor photo_path, license_number, experience_years, qualification_category

Revision ID: c9d0e1f2a3b4
Revises: a7b8c9d0e1f2
Create Date: 2026-08-30 00:00:00.000000

Shifokor moduliga to'rtta yangi ustun qo'shiladi — patients.photo_path/
blood_type kabi maydonlar bilan bir xil pattern (b2c3d4e5f6a7 migratsiyasiga
qara):

  - photo_path               — oddiy matn (shifrlanmagan). Fayl tizimidagi
                                static/uploads/doctors/{id}.jpg ga ishora
                                qiluvchi URL yo'l; o'zi maxfiy ma'lumot emas.
  - license_number            — oddiy matn. Tibbiy litsenziya raqami,
                                maxfiy emas — hisobot/tekshiruv maqsadida
                                ko'rsatiladi, shuning uchun shifrlanmaydi.
  - experience_years          — Integer. Ish tajribasi (to'liq yil).
  - qualification_category    — oddiy matn. Malaka toifasi (masalan
                                "Oliy toifa"). Cheklangan qiymatlar to'plami
                                frontendda (select) ta'minlanadi, DB darajasida
                                erkin matn — davlat toifalari o'zgarishi mumkin.

Mavjud shifokorlarda bu to'rtta maydon hozircha bo'sh, shuning uchun
backfill kerak emas — ustunlar to'g'ridan-to'g'ri NULL bilan qo'shiladi.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, Sequence[str], None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("doctors") as batch_op:
        batch_op.add_column(sa.Column("photo_path", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("license_number", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("experience_years", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("qualification_category", sa.String(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("doctors") as batch_op:
        batch_op.drop_column("qualification_category")
        batch_op.drop_column("experience_years")
        batch_op.drop_column("license_number")
        batch_op.drop_column("photo_path")
