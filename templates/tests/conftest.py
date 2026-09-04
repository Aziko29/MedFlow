# tests/conftest.py
"""
Prompt 15.1 — pytest/FastAPI TestClient uchun umumiy fixture'lar.

MUHIM TARTIB: env o'zgaruvchilar (CLINICFLOW_SECRET_KEY,
CLINICFLOW_FIELD_KEY, CLINICFLOW_BLIND_INDEX_KEY, DATABASE_URL) ushbu
faylning ENG BOSHIDA, `main`/`database`/`auth`/`crypto_fields`ni import
qilishdan OLDIN o'rnatiladi — bu modullar import vaqtida (module-level)
shu qiymatlarni o'qiydi (masalan auth.py: "SECRET_KEY yo'q bo'lsa
RuntimeError"), shuning uchun keyinroq monkeypatch bilan o'rnatish
kech qoladi.

DATABASE_URL ataylab productiondagi clinicflow.db'ga EMAS, vaqtinchalik
(temp) faylga yo'naltirilgan — testlar haqiqiy ma'lumotlarni hech qachon
ishga tushirmaydi/o'zgartirmaydi.
"""
import base64
import datetime
import os
import secrets
import tempfile

# ── 1) Testlar uchun maxfiy kalitlar (production .env'ga TEGILMAYDI) ──
os.environ.setdefault("CLINICFLOW_SECRET_KEY", secrets.token_hex(32))
os.environ.setdefault(
    "CLINICFLOW_FIELD_KEY", base64.b64encode(secrets.token_bytes(32)).decode()
)
os.environ.setdefault(
    "CLINICFLOW_BLIND_INDEX_KEY", base64.b64encode(secrets.token_bytes(32)).decode()
)

# ── 2) Alohida, vaqtinchalik SQLite fayli (test-only DB) ──────────────
_TEST_DB_FD, _TEST_DB_PATH = tempfile.mkstemp(prefix="clinicflow_test_", suffix=".db")
os.close(_TEST_DB_FD)
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"

# Telegram/SMS/Redis integratsiyalari sinovda o'chirilgan holda qoladi
# (ular ixtiyoriy — bo'sh qiymat "jim o'chirilgan" degani, .env.example'ga
# qarang), reminder_service/backup_manager fon jarayonlari esa lifespan
# ichida (TestClient "with" siz ishlatilganda) umuman ishga tushmaydi.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "")
os.environ.setdefault("CLINICFLOW_REDIS_URL", "")

# ⚠️ CLINICFLOW_ADMIN_ACTIONS_PASSWORD ataylab shu yerda GLOBAL
# o'rnatilmaydi — "500: server sozlamasi to'liq emas" holatini ham test
# qilish kerak (talab: env yo'q bo'lganda xavfli amallar bloklanadi).
# Har bir testga kerak bo'lganda monkeypatch.setenv/delenv orqali
# alohida o'rnatiladi (qarang: test_rbac.py TestDangerousAction).

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import models
from auth import create_session_token, hash_password
from database import SessionLocal, engine

# main.py import qilinganda modul-avto-discovery ishga tushadi va
# barcha routerlar (jumladan bugungi backup/check-path tuzatishi)
# app'ga ulanadi.
import main  # noqa: E402  (env o'rnatilgandan KEYIN import qilinishi shart)
from rate_limiter import limiter  # noqa: E402

DEFAULT_PASSWORD = "Test!Passw0rd123"


# ==============================================
# RATE LIMITER: har bir test oldidan tozalanadi
#
# `limiter` (slowapi) — butun test-sessiya davomida BITTA, jarayon
# darajasidagi singleton (rate_limiter.py). Agar buni har bir test
# oldidan tozalamasak, /api/auth/login'ga turli test FUNKSIYALARIDAN
# qilingan chaqiruvlar BIR XIL "5/minute" hisoblagichni bo'lishadi va
# ikkinchi/uchinchi login-testi kutilmagan 429 (Too Many Requests) olib
# qulashi mumkin edi — bu testning o'zi emas, balki testlar orasidagi
# YASHIRIN bog'liqlik (state leakage) bo'lardi.
# ==============================================
@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    try:
        limiter._storage.reset()
    except Exception:
        pass
    yield


# ==============================================
# DB: har bir test uchun toza sxema
# ==============================================
@pytest.fixture(autouse=True)
def _reset_db():
    """Har bir test FUNKSIYASIDAN oldin sxemani butunlay qayta yaratadi
    — testlar bir-biriga ta'sir qilmasligi (izolyatsiya) uchun. SQLite
    fayl-asosli bo'lgani uchun bu tez (mikrosoniyalar)."""
    models.Base.metadata.drop_all(bind=engine)
    models.Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture()
