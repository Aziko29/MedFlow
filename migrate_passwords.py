# migrate_passwords.py
"""
2-band (2.1): PBKDF2 → Argon2id parol migratsiyasi bo'yicha bir martalik
yordamchi skript.

MUHIM — NEGA BU SKRIPT XESHLARNI TO'G'RIDAN-TO'G'RI QAYTA YOZMAYDI:
Xeshlash — bir tomonlama (one-way) funksiya. Bazada faqat PBKDF2 xeshi
saqlangan, ochiq (plaintext) parol saqlanmagan — shuning uchun bazadan
o'qib, oflayn/ommaviy ravishda "PBKDF2 xeshini Argon2 xeshiga aylantirish"
texnik jihatdan IMKONSIZ (buni faqat parolni QAYTA TERGAN, ya'ni muvaffaqiyatli
login qilgan foydalanuvchi orqaligina qilish mumkin).

Shu sababli haqiqiy migratsiya avtomatik ravishda `modules/auth_module.py`
ichidagi login() funksiyasida sodir bo'ladi: foydalanuvchi eski (PBKDF2)
xesh bilan muvaffaqiyatli login qilgan zahoti, uning xeshi darhol Argon2'ga
qayta yoziladi (qarang: auth.needs_rehash(), auth.hash_password()).

Bu skriptning vazifasi — FAQAT KUZATUV/AUDIT:
  - Bazada hali nechta foydalanuvchi eski PBKDF2 formatida qolganini
    ko'rsatadi (ularning parollari hali Argon2'ga o'tmagan, chunki ular
    hali qayta login qilmagan).
  - Buni admin panelda yoki shu skriptni davriy ishga tushirib kuzatib
    borish mumkin — agar biror foydalanuvchi uzoq vaqt (masalan bir necha
    hafta) login qilmasa-yu hali PBKDF2'da qolsa, uni qo'lda ("parolni
    tiklash" orqali, band 6.4 — hali amalga oshirilmagan) yangilashga
    undash mumkin.

Ishga tushirish:
    python migrate_passwords.py
"""
from auth import _is_legacy_pbkdf2_hash  # ichki, lekin shu skript uchun maxsus ruxsat etilgan
from database import SessionLocal
import models


def report_legacy_password_hashes() -> None:
    db = SessionLocal()
    try:
        users = db.query(models.User).order_by(models.User.username).all()
        legacy_users = [u for u in users if _is_legacy_pbkdf2_hash(u.password_hash)]

        print("=" * 60)
        print("🔐 ARGON2 MIGRATSIYASI — HOLAT HISOBOTI")
        print("=" * 60)
        print(f"Jami foydalanuvchilar: {len(users)}")
        print(f"Argon2'ga o'tgan:      {len(users) - len(legacy_users)}")
        print(f"Hali PBKDF2'da (eski): {len(legacy_users)}")

        if legacy_users:
            print("\nHali qayta login qilmagan (PBKDF2'da qolgan) foydalanuvchilar:")
            for u in legacy_users:
                print(f"  - {u.username} ({u.fullname}, rol: {u.role})")
            print(
                "\nBu foydalanuvchilar keyingi muvaffaqiyatli login'ida "
                "AVTOMATIK Argon2'ga o'tkaziladi (qo'shimcha amal shart emas)."
            )
        else:
            print("\n✅ Barcha foydalanuvchilar Argon2id formatida.")
        print("=" * 60)
    finally:
        db.close()


if __name__ == "__main__":
    report_legacy_password_hashes()
