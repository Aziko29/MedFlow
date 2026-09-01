# scripts/seed_users.py
"""
🌱 Test/demo foydalanuvchilarini avtomatik yaratuvchi skript (Prompt 15.3).

MAQSAD: staging/demo muhitida (yoki development'da tezkor tekshiruv uchun)
har bir rol uchun bitta faol login bo'lishini kafolatlash — QO'LDA
`python create_users.py` orqali 6 marta o'tirib parol kiritish shart
bo'lmasin.

⚠️ XAVFSIZLIK — MUHIM CHEKLOV:
Bu skript ATAYLAB faqat quyidagi hollarda ishlashga ruxsat beradi:
  - ENV=production BO'LMASA (development/staging/bo'sh), YOKI
  - --force bayrog'i aniq ko'rsatilgan bo'lsa.
Production muhitida standart, hammaga ma'lum parollar bilan hisob
yaratish — eng keng tarqalgan xavfsizlik xatolaridan biri (OWASP
"Default Credentials"). Shu sabab: `ENV=production` bo'lsa skript
darhol xatolik bilan to'xtaydi, `--force` bilan ham DAVOM ETADI, lekin
qizil ogohlantirish chop etadi. Production uchun haqiqiy xodim
hisoblarini FAQAT `python create_users.py` (interaktiv, getpass bilan,
parolni hech qayerga yozmaydigan) orqali yarating.

NIMA QILADI:
  - Har bir rol (models.USER_ROLES) uchun bitta belgilangan test login
    yaratadi (agar mavjud bo'lmasa) yoki mavjud bo'lsa — o'zgartirmaydi
    (idempotent: qayta-qayta ishga tushirish xavfsiz, mavjud parollarni
    tasodifan qayta yozib yubormaydi).
  - `doctor` va `lab_doctor` rollari `Doctor` yozuviga bog'lanishi shart
    (auth.py / modules/auth_module.py shuni talab qiladi) — agar mos
    faol Doctor topilmasa, skript o'zi bitta test Doctor yozuvi yaratadi.
  - Parollarni loyihaning YAGONA xesh sxemasi — Argon2id (`auth.hash_password`)
    bilan xeshlaydi.

PAROL XESHLASH HAQIDA ESLATMA (talabnomadan chetlashish):
Original topshiriqda "passlib (bcrypt)" so'ralgan edi, lekin bu loyiha
allaqachon PBKDF2'dan Argon2id'ga o'tgan (qarang: auth.py, migrate_passwords.py)
va boshqa hech qanday joyda bcrypt/passlib ishlatilmaydi (requirements.txt'da
ham yo'q). Bitta bazada ikkita turli xeshlash sxemasini aralashtirish
(ba'zi userlar bcrypt, ba'zilari Argon2id) keraksiz murakkablik va
xato manbai bo'lar edi, shuning uchun bu skript ham `auth.hash_password()`
(Argon2id) dan foydalanadi — xuddi create_users.py va ro'yxatdan o'tishning
boshqa barcha joylari kabi, YAGONA manba.

ISHLATISH:
    python scripts/seed_users.py                  # development/staging
    python scripts/seed_users.py --password "..."  # barcha test userlar uchun bitta parol
    python scripts/seed_users.py --force            # ENV=production bo'lsa ham (tavsiya etilmaydi)
    python scripts/seed_users.py --reset-password    # mavjud test userlarning parolini ham qayta o'rnatadi
"""
from __future__ import annotations

import argparse
import os
import sys

# Loyiha ildizini import yo'liga qo'shamiz — skript scripts/ ichida turgani
# uchun `import models`/`import auth` to'g'ridan-to'g'ri ishlamaydi.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

import models
from auth import hash_password
from database import Base, SessionLocal, engine

# ─────────────────────────── Test foydalanuvchilar ro'yxati ───────────────
# Har biri: (username, fullname, role). Parol --password bilan yagona
# qilib berilishi mumkin, aks holda standart DEFAULT_PASSWORD ishlatiladi.
SEED_USERS: list[dict] = [
    {"username": "admin@medflow.uz", "fullname": "Test Admin", "role": "admin"},
    {"username": "assistant@medflow.uz", "fullname": "Test Yordamchi Admin", "role": "assistant_admin"},
    {"username": "doctor@medflow.uz", "fullname": "Test Shifokor", "role": "doctor"},
    {"username": "lab@medflow.uz", "fullname": "Test Laborant", "role": "lab_doctor"},
    {"username": "cashier@medflow.uz", "fullname": "Test Kassir", "role": "cashier"},
    {"username": "reception@medflow.uz", "fullname": "Test Qabulxona", "role": "reception"},
]

# create_users.py'dagi kuchli-parol talabiga mos (14+ belgi, katta/kichik
# harf, raqam, maxsus belgi) — shu bilan seed hisoblari ham production
# parol siyosatiga zid bo'lib qolmaydi.
DEFAULT_PASSWORD = "MedFlow#Seed2026!"

