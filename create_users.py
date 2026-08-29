# create_users.py
"""
create_admin.py va create_user.py ning BIRLASHTIRILGAN o'rniga keldi —
endi login hisoblarini yaratish/yangilash uchun bitta skript kifoya.

MANTIQ:
  - Bazada HECH QANDAY admin yo'q bo'lsa (masalan yangi o'rnatish) —
    skript avtomatik "Birinchi admin yaratish" rejimida ishga tushadi:
    faqat login, F.I.O va parolni so'raydi, rol="admin" bilan yozadi.
  - Kamida bitta admin allaqachon mavjud bo'lsa — skript rolni tanlashni
    so'raydi (admin/reception/cashier/doctor). Rol="doctor" tanlansa,
    mavjud Doctor yozuvlaridan biriga bog'lash so'raladi.
  - Login allaqachon mavjud bo'lsa — parol/rol/bog'lanishni yangilash
    taklif qilinadi (o'chirib qayta yaratish shart emas).

YANGI ROL QO'SHISH KERAK BO'LSA: bu qiymatlar bitta joyda — models.py
dagi USER_ROLES tuple'ida — saqlanadi (schemas.py ham shu yerdan import
qiladi). Yangi rol qo'shish uchun: (1) models.USER_ROLES ga qo'shing,
(2) require_role() chaqiruvlarini (auth_module.py, modules/*.py)
kerak bo'lsa yangilang. Bu skript ROLES ro'yxatini to'g'ridan-to'g'ri
models.USER_ROLES'dan oladi — shuning uchun bu yerda alohida
o'zgartirish shart emas.

XAVFSIZLIK PRINSIPI (create_admin.py/create_user.py bilan bir xil):
  - Parol bu faylning HECH QAYERIDA yozilmagan. U faqat ishga
    tushirilganda, terminalda getpass() orqali so'raladi (ekranda
    ko'rinmaydi, terminal tarixida ham qolmaydi).
  - Parol hech qanday faylga, logga yoki konsolga qaytadan chop
    etilmaydi — darhol Argon2id bilan xeshlanadi (bir tomonlama) va
    faqat xesh bazaga yoziladi; xotiradagi ochiq matn `del password`
    bilan darhol tozalanadi.

ISHLATISH:
    python create_users.py
"""
import getpass
import re
import sys

from database import Base, SessionLocal, engine
from auth import hash_password
import models
from models import USER_ROLES

MIN_LENGTH = 14


def _password_is_strong(pw: str) -> str | None:
    """Kuchsiz parolni rad etadi. Muammo bo'lsa xabar qaytaradi, aks holda None."""
    if len(pw) < MIN_LENGTH:
        return f"Parol kamida {MIN_LENGTH} ta belgidan iborat bo'lishi kerak."
    checks = [
        (r"[a-z]", "kichik harf"),
        (r"[A-Z]", "katta harf"),
        (r"[0-9]", "raqam"),
        (r"[^a-zA-Z0-9]", "maxsus belgi (masalan ! @ # $ %)"),
    ]
    missing = [label for pattern, label in checks if not re.search(pattern, pw)]
    if missing:
        return "Parolda quyidagilar yetishmayapti: " + ", ".join(missing)
    common_weak = {"password", "admin123", "12345678", "qwerty", "parol123"}
    if pw.lower() in common_weak:
        return "Bu parol juda oddiy/taxmin qilinadigan — boshqasini tanlang."
    return None


def _prompt_password(label: str = "Parol") -> str:
    while True:
        pw1 = getpass.getpass(f"{label} (ekranda ko'rinmaydi): ")
        problem = _password_is_strong(pw1)
        if problem:
            print(f"❌ {problem}\n")
            continue
        pw2 = getpass.getpass("Parolni tasdiqlang: ")
        if pw1 != pw2:
            print("❌ Parollar mos kelmadi, qaytadan urinib ko'ring.\n")
            continue
        return pw1


def _prompt_role() -> str:
    print("Rollar: " + ", ".join(USER_ROLES))
    while True:
        role = input("Rol tanlang: ").strip().lower()
        if role in USER_ROLES:
            return role
        print("❌ Noto'g'ri rol, ro'yxatdan birini yozing.\n")


