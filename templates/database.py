# database.py
"""
SQLAlchemy engine, session factory, and declarative base.
Single source of truth for DB configuration — every other module
(models, modules/*, main) imports from here.

⬅️ YANGI (5-band, 5.1/5.2): DATABASE_URL endi environment o'zgaruvchisi
(yoki .env fayli) orqali sozlanadi, kodga "qattiq yozilmagan". Agar
DATABASE_URL o'rnatilmasa, xavfsiz standart — mahalliy SQLite fayli —
ishlatiladi (development uchun qulay). Productionda bir nechta xodim
bir vaqtda yozayotgan bo'lsa, .env faylida DATABASE_URL'ni PostgreSQL'ga
o'zgartirish kifoya — kodning boshqa hech qanday qismini o'zgartirish
shart emas (SQLAlchemy farqni abstraktsiya qiladi).
"""
import os
from typing import Generator

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base, Session

# .env faylini eng ilk bosqichda yuklaymiz — bu fayl models.py orqali,
# demak auth.py'dan OLDIN import qilinadi, shuning uchun .env'dagi
# CLINICFLOW_SECRET_KEY ham shu yerda yuklanib ulguradi.
load_dotenv()

# 📂 Ma'lumotlar bazasi manzili — .env yoki environment'dan.
# Standart: mahalliy SQLite fayli (development uchun qulay, o'rnatish
# talab qilmaydi). Productionda: DATABASE_URL=postgresql+psycopg2://...
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./clinicflow.db")
_IS_SQLITE = DATABASE_URL.startswith("sqlite")

# check_same_thread=False faqat SQLite uchun kerak (FastAPI so'rovlarni
# turli threadlarda qayta ishlashi mumkin). PostgreSQL/MySQL kabi haqiqiy
# server-asosidagi bazalar uchun bu parametr kerak emas va xato beradi.
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if _IS_SQLITE else {},
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:
    """
    ⚙️ Yuklama (load) ostida barqarorlik uchun muhim ikkita sozlama:

    - WAL (Write-Ahead Logging): standart SQLite rejimida YOZISH paytida
      barcha O'QISHLAR ham bloklanadi — bitta kassir to'lov kiritayotganda
      boshqa hammaning sahifalari "muallif" bo'lib qoladi. WAL rejimida
      o'qishlar yozish bilan bir vaqtda davom etadi, faqat ikkita yozish
      bir-birini bloklaydi.
    - busy_timeout: WAL bilan ham ikkita bir vaqtdagi YOZISH to'qnashishi
      mumkin. Standart holatda SQLite darhol "database is locked" xatosini
      qaytaradi. busy_timeout SQLite'ga xatolik qaytarishdan oldin bir
      necha soniya kutib turishni buyuradi — foydalanuvchi xatolik
      ko'rmaydi, so'rov shunchaki bir oz kutadi.

    MUHIM: bu sozlamalar faqat SQLite uchun ma'noli — PostgreSQL'ga
    ulanganda bu funksiya chaqirilmaydi (PostgreSQL'ning o'zi allaqachon
    bir nechta yozuvchini xavfsiz boshqaradi, MVCC orqali).
    """
    if not _IS_SQLITE:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

# Har bir so'rov (request) uchun alohida seans (session) yaratuvchi zavod
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Barcha ORM modellari meros oladigan deklarativ bazaviy sinf
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency: har bir so'rov uchun DB seansini ochadi va
    so'rov tugagach — muvaffaqiyatli bo'lsa ham, xatolik bo'lsa ham — yopadi.
    Barcha modullar (patients, appointments, dashboard, payments) SHU bitta
    dependency'dan foydalanishi kerak, alohida nusxa yaratmasin.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