# doctor/lab_doctor test hisoblari bog'lanadigan test Doctor yozuvi —
# mavjud faol Doctor bo'lmasa, shu qiymatlar bilan avtomatik yaratiladi.
SEED_DOCTOR = {
    "fullname": "Test Shifokor (Seed)",
    "specialty": "Terapevt",
    "consultation_price": 50_000,
    "working_hours": "09:00 - 18:00",
    "is_active": True,
}


def _ensure_seed_doctor(db) -> int:
    """doctor/lab_doctor test hisoblari uchun Doctor.id qaytaradi —
    mavjud faol shifokorlardan birini ishlatadi, bo'lmasa yangisini
    yaratadi. Hech qachon takror-takror yangi Doctor yaratmaydi (mavjud
    'Test Shifokor (Seed)' bo'lsa o'shani qaytaradi)."""
    existing = (
        db.query(models.Doctor)
        .filter(models.Doctor.fullname == SEED_DOCTOR["fullname"])
        .first()
    )
    if existing:
        return existing.id

    doctor = models.Doctor(**SEED_DOCTOR)
    db.add(doctor)
    db.commit()
    db.refresh(doctor)
    print(f"  ➕ Test Doctor yaratildi: #{doctor.id} — {doctor.fullname}")
    return doctor.id


def seed_users(password: str, reset_password: bool) -> None:
    db = SessionLocal()
    doctor_id_cache: int | None = None
    try:
        for spec in SEED_USERS:
            username = spec["username"]
            role = spec["role"]
            existing = db.query(models.User).filter(models.User.username == username).first()

            doctor_id = None
            if role in ("doctor", "lab_doctor"):
                if doctor_id_cache is None:
                    doctor_id_cache = _ensure_seed_doctor(db)
                doctor_id = doctor_id_cache

            if existing:
                if reset_password:
                    existing.password_hash = hash_password(password)
                    existing.role = role
                    existing.fullname = spec["fullname"]
                    existing.doctor_id = doctor_id
                    db.commit()
                    print(f"  🔁 Yangilandi (parol qayta o'rnatildi): {username} ({role})")
                else:
                    print(f"  ⏭️  O'tkazib yuborildi (allaqachon mavjud): {username} ({role})")
                continue

            db.add(models.User(
                username=username,
                password_hash=hash_password(password),
                fullname=spec["fullname"],
                role=role,
                doctor_id=doctor_id,
            ))
            db.commit()
            print(f"  ✅ Yaratildi: {username} ({role})")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MedFlow uchun test/demo foydalanuvchilarni (har bir rol uchun 1 tadan) yaratish."
    )
    parser.add_argument(
        "--password", default=DEFAULT_PASSWORD,
        help="Barcha test userlar uchun bitta parol (standart: xavfsiz o'rnatilgan qiymat).",
    )
    parser.add_argument(
        "--reset-password", action="store_true",
        help="Test user allaqachon mavjud bo'lsa ham, uning parolini/rolini qayta o'rnatadi.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="ENV=production bo'lsa ham davom etish (TAVSIYA ETILMAYDI).",
    )
    args = parser.parse_args()

    env = os.environ.get("ENV", "development").strip().lower()
    if env == "production" and not args.force:
        print(
            "❌ ENV=production aniqlandi. Bu skript standart/ma'lum parollar bilan\n"
            "   test hisoblar yaratadi — production muhitida bu xavfsizlik xatosi\n"
            "   hisoblanadi. Haqiqiy xodim hisoblarini `python create_users.py`\n"
            "   (interaktiv, individual parollar bilan) orqali yarating.\n"
            "   Agar bu ataylab qilinayotgan bo'lsa (masalan vaqtinchalik staging\n"
            "   server production konfiguratsiyasida), --force bilan qayta ishga\n"
            "   tushiring."
        )
        sys.exit(1)

    if env == "production" and args.force:
        print("🔴 OGOHLANTIRISH: ENV=production'da --force bilan ishga tushirilmoqda!")
        print("   Bu standart parollar bilan hisoblar yaratadi/yangilaydi. Bu\n"
              "   test/demo maqsadidan tashqari hech qachon qoldirilmasligi kerak —\n"
              "   ishni tugatgach shu hisoblarni o'chiring yoki parolini almashtiring.\n")

    # alembic ishlatilmagan sof-yangi muhitda ham jadvallar mavjud bo'lishi
    # uchun (mavjud jadvallarga tegmaydi, create_users.py bilan bir xil naqsh).
    Base.metadata.create_all(bind=engine)

    print(f"\n🌱 Test foydalanuvchilarni yaratish (ENV={env})...\n")
    seed_users(password=args.password, reset_password=args.reset_password)
    print(
        f"\n✅ Tayyor. Standart parol (agar --password berilmagan bo'lsa): "
        f"{DEFAULT_PASSWORD!r}\n"
        "   ⚠️ Bu parol shu faylda ochiq matnda yozilgan — FAQAT development/\n"
        "   demo/staging muhitida ishlating, hech qachon production hisoblari\n"
        "   uchun ishlatmang."
    )


if __name__ == "__main__":
    main()
