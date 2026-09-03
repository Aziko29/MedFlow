"""add patient_admissions table (admission/discharge history)

Revision ID: a2b3c4d5e6f7
Revises: b4c5d6e7f8a9
Create Date: 2026-09-02 00:00:00.000000

PROMPT 9: bag — bemorni qayta yotqizishda avvalgi `discharged_at` sanasi
o'chib ketardi (tarix yo'qolardi), chunki Prompt 7'da qo'shilgan
patients.room_number/admitted_at/discharged_at/is_admitted — bularning
barchasi oddiy ustunlar bo'lib, faqat "joriy" yotqizilish holatini
bildiradi; har safar POST /{id}/admit chaqirilganda o'sha ustunlar
ustiga yozib yuborilardi.

Bu migratsiya yangi `patient_admissions` jadvalini qo'shadi — har bir
yotqizilish/chiqarilish HODISASI uchun alohida qator (to'liq tarix).
`patients` jadvalidagi joriy-holat ustunlari O'ZGARTIRILMAYDI/
O'CHIRILMAYDI (ular hamon "hozir kim palatada" tezkor so'rovi uchun
ishlatiladi) — bu faqat ularni to'ldiruvchi qo'shimcha jadval.
treatment_history (a7b8c9d0e1f2) bilan bir xil pattern: patient_id'ga
oddiy FK (ON DELETE CASCADE'siz — o'chirish ORM darajasida,
Patient.admissions cascade="all, delete-orphan" orqali boshqariladi).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a2b3c4d5e6f7'
down_revision: Union[str, Sequence[str], None] = 'b4c5d6e7f8a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "patient_admissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=False),
        sa.Column("room_number", sa.String(length=20), nullable=True),
        sa.Column("admitted_at", sa.DateTime(), nullable=False),
        sa.Column("discharged_at", sa.DateTime(), nullable=True),
        sa.Column("discharged_reason", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_patient_admissions_id"), "patient_admissions", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_patient_admissions_patient_id"),
        "patient_admissions",
        ["patient_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_patient_admissions_patient_id"), table_name="patient_admissions"
    )
    op.drop_index(op.f("ix_patient_admissions_id"), table_name="patient_admissions")
    op.drop_table("patient_admissions")
