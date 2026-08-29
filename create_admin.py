# create_user.py
"""
Admin panelda faqat Doctor (narxnoma/profil) yozuvi yaratiladi — LOGIN
hisobi emas. Shifokor (yoki boshqa xodim) tizimga kirishi uchun alohida
User yozuvi kerak, va agar rol="doctor" bo'lsa, u aynan qaysi Doctor
yozuviga tegishli ekani (doctor_id) ko'rsatilishi shart — aks holda u
"o'z navbatlarini" ko'ra olmaydi.

Bu skript shuni qiladi: username/parol/rolni so'raydi, rol "doctor"
bo'lsa mavjud Doctor yozuvlaridan birini tanlashni so'raydi va User'ni
o'sha Doctor'ga bog'laydi.

XAVFSIZLIK: create_admin.py bilan bir xil tamoyil — parol hech qayerda
yozilmagan, faqat ishga tushirilganda getpass() bilan so'raladi va
darhol Argon2id bilan (bir tomonlama) xeshlanadi.

ISHLATISH:
    python create_user.py
"""
import getpass
import re
import sys

from database import Base, SessionLocal, engine
from auth import hash_password
import models

MIN_LENGTH = 14
ROLES = ("admin", "reception", "doctor", "cashier")


def _password_is_strong(pw: str) -> str | None:
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


def _prompt_password() -> str:
    while True:
        pw1 = getpass.getpass("Parol (ekranda ko'rinmaydi): ")
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
    print("Rollar: " + ", ".join(ROLES))
    while True:
        role = input("Rol tanlang: ").strip().lower()
        if role in ROLES:
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


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
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
    finally:
        db.close()


if __name__ == "__main__":
    main()