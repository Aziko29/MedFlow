# models.py
"""
SQLAlchemy ORM models — this is the DOMAIN model, not just a CRUD schema.

Compared to the previous version this fixes the structural issues found in
the audit (see the report you received):

  1. Payment now belongs to an Appointment (appointment_id, NOT NULL).
     A payment can no longer float free of any service — every UZS coming
     in is tied to the appointment it was collected for.
  2. Patient.balance (a single ever-growing counter that conflated "debt"
     and "amount paid") is REMOVED. Debt is now derived, per appointment,
     as price - sum(payments), and a patient-level summary is computed on
     the fly in modules/patients.py. Nothing stores a fake running total.
  3. Doctor.consultation_price is the price list ("narxnoma") entry. When
     an Appointment is booked, the doctor's current price is COPIED onto
     Appointment.price, so past invoices don't change if the doctor's
     price changes later.
  4. Appointment.scheduled_time is now a real DateTime column (not a
     free-text string), so "today's queue" can be filtered with a proper
     date comparison instead of trusting whatever status happens to be set.
  5. Appointment.status is constrained to a fixed set of values and status
     changes are validated by a state machine in modules/appointments.py
     (waiting -> in_progress -> completed, plus delayed/cancelled/no_show).
     Two new terminal statuses were added: cancelled, no_show.
  6. See modules/patients.py (get_patient_history) and modules/doctors.py
     (get_doctor_appointments) + the new /patients/{id} and /doctors/{id}
     detail pages in main.py — this is where "island pages" got wired
     together.
  7. Patient gained gender/birth_date/address/medical_notes. Doctor gained
     working_hours + consultation_price, and double-booking is rejected in
     modules/appointments.py by checking for an existing, non-cancelled
     appointment for the same doctor at the same scheduled_time.
  9. A minimal role system was added (User model + auth.py) so that
     reception / doctor / cashier / admin have different permissions
     instead of every endpoint being callable by anyone.
"""
import datetime
import secrets

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    event,
)
from sqlalchemy.orm import relationship

from database import Base
from crypto_fields import EncryptedString, EncryptedText, blind_index

# ── Status/role constants (single source of truth, imported by schemas.py
#    and every modules/*.py file instead of re-typing string literals) ──
APPOINTMENT_STATUSES = (
    "waiting",
    "in_progress",
    "delayed",
    "completed",
    "cancelled",
    "no_show",
)

# Allowed forward transitions for the appointment state machine.
# A status that isn't a key here is terminal (no further transition allowed).
APPOINTMENT_TRANSITIONS = {
    "waiting": {"in_progress", "delayed", "cancelled", "no_show"},
    "delayed": {"waiting", "in_progress", "cancelled", "no_show"},
    "in_progress": {"completed", "delayed", "cancelled"},
}

USER_ROLES = ("admin", "reception", "doctor", "cashier")


