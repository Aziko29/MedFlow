# create_users.py
"""
Login hisoblarini boshqarish uchun YAGONA skript — create_admin.py va
create_user.py (eski, alohida fayllar) shu yerga BIRLASHTIRILGAN va
ustiga foydalanuvchilar ro'yxati / o'chirish menyusi bilan kengaytirilgan.

MANTIQ:
  - Bazada HECH QANDAY admin yo'q bo'lsa (masalan yangi o'rnatish) —
    skript avtomatik "Birinchi admin yaratish" rejimida ishga tushadi:
    faqat login, F.I.O va parolni so'raydi, rol="admin" bilan yozadi.
  - Kamida bitta admin allaqachon mavjud bo'lsa — asosiy menyu chiqadi:
      [1] Yangi foydalanuvchi qo'shish / mavjudini yangilash
      [2] Foydalanuvchilar ro'yxatini ko'rish
      [3] Foydalanuvchini o'chirish
      [4] Chiqish
  - "Qo'shish/yangilash"da rol tanlanadi (admin/reception/cashier/doctor).
    Rol="doctor" tanlansa, mavjud Doctor yozuvlaridan biriga bog'lash
    so'raladi — aks holda u "o'z navbatlarini" ko'ra olmaydi.
  - Login allaqachon mavjud bo'lsa — parol/rol/bog'lanishni yangilash
    taklif qilinadi (o'chirib qayta yaratish shart emas).
  - "O'chirish"da — bazadagi OXIRGI admin hech qachon o'chirilmaydi
    (tizim kirish huquqisiz qolib ketmasligi uchun).

YANGI ROL QO'SHISH KERAK BO'LSA: bu qiymatlar bitta joyda — models.py
dagi USER_ROLES tuple'ida — saqlanadi (schemas.py ham shu yerdan import
qiladi). Yangi rol qo'shish uchun: (1) models.USER_ROLES ga qo'shing,
(2) require_role() chaqiruvlarini (auth_module.py, modules/*.py)
kerak bo'lsa yangilang. Bu skript ROLES ro'yxatini to'g'ridan-to'g'ri
models.USER_ROLES'dan oladi — shuning uchun bu yerda alohida
o'zgartirish shart emas.

XAVFSIZLIK PRINSIPI:
  - Parol bu faylning HECH QAYERIDA yozilmagan. U faqat ishga
    tushirilganda, terminalda getpass() orqali so'raladi (ekranda
    ko'rinmaydi, terminal tarixida ham qolmaydi).
  - Parol hech qanday faylga, logga yoki konsolga qaytadan chop
    etilmaydi — darhol Argon2id bilan xeshlanadi (bir tomonlama) va
    faqat xesh bazaga yoziladi; xotiradagi ochiq matn `del password`
    bilan darhol tozalanadi.
  - O'chirish amali qaytarib bo'lmaydigan (irreversible) — ikki marta
    tasdiqlash so'raladi va oxirgi admin hech qachon o'chirilmaydi.

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


# ─────────────────────────── Yordamchi validatorlar ───────────────────────

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


def _confirm(prompt: str) -> bool:
    return input(prompt).strip().lower() in ("ha", "h", "yes", "y")


def _admin_count(db, exclude_user_id: int | None = None) -> int:
    query = db.query(models.User).filter(models.User.role == "admin")
    if exclude_user_id is not None:
        query = query.filter(models.User.id != exclude_user_id)
    return query.count()


# ─────────────────────────── Birinchi admin (bootstrap) ───────────────────

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
        if not _confirm("   Uni admin qilib, parolini yangilaymi? (ha/yo'q): "):
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


# ─────────────────────────── Qo'shish / yangilash ──────────────────────────

def _create_or_update_user(db) -> None:
    username = input("Login (username): ").strip()
    if not username:
        print("❌ Login bo'sh bo'lishi mumkin emas.")
        return

    existing = db.query(models.User).filter(models.User.username == username).first()
    if existing:
        print(f"ℹ️  '{username}' allaqachon mavjud (rol: {existing.role}).")
        if not _confirm("   Parol/rolini yangilaymi? (ha/yo'q): "):
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
        if _admin_count(db, exclude_user_id=existing.id) == 0:
            print("❌ Bu — bazadagi OXIRGI admin. Uning rolini boshqasiga o'zgartirib bo'lmaydi.")
            print("   Avval boshqa birontasini admin qiling, keyin qayta urinib ko'ring.")
            return

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


# ─────────────────────────── Ro'yxat ────────────────────────────────────

def _list_users(db) -> None:
    users = db.query(models.User).order_by(models.User.id).all()
    if not users:
        print("ℹ️  Bazada hech qanday foydalanuvchi yo'q.")
        return
    print(f"\n{'ID':<4} {'Login':<20} {'F.I.O':<28} {'Rol':<10} {'Shifokor'}")
    print("-" * 80)
    for u in users:
        doctor_label = ""
        if u.doctor_id:
            doctor = db.query(models.Doctor).filter(models.Doctor.id == u.doctor_id).first()
            doctor_label = f"#{u.doctor_id} — {doctor.fullname}" if doctor else f"#{u.doctor_id} (topilmadi)"
        print(f"{u.id:<4} {u.username:<20} {(u.fullname or ''):<28} {u.role:<10} {doctor_label}")
    print(f"\nJami: {len(users)} ta foydalanuvchi.")


# ─────────────────────────── O'chirish ──────────────────────────────────

def _delete_user(db) -> None:
    username = input("O'chiriladigan login (username): ").strip()
    if not username:
        print("❌ Login bo'sh bo'lishi mumkin emas.")
        return

    target = db.query(models.User).filter(models.User.username == username).first()
    if not target:
        print(f"❌ '{username}' topilmadi.")
        return

    if target.role == "admin" and _admin_count(db, exclude_user_id=target.id) == 0:
        print("❌ Bu — bazadagi OXIRGI admin. Uni o'chirib bo'lmaydi (tizim")
        print("   kirish huquqisiz qolib ketadi). Avval boshqa birontasini")
        print("   admin qiling, keyin qayta urinib ko'ring.")
        return

    print(f"\n⚠️  '{username}' (rol: {target.role}) O'CHIRILADI. Bu qaytarib bo'lmaydi.")
    if not _confirm("   Aniq o'chirilsinmi? (ha/yo'q): "):
        print("Bekor qilindi.")
        return
    if not _confirm(f"   Tasdiqlash uchun yana bir bor: '{username}'ni o'chirishga rozimisiz? (ha/yo'q): "):
        print("Bekor qilindi.")
        return

    db.delete(target)
    db.commit()
    print(f"✅ '{username}' o'chirildi.")


# ─────────────────────────── Asosiy menyu ───────────────────────────────

def _main_menu(db) -> None:
    actions = {
        "1": ("Yangi foydalanuvchi qo'shish / mavjudini yangilash", _create_or_update_user),
        "2": ("Foydalanuvchilar ro'yxatini ko'rish", _list_users),
        "3": ("Foydalanuvchini o'chirish", _delete_user),
    }
    while True:
        print("\n" + "=" * 50)
        print("MedFlow — Foydalanuvchilarni boshqarish")
        print("=" * 50)
        for key, (label, _) in actions.items():
            print(f"  [{key}] {label}")
        print("  [4] Chiqish")
        choice = input("\nTanlang (1-4): ").strip()

        if choice == "4":
            print("Xayr!")
            return
        action = actions.get(choice)
        if not action:
            print("❌ Noto'g'ri tanlov, 1-4 oralig'ida raqam kiriting.")
            continue
        _, handler = action
        handler(db)


def main() -> None:
    # alembic ishlatilmagan hollarda ham jadvallar mavjud bo'lishi uchun —
    # xavfsiz, mavjud jadvallarga tegmaydi.
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        has_admin = db.query(models.User).filter(models.User.role == "admin").first() is not None
        if not has_admin:
            _create_first_admin(db)
        else:
            _main_menu(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
