"""add treatment_history table

Revision ID: a7b8c9d0e1f2
Revises: b2c3d4e5f6a7
Create Date: 2026-08-30 00:00:00.000000

Bemorlar moduliga yangi jadval: `treatment_history` (davolanishlar
tarixi, Prompt 2). `patients.id`ga FK bilan bog'langan va Patient
o'chirilganda ORM darajasida cascade qilinadi
(models.Patient.treatment_history, cascade="all, delete-orphan") —
allergies/chronic_conditions bilan bir xil tamoyil: o'chirish ORM orqali,
bitta joyda (modules/patients.py: delete_patient) boshqariladi, shu
sababli bu yerda DB darajasida ON DELETE CASCADE qo'shilmagan.

`appointment_id` (appointments.id) va `doctor_id` (doctors.id) ikkalasi
ham nullable — yozuv aniq bir tashrifga yoki shifokorga bog'lanmasdan
ham kiritilishi mumkin (LabResult.doctor_id bilan bir xil pattern).

`diagnosis` va `treatment` — EncryptedText (AES-256-GCM,
aad_context="treatment.diagnosis" / "treatment.plan"),
chronic_conditions.notes bilan bir xil patternga mos (crypto_fields.py).
DB darajasida ustunlar oddiy sa.String(length=8000) sifatida yaratiladi —
bu boshqa EncryptedText ustunlarida ishlatilgan aynan shu uzunlik
(EncryptedText.impl = String(8000), chunki shifrlangan qiymat
plaintext'dan ancha uzun bo'ladi: nonce+tag+base64 overhead).
Shifrlash/deshifrlash butunlay ORM qatlamida bo'ladi — migratsiya faqat
DB ustunlarining o'zini yaratadi, u yerda hech qanday kalit kerak emas.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "treatment_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=False),
        sa.Column("appointment_id", sa.Integer(), nullable=True),
        sa.Column("doctor_id", sa.Integer(), nullable=True),
        sa.Column("diagnosis", sa.String(length=8000), nullable=True),
        sa.Column("treatment", sa.String(length=8000), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
        sa.ForeignKeyConstraint(["appointment_id"], ["appointments.id"]),
        sa.ForeignKeyConstraint(["doctor_id"], ["doctors.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_treatment_history_id"), "treatment_history", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_treatment_history_patient_id"),
        "treatment_history",
        ["patient_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_treatment_history_appointment_id"),
        "treatment_history",
        ["appointment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_treatment_history_doctor_id"),
        "treatment_history",
        ["doctor_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_treatment_history_doctor_id"), table_name="treatment_history"
    )
    op.drop_index(
        op.f("ix_treatment_history_appointment_id"), table_name="treatment_history"
    )
    op.drop_index(
        op.f("ix_treatment_history_patient_id"), table_name="treatment_history"
    )
    op.drop_index(op.f("ix_treatment_history_id"), table_name="treatment_history")
    op.drop_table("treatment_history")