class User(Base):
    """🔐 Tizim foydalanuvchilari — rol asosida ruxsatlar (reception,
    doctor, cashier, admin) uchun. Parol hech qachon ochiq matnda
    saqlanmaydi (auth.py'dagi hash_password() bilan xeshlanadi)."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    fullname = Column(String, nullable=False)
    role = Column(String, nullable=False, default="reception")
    # Agar rol="doctor" bo'lsa, shu foydalanuvchi qaysi Doctor yozuviga mos
    # kelishini bildiradi (shifokor faqat o'z navbatlarini ko'rishi uchun).
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=True)

    doctor = relationship("Doctor", back_populates="user_account")


class Doctor(Base):
    """🧑‍⚕️ Shifokorlar jadvali"""

    __tablename__ = "doctors"

    id = Column(Integer, primary_key=True, index=True)
    fullname = Column(String, nullable=False)
    specialty = Column(String, nullable=False)  # LOR, Stomatolog, Terapevt h.k.
    room = Column(String, nullable=True)
    # 💵 Narxnoma: shu shifokordagi konsultatsiyaning joriy narxi (UZS).
    consultation_price = Column(Integer, nullable=False, default=0)
    # 🕒 Ish jadvali — ko'rsatish uchun matn, masalan "Du-Ju 09:00-18:00".
    working_hours = Column(String, nullable=True, default="09:00 - 18:00")
    is_active = Column(Boolean, default=True)

    appointments = relationship(
        "Appointment", back_populates="doctor", cascade="all, delete-orphan"
    )
    user_account = relationship("User", back_populates="doctor", uselist=False)
    lab_results = relationship("LabResult", back_populates="doctor")


class Patient(Base):
    """👥 Bemorlar jadvali"""

    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)
    fullname = Column(String, nullable=False)
    # 🔐 AES-256-GCM bilan shifrlangan (crypto_fields.py). Ustunning o'zi
    # endi UNIQUE emas — shifrlash tasodifiy nonce ishlatadi, shuning uchun
    # bir xil telefon raqami har safar boshqacha ciphertext beradi va DB
    # darajasidagi unique constraint ishlamay qoladi. Haqiqiy dublikat
    # tekshiruvi endi pastdagi `phone_bidx` (blind index) orqali bo'ladi.
    phone = Column(EncryptedString(32, aad_context="patient.phone"), nullable=False)
    phone_bidx = Column(String(64), unique=True, index=True, nullable=False)
    gender = Column(String, nullable=True)  # "M" | "F"
    birth_date = Column(Date, nullable=True)
    address = Column(EncryptedString(255, aad_context="patient.address"), nullable=True)
    medical_notes = Column(EncryptedText(aad_context="patient.medical_notes"), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # 📲 Telegram reminder integratsiyasi: bemor botga /start bosib, kontaktini
    # (telefon raqamini) yuborganda phone_bidx orqali topilib shu yerga yoziladi.
    telegram_chat_id = Column(String(32), unique=True, index=True, nullable=True)

    # ⚙️ Bot sozlamalari (Sozlama/SOS menyusi):
    # Interfeys tili — "uz" | "ru" | "en". Bot shu tilda javob beradi.
    telegram_language = Column(String(2), nullable=False, default="uz")
    # Ovozsiz rejim: True bo'lsa, avtomatik eslatmalar (24h/2h) yuborilmaydi,
    # lekin bemor botdan qo'lda foydalanishda davom etishi mumkin.
    telegram_muted = Column(Boolean, nullable=False, default=False)
    # Bemor o'zi sozlaydigan eslatma vaqtlari (soat, navbatdan necha soat
    # oldin). NULL/0 = shu bosqich o'chirilgan (eslatma yuborilmaydi).
    # Standart: birinchi eslatma 24 soat oldin, ikkinchisi 2 soat oldin —
    # bu reminder_service.py'dagi eski qattiq REMINDER_WINDOWS bilan bir xil.
    reminder_first_hours = Column(Integer, nullable=True, default=24)
    reminder_second_hours = Column(Integer, nullable=True, default=2)

    appointments = relationship(
        "Appointment", back_populates="patient", cascade="all, delete-orphan"
    )
    payments = relationship(
        "Payment", back_populates="patient", cascade="all, delete-orphan"
    )
    lab_results = relationship(
        "LabResult", back_populates="patient", cascade="all, delete-orphan"
    )


@event.listens_for(Patient, "before_insert")
@event.listens_for(Patient, "before_update")
def _sync_patient_phone_bidx(mapper, connection, target: "Patient") -> None:
    """phone_bidx har doim joriy `phone` bilan mos bo'lishini ta'minlaydi
    (qidiruv/dublikat tekshiruvi shu ustun orqali, plaintext saqlanmaydi)."""
    target.phone_bidx = blind_index(target.phone) if target.phone else None


class Appointment(Base):
    """📅 Qabul (navbat) jadvali — bitta xizmat ko'rsatish holati."""

    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False, index=True)

    # index=True: dashboard/live-queue/double-booking tekshiruvlari doim shu
    # ikki ustun bo'yicha filtrlaydi — indekssiz jadval kattalashgani sayin
    # (minglab yozuv) bu so'rovlar sekinlashadi (full table scan).
    scheduled_time = Column(DateTime, nullable=False, index=True)
    status = Column(String, nullable=False, default="waiting", index=True)

    # Bron qilingan paytdagi narx — shifokorning narxi keyin o'zgarsa ham,
    # eski qabulning narxi o'zgarmay qoladi (haqiqiy hisob-kitob uchun).
    price = Column(Integer, nullable=False, default=0)
    cancel_reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # 📲 Telegram eslatma tizimi (reminder_service.py + modules/reminders.py):
    # 24 soat va 2 soat oldingi xabarlar faqat bir marta yuborilishi uchun.
    reminder_sent_24h = Column(Boolean, nullable=False, default=False, index=True)
    reminder_sent_2h = Column(Boolean, nullable=False, default=False, index=True)
    # Bemor autentifikatsiyasiz (Telegramdagi link orqali) o'z navbatini bekor
    # qilishi uchun tasodifiy, taxmin qilib bo'lmaydigan token (256-bit).
    cancel_token = Column(String(64), unique=True, index=True, nullable=True)

    patient = relationship("Patient", back_populates="appointments")
    doctor = relationship("Doctor", back_populates="appointments")
    payments = relationship(
        "Payment", back_populates="appointment", cascade="all, delete-orphan"
    )

    @property
    def paid_amount(self) -> int:
        return sum(p.amount for p in self.payments if not p.is_refund) - sum(
            p.amount for p in self.payments if p.is_refund
        )

    @property
    def debt(self) -> int:
        return max(self.price - self.paid_amount, 0)