def _prompt_doctor_link(db) -> int:
    doctors = db.query(models.Doctor).order_by(models.Doctor.id).all()
    if not doctors:
        print("❌ Bazada hech qanday Doctor yozuvi yo'q. Avval admin paneldan")
        print("   'Shifokor qo'shish' orqali uning profilini (ism, mutaxassislik,")
        print("   narx) yarating, keyin shu skriptni qayta ishga tushiring.")
        sys.exit(1)
    print("\nMavjud shifokorlar:")
    for d in doctors:
        print(f"  #{d.id} — {d.fullname} ({d.specialty})")
    while True:
        raw = input("Ushbu login qaysi shifokorga tegishli? (ID kiriting): ").strip()
        if raw.isdigit() and any(d.id == int(raw) for d in doctors):
            return int(raw)
        print("❌ Noto'g'ri ID, ro'yxatdagi raqamlardan birini kiriting.\n")


def _create_first_admin(db) -> None:
    print("ℹ️  Bazada hech qanday admin topilmadi — 'Birinchi admin yaratish' rejimi.\n")
    username = input("Admin uchun login (username): ").strip()
    if not username:
        print("❌ Login bo'sh bo'lishi mumkin emas.")
        sys.exit(1)

    existing = db.query(models.User).filter(models.User.username == username).first()
    if existing:
        # Nazariy jihatdan bo'lmasligi kerak (admin yo'q degan holatdamiz),
        # lekin boshqa rolga ega shu login mavjud bo'lishi mumkin.
        print(f"ℹ️  '{username}' allaqachon mavjud (rol: {existing.role}).")
        confirm = input("   Uni admin qilib, parolini yangilaymi? (ha/yo'q): ").strip().lower()
        if confirm not in ("ha", "h", "yes", "y"):
            print("Bekor qilindi, hech narsa o'zgartirilmadi.")
            return
        fullname = existing.fullname
    else:
        fullname = input("To'liq ism (masalan 'Azamat Administrator'): ").strip() or username

    password = _prompt_password("Yangi admin paroli")
    password_hash = hash_password(password)
    del password

    if existing:
        existing.password_hash = password_hash
        existing.role = "admin"
        existing.fullname = fullname
        existing.doctor_id = None
    else:
        db.add(models.User(
            username=username,
            password_hash=password_hash,
            fullname=fullname,
            role="admin",
        ))

    db.commit()
    print(f"\n✅ '{username}' admin sifatida tayyor. Parolni endi FAQAT o'zingiz bilasiz.")
    print("   Tavsiya: terminal tarixini tozalang, skrinshot/screen-record qilmang.")


def _create_or_update_user(db) -> None:
    username = input("Login (username): ").strip()
    if not username:
        print("❌ Login bo'sh bo'lishi mumkin emas.")
        sys.exit(1)

    existing = db.query(models.User).filter(models.User.username == username).first()
    if existing:
        print(f"ℹ️  '{username}' allaqachon mavjud (rol: {existing.role}).")
        confirm = input("   Parol/rolini yangilaymi? (ha/yo'q): ").strip().lower()
        if confirm not in ("ha", "h", "yes", "y"):
            print("Bekor qilindi.")
            return
        fullname = existing.fullname
    else:
        fullname = input("To'liq ism: ").strip() or username

    role = _prompt_role()
    doctor_id = _prompt_doctor_link(db) if role == "doctor" else None

    # Oxirgi adminni tasodifan boshqa rolga o'tkazib qo'yishning oldini
    # olish — admin panelidagi PUT /api/auth/users/{id} bilan bir xil
    # himoya, CLI orqali ham chetlab o'tib bo'lmasin.
    if existing and existing.role == "admin" and role != "admin":
        remaining_admins = (
            db.query(models.User)
            .filter(models.User.role == "admin", models.User.id != existing.id)
            .count()
        )
        if remaining_admins == 0:
            print("❌ Bu — bazadagi OXIRGI admin. Uning rolini boshqasiga o'zgartirib bo'lmaydi.")
            print("   Avval boshqa birontasini admin qiling, keyin qayta urinib ko'ring.")
            sys.exit(1)

    password = _prompt_password()
    password_hash = hash_password(password)
    del password

    if existing:
        existing.password_hash = password_hash
        existing.role = role
        existing.fullname = fullname
        existing.doctor_id = doctor_id
    else:
        db.add(models.User(
            username=username,
            password_hash=password_hash,
            fullname=fullname,
            role=role,
            doctor_id=doctor_id,
        ))

    db.commit()
    print(f"\n✅ '{username}' — rol: {role}"
          + (f", bog'langan Doctor #{doctor_id}" if doctor_id else "")
          + " — tayyor.")


def main() -> None:
    # seed.py ishlatilmasa ham jadvallar mavjud bo'lishi uchun (agar
    # alembic ishlatilmagan bo'lsa) — xavfsiz, mavjud jadvallarga tegmaydi.
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        has_admin = db.query(models.User).filter(models.User.role == "admin").first() is not None
        if not has_admin:
            _create_first_admin(db)
        else:
            _create_or_update_user(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
