"""add allergies and chronic_conditions tables

Revision ID: f1a2b3c4d5e6
Revises: e5f6a7b8c9d0
Create Date: 2026-08-30 00:00:00.000000

Bemorlar moduliga ikkita yangi jadval: `allergies` (allergiyalar) va
`chronic_conditions` (surunkali kasalliklar). Ikkalasi ham patients.id'ga
FK bilan bog'langan va Patient o'chirilganda ORM darajasida cascade
qilinadi (models.Patient.allergies / chronic_conditions,
cascade="all, delete-orphan") — shu sababli bu yerda DB darajasida
ON DELETE CASCADE qo'shilmagan, chunki loyihaning boshqa jadvallarida
ham (appointments, payments, lab_results) xuddi shu tamoyilga amal
qilingan: o'chirish ORM orqali, bitta joyda (modules/patients.py:
delete_patient) boshqariladi.

`chronic_conditions.notes` — EncryptedText (AES-256-GCM,
aad_context="patient.chronic_notes"), patients.medical_notes bilan bir
xil patternga mos (crypto_fields.py). DB darajasida ustun oddiy
sa.String(length=8000) sifatida yaratiladi — bu a1b2c3d4e5f6
migratsiyasidagi patients.medical_notes uchun ishlatilgan aynan shu
uzunlik (EncryptedText.impl = String(8000), chunki shifrlangan qiymat
plaintext'dan ancha uzun bo'ladi: nonce+tag+base64 overhead).
Shifrlash/deshifrlash butunlay ORM qatlamida (models.ChronicCondition.notes
= Column(EncryptedText(...))) bo'ladi — migratsiya faqat DB ustunining
o'zini yaratadi, u yerda hech qanday key kerak emas.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "allergies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=False),
        sa.Column("substance", sa.String(), nullable=False),
        sa.Column("reaction", sa.String(), nullable=True),
        sa.Column("severity", sa.String(), nullable=True),
        sa.Column("noted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_allergies_id"), "allergies", ["id"], unique=False)
    op.create_index(
        op.f("ix_allergies_patient_id"), "allergies", ["patient_id"], unique=False
    )

    op.create_table(
        "chronic_conditions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("diagnosed_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="faol"),
        sa.Column("notes", sa.String(length=8000), nullable=True),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_chronic_conditions_id"), "chronic_conditions", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_chronic_conditions_patient_id"),
        "chronic_conditions",
        ["patient_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_chronic_conditions_patient_id"), table_name="chronic_conditions"
    )
    op.drop_index(op.f("ix_chronic_conditions_id"), table_name="chronic_conditions")
    op.drop_table("chronic_conditions")

    op.drop_index(op.f("ix_allergies_patient_id"), table_name="allergies")
    op.drop_index(op.f("ix_allergies_id"), table_name="allergies")
    op.drop_table("allergies")
