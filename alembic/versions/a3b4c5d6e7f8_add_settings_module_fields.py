"""add settings module fields — user profile, clinic queue interval, system_settings (Prompt 12)

Revision ID: a3b4c5d6e7f8
Revises: e1f2a3b4c5d6
Create Date: 2026-09-01 00:00:00.000000

Prompt 12.1: Sozlamalar menyusi (backend).

1) `users` — first_name/last_name/phone/email qo'shiladi. Barchasi
   nullable va mavjud qatorlarda NULL bilan boshlanadi (fullname'ni
   avtomatik bo'lib first_name/last_name'ga taqsimlash ishonchsiz —
   xodim o'zi "Profil" bo'limidan to'ldiradi), shuning uchun backfill
   shart emas.

2) `admin_profile_settings` — queue_interval_minutes (Integer,
   default=15) qo'shiladi. Mavjud qator(lar) uchun server_default
   bilan to'ldiriladi.

3) Yangi `system_settings` jadvali — bitta-qator (singleton) sozlamalar,
   AdminProfileSettings/GovIntegrationSettings bilan bir xil naqsh.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users: profil maydonlari ────────────────────────────────────
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("first_name", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("last_name", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("phone", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("email", sa.String(), nullable=True))

    # ── admin_profile_settings: navbat oralig'i ──────────────────────
    with op.batch_alter_table("admin_profile_settings") as batch_op:
        batch_op.add_column(
            sa.Column(
                "queue_interval_minutes",
                sa.Integer(),
                nullable=False,
                server_default="15",
            )
        )

    # ── system_settings: yangi singleton jadval ──────────────────────
    op.create_table(
        "system_settings",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "timezone", sa.String(), nullable=False, server_default="Asia/Tashkent"
        ),
        sa.Column(
            "date_format", sa.String(), nullable=False, server_default="dd.MM.yyyy"
        ),
        sa.Column(
            "session_timeout_minutes",
            sa.Integer(),
            nullable=False,
            server_default="480",
        ),
        sa.Column(
            "max_login_attempts", sa.Integer(), nullable=False, server_default="5"
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("system_settings")

    with op.batch_alter_table("admin_profile_settings") as batch_op:
        batch_op.drop_column("queue_interval_minutes")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("email")
        batch_op.drop_column("phone")
        batch_op.drop_column("last_name")
        batch_op.drop_column("first_name")
