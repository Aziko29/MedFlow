"""add field-level encryption (phone, address, medical_notes) + phone_bidx

Revision ID: a1b2c3d4e5f6
Revises: 5b117af8f671
Create Date: 2026-08-26 00:00:00.000000

MUHIM: bu migratsiya patients jadvalidagi mavjud PLAINTEXT qatorlarni
CLINICFLOW_FIELD_KEY bilan shifrlaydi va phone_bidx'ni to'ldiradi.
Ishga tushirishdan oldin: CLINICFLOW_FIELD_KEY va CLINICFLOW_BLIND_INDEX_KEY
environment o'zgaruvchilarini o'rnating, DB backup oling.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from crypto_fields import EncryptedString, blind_index

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '5b117af8f671'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_phone_type = EncryptedString(32, aad_context="patient.phone")
_addr_type = EncryptedString(255, aad_context="patient.address")
_notes_type = EncryptedString(4000, aad_context="patient.medical_notes")


def upgrade() -> None:
    bind = op.get_bind()

    # 1) yangi ustun (hozircha nullable, backfill'dan keyin unique index)
    with op.batch_alter_table("patients") as batch_op:
        batch_op.add_column(sa.Column("phone_bidx", sa.String(length=64), nullable=True))

    # 2) mavjud qatorlarni shifrlash + blind index to'ldirish (raw SQL,
    #    ORM dirty-tracking'ga tayanmasdan — rotate_keys.py bilan bir xil uslub)
    rows = bind.execute(sa.text("SELECT id, phone, address, medical_notes FROM patients")).fetchall()
    for row in rows:
        enc_phone = _phone_type.process_bind_param(row.phone, bind.dialect)
        enc_addr = _addr_type.process_bind_param(row.address, bind.dialect)
        enc_notes = _notes_type.process_bind_param(row.medical_notes, bind.dialect)
        bidx = blind_index(row.phone) if row.phone else None
        bind.execute(
            sa.text(
                "UPDATE patients SET phone=:p, address=:a, medical_notes=:m, "
                "phone_bidx=:b WHERE id=:id"
            ),
            {"p": enc_phone, "a": enc_addr, "m": enc_notes, "b": bidx, "id": row.id},
        )

    # 3) endi phone_bidx to'ldirilgan — unique qilamiz, eski `phone` unique'ni olib tashlaymiz
    with op.batch_alter_table("patients") as batch_op:
        batch_op.alter_column("phone_bidx", nullable=False)
        batch_op.create_unique_constraint("uq_patients_phone_bidx", ["phone_bidx"])
        batch_op.drop_index("ix_patients_phone")  # eski plaintext-davridagi unique index
        batch_op.alter_column(
            "phone", type_=sa.String(length=512), existing_type=sa.String()
        )
        batch_op.alter_column(
            "address", type_=sa.String(length=512), existing_type=sa.String()
        )
        batch_op.alter_column(
            "medical_notes", type_=sa.String(length=8000), existing_type=sa.String()
        )


def downgrade() -> None:
    # Shifrlashni bekor qilish tavsiya etilmaydi (ma'lumot yo'qotish xavfi).
    raise NotImplementedError(
        "Bu migratsiyani downgrade qilish qo'llab-quvvatlanmaydi — "
        "avval DB backup'dan tiklang."
    )