def db() -> Session:
    """To'g'ridan-to'g'ri DB yozish/o'qish uchun (test setup/assert).
    Ilova (TestClient orqali kelgan so'rovlar) o'zining ALOHIDA
    SessionLocal() seansidan foydalanadi — ikkalasi ham bir xil SQLite
    faylga ishora qilgani uchun committed ma'lumotlar ko'rinadi."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ==============================================
# APP / CLIENT
# ==============================================
@pytest.fixture()
def client() -> TestClient:
    """Lifespan (reminder_service/backup_manager fon jarayonlari) ATAYLAB
    ishga TUSHIRILMAYDI — TestClient "with" blokisiz ishlatiladi, chunki
    testlar HTTP darajasidagi xatti-harakatga (status kodlar/RBAC) tegishli,
    fon scheduler'lariga emas."""
    return TestClient(main.app)


# ==============================================
# FOYDALANUVCHI FABRIKASI (har bir rol uchun)
# ==============================================
_username_counter = {"n": 0}


def _unique_username(role: str) -> str:
    _username_counter["n"] += 1
    return f"test_{role}_{_username_counter['n']}"


@pytest.fixture()
def make_doctor(db: Session):
    """Doctor jadvalida yozuv yaratuvchi fabrika — doctor/lab_doctor
    rolidagi User'larni User.doctor_id orqali bog'lash uchun."""
    def _make(fullname: str = "Test Shifokor", specialty: str = "Terapevt") -> models.Doctor:
        doctor = models.Doctor(fullname=fullname, specialty=specialty, is_active=True)
        db.add(doctor)
        db.commit()
        db.refresh(doctor)
        return doctor
    return _make


@pytest.fixture()
def make_user(db: Session, make_doctor):
    """Berilgan rol uchun User yaratadi (parol DEFAULT_PASSWORD bilan
    xeshlanadi). role in ("doctor", "lab_doctor") bo'lsa, avtomatik
    bog'langan Doctor yozuvi ham yaratiladi (agar doctor_id berilmagan
    bo'lsa)."""
    def _make(role: str, *, doctor_id: int = None, username: str = None) -> models.User:
        if role in ("doctor", "lab_doctor") and doctor_id is None:
            doctor_id = make_doctor(fullname=f"Dr. {role}").id
        user = models.User(
            username=username or _unique_username(role),
            password_hash=hash_password(DEFAULT_PASSWORD),
            fullname=f"Test {role}",
            role=role,
            doctor_id=doctor_id,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    return _make


@pytest.fixture()
def login_as(client: TestClient):
    """Berilgan User uchun signed session-token yaratib, TestClient
    cookie'siga o'rnatadi va SHU clientni qaytaradi (haqiqiy POST
    /api/auth/login orqali EMAS — bu 5/minute slowapi rate-limitiga
    urilmaslik uchun ataylab shunday: real login endpoint faqat
    TestLoginSecurityAlerts'da, o'zining maxsus testida ishlatiladi)."""
    def _login(user: models.User) -> TestClient:
        token = create_session_token(user.id)
        client.cookies.set("cf_session", token)
        return client
    return _login


# ==============================================
# TIPIK YORDAMCHI OBYEKTLAR (Patient/Appointment/Payment)
# ==============================================
_patient_phone_counter = {"n": 0}


@pytest.fixture()
def make_patient(db: Session):
    def _make(fullname: str = "Test Bemor", phone: str = None) -> models.Patient:
        _patient_phone_counter["n"] += 1
        patient = models.Patient(
            fullname=fullname,
            phone=phone or f"+998901234{_patient_phone_counter['n']:03d}",
        )
        db.add(patient)
        db.commit()
        db.refresh(patient)
        return patient
    return _make


@pytest.fixture()
def make_appointment_with_payment(db: Session, make_patient, make_doctor):
    """cancel/refund testlari uchun: 'completed' statusdagi Payment,
    unga tegishli Appointment va Patient bilan birga yaratadi."""
    def _make(amount: int = 100_000) -> models.Payment:
        patient = make_patient()
        doctor = make_doctor()
        appointment = models.Appointment(
            patient_id=patient.id,
            doctor_id=doctor.id,
            scheduled_time=datetime.datetime.utcnow(),
            status="completed",
            price=amount,
        )
        db.add(appointment)
        db.commit()
        db.refresh(appointment)

        payment = models.Payment(
            patient_id=patient.id,
            appointment_id=appointment.id,
            amount=amount,
            status="completed",
        )
        db.add(payment)
        db.commit()
        db.refresh(payment)
        return payment
    return _make
