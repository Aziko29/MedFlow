"""
rotate_keys.py — Re-encrypt every encrypted column with the CURRENT key.

Bosqichlar:
  1. Yangi kalit yarating, CLINICFLOW_FIELD_KEY ga qo'ying (yangisi = current).
  2. Eski kalitni CLINICFLOW_FIELD_KEY_MAP ga qo'shing:
         export CLINICFLOW_FIELD_KEY_MAP='{"0": "<eski_base64_kalit>"}'
  3. Ushbu skriptni ishga tushiring — barcha qatorlar yangi kalit bilan
     qayta shifrlanadi (rewrap), key_id yangilanadi.
  4. Tasdiqlangач CLINICFLOW_FIELD_KEY_MAP ni olib tashlashingiz mumkin.

Ehtiyot chorasi: ishga tushirishdan oldin DB backup oling.
"""

import sys
from sqlalchemy import create_engine, text

from models_patch_example import Patient

# Ustun nomi -> TypeDecorator instance (aad_context to'g'ri kelishi kerak,
# chunki u ustun nomiga bog'langan AAD hisoblanadi).
ENCRYPTED_COLUMNS = {
    "phone": Patient.__table__.c.phone.type,
    "address": Patient.__table__.c.address.type,
    "medical_notes": Patient.__table__.c.medical_notes.type,
}


def rotate(db_url: str, batch_size: int = 500):
    """
    Har bir qatorni raw SQL orqali o'qib, TypeDecorator.rewrap() bilan
    joriy kalitga qayta shifrlaydi va yozib qo'yadi. ORM dirty-tracking'ga
    tayanmaydi (bir xil qiymatni qayta yozish "o'zgarish yo'q" deb hisoblanib,
    e'tibordan chetda qolishi mumkin edi).
    """
    engine = create_engine(db_url)
    table = Patient.__table__

    with engine.begin() as conn:
        total = conn.execute(text(f"SELECT COUNT(*) FROM {table.name}")).scalar()
        print(f"Jami {total} ta bemor yozuvi qayta shifrlanadi...")

        offset = 0
        rewrapped = 0
        while True:
            rows = conn.execute(
                text(f"SELECT id, phone, address, medical_notes FROM {table.name} "
                     f"ORDER BY id LIMIT :lim OFFSET :off"),
                {"lim": batch_size, "off": offset},
            ).fetchall()
            if not rows:
                break

            for row in rows:
                new_phone = ENCRYPTED_COLUMNS["phone"].rewrap(row.phone, engine.dialect)
                new_address = ENCRYPTED_COLUMNS["address"].rewrap(row.address, engine.dialect)
                new_notes = ENCRYPTED_COLUMNS["medical_notes"].rewrap(row.medical_notes, engine.dialect)
                conn.execute(
                    text(f"UPDATE {table.name} SET phone=:p, address=:a, medical_notes=:m WHERE id=:id"),
                    {"p": new_phone, "a": new_address, "m": new_notes, "id": row.id},
                )
                rewrapped += 1

            offset += batch_size
            print(f"  ... {min(offset, total)}/{total}")

    print(f"✅ Tugadi. {rewrapped} ta yozuv yangi kalit bilan qayta shifrlandi.")


if __name__ == "__main__":
    db_url = sys.argv[1] if len(sys.argv) > 1 else "sqlite:///medflow.db"
    rotate(db_url)
