"""add admin_profile_settings table (clinic info + positions/departments JSON)

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-09-01 00:00:00.000000

Prompt 8: admin profili moduli. Bitta qatorli sozlamalar jadvali
(GovIntegrationSettings bilan bir xil naqsh) — klinika ma'lumotlari
oddiy ustunlarda, "ish o'rinlari" (positions) va "sohalar"
(departments) esa talab bo'yicha ("Barcha ma'lumotlar
admin_profile_settings jadvalida saqlansin") shu jadvalning JSON
ustunlarida ro'yxat sifatida saqlanadi — alohida jadval yaratilmaydi.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, Sequence[str], None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_profile_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("clinic_name", sa.String(), nullable=True),
        sa.Column("address", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("license_number", sa.String(), nullable=True),
        sa.Column("working_hours", sa.String(), nullable=True),
        sa.Column("positions", sa.JSON(), nullable=False),
        sa.Column("departments", sa.JSON(), nullable=False),
        sa.Column("next_position_id", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("next_department_id", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_admin_profile_settings_id"), "admin_profile_settings", ["id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_admin_profile_settings_id"), table_name="admin_profile_settings")
    op.drop_table("admin_profile_settings")
