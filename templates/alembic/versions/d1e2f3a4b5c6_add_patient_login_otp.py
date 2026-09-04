"""add patient_login_otp table (FAZA 2 — bemor portali SMS-kod login)

Revision ID: d1e2f3a4b5c6
Revises: c9d0e1f2a3b4
Create Date: 2026-08-31 00:00:00.000000

Bemor portali (modules/patient_portal.py) uchun bir martalik SMS login
kodlarini saqlaydigan yangi jadval. Kod o'zi HECH QACHON saqlanmaydi —
faqat `code_hash` (sha256), a1b2c3d4e5f6 migratsiyasidagi "maxfiy narsa
ochiq saqlanmaydi" printsipiga mos (u yerda bu shifrlash, bu yerda esa
xeshlash — chunki kod qisqa muddatli va faqat tenglik tekshiriladi,
qayta o'qish shart emas).

Yangi bemor sessiyasi (mf_patient_session cookie, auth.py) xodimlar
sessiyasidan (cf_session) butunlay mustaqil — bu migratsiya faqat OTP
kodlari uchun, sessiya o'zi bazada saqlanmaydi (signed-token, boshqa
sessiyalar kabi).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, Sequence[str], None] = 'c9d0e1f2a3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "patient_login_otp",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "patient_id",
            sa.Integer(),
            sa.ForeignKey("patients.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("code_hash", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("patient_login_otp")