@event.listens_for(Appointment, "before_insert")
def _generate_appointment_cancel_token(mapper, connection, target: "Appointment") -> None:
    if not target.cancel_token:
        target.cancel_token = secrets.token_urlsafe(32)


class Payment(Base):
    """💰 To'lovlar jadvali — HAR BIR to'lov aniq bitta qabulga (Appointment)
    bog'langan bo'lishi shart."""

    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    appointment_id = Column(Integer, ForeignKey("appointments.id"), nullable=False, index=True)
    amount = Column(Integer, nullable=False)  # UZS hisobida, musbat son
    note = Column(String, nullable=True)
    is_refund = Column(Boolean, default=False)
    refund_of_payment_id = Column(
        Integer, ForeignKey("payments.id"), nullable=True, unique=True, index=True
    )
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    patient = relationship("Patient", back_populates="payments")
    
    # 🔴 XATOLIK SHU YERDA EDI (oldin appointment = relationship(..., back_populates="payments") edi)
    # Buni to'g'ri bog'lanishga o'zgartiramiz:
    appointment = relationship("Appointment", back_populates="payments")
    
    refund_of = relationship("Payment", remote_side=[id], backref="refund")

class LabResult(Base):
    """🔬 Tahlil natijalari jadvali — modules/lab_results.py shu modelga
    tayanadi (patient_id, doctor_id, test_name, result_data, status,
    created_at). Ilgari bu model models.py'da yo'q edi, shuning uchun
    LabResult moduli import vaqtida ishlamas edi."""

    __tablename__ = "lab_results"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    # Ixtiyoriy: natija qaysi shifokor tomonidan buyurilgani/ko'rib
    # chiqilgani. Shifokor o'chirilsa ham tahlil tarixi yo'qolmasin deb
    # SET NULL xatti-harakati (nullable=True, cascade yo'q).
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=True, index=True)
    test_name = Column(String, nullable=False, index=True)
    result_data = Column(String, nullable=False)
    status = Column(String, nullable=False, default="Tayyor")
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)

    patient = relationship("Patient", back_populates="lab_results")
    doctor = relationship("Doctor", back_populates="lab_results")


class AuditLog(Base):
    """🧾 Audit jurnal (4-band) — kim, qachon, nima qildi. Write (yozuvchi)
    amallar (qo'shish/o'zgartirish/o'chirish/holat almashtirish) har birida
    bitta qator qo'shiladi. Yozuvlar hech qachon o'chirilmaydi yoki
    tahrirlanmaydi — bu haqiqiy audit iz, keyinchalik "kim bu to'lovni
    o'chirdi" kabi savollarga javob berish uchun."""

    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    username = Column(String, nullable=False)
    action = Column(String, nullable=False, index=True)        # masalan: "payment.add"
    entity_type = Column(String, nullable=False, index=True)    # "Payment", "Patient", ...
    entity_id = Column(Integer, nullable=True, index=True)
    details = Column(String, nullable=True)                     # qisqa, inson o'qiy oladigan matn
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)


# NOTE: password hashing lives ONLY in auth.py (PBKDF2, hash_password /
# verify_password). A second, bcrypt-based pair of same-named functions
# used to live here as dead code — nothing called them, but if anything
# ever had (e.g. a future edit to seed.py), login would have silently
# broken for that user because auth.verify_password expects the
# "<salt>$<hex digest>" PBKDF2 format, not a bcrypt hash. Removed rather
# than kept "just in case" — a dead, incompatible auth implementation is
# a bug waiting to happen, not a convenience. It also let bcrypt be
# dropped from requirements.txt entirely: the app has exactly one
# password-hashing scheme now, and installing from a clean
# requirements.txt no longer crashes on `import bcrypt`.