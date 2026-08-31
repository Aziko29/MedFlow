"""add gov_integration_settings table (FAZA 3 — davlat integratsiyasiga tayyorgarlik, SKELETON)

Revision ID: f2a3b4c5d6e7
Revises: d1e2f3a4b5c6
Create Date: 2026-08-31 00:00:00.000000

Bu migratsiya faqat bo'sh, mustaqil sozlamalar jadvalini qo'shadi —
boshqa hech qanday jadvalga tegilmaydi. `is_enabled` ustuni `False`
default bilan yaratiladi va bu FAZAda hech qachon boshqacha
o'rnatilmaydi. Haqiqiy davlat platformasi (masalan OneID) bilan
tashqi API almashinuvi bu migratsiyada YOZILMAGAN — jadval faqat
kelajakdagi integratsiya uchun joy tayyorlaydi.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, Sequence[str], None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gov_integration_settings",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("provider_name", sa.String(length=64), nullable=True),
        sa.Column("config_json", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("gov_integration_settings")
