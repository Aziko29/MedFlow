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
    JSON,
    String,
    Text,
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

USER_ROLES = (
    "admin",
    "reception",
    "doctor",
    "cashier",
    # ⬅️ YANGI: assistant_admin — yordamchi admin (hisobotlarni ko'rish,
    # xodimlarni ko'rish/qo'shish, bemorlarni/navbatlarni ko'rish,
    # dashboard va xavfsizlik monitoringini FAQAT o'qish — huquqlar
    # ro'yxati main.py'dagi ROLE_MODULE_ACCESS'da aniqlangan).
    "assistant_admin",
    # ⬅️ YANGI: lab_doctor — laboratoriya shifokori (faqat tahlil
    # natijalarini kiritish/ko'rish va o'ziga biriktirilgan bemorlarni
    # ko'rish; boshqa modullarga kirish yo'q). "doctor" kabi doctor_id
    # orqali bitta Doctor yozuviga bog'lanadi (qarang: modules/auth_module.py
    # _validate_doctor_link) — shu bog'lanish orqali "o'ziga biriktirilgan
    # bemorlar" LabResult.doctor_id bo'yicha aniqlanadi.
    "lab_doctor",
)

# Bemor moduli — allergiya og'irlik darajasi va surunkali kasallik holati
# uchun ruxsat etilgan qiymatlar (schemas.py shu yerdan import qiladi,
# APPOINTMENT_STATUSES bilan bir xil naqsh).
ALLERGY_SEVERITIES = ("yengil", "o'rta", "og'ir")
CHRONIC_CONDITION_STATUSES = ("faol", "remissiyada", "davolangan")

# Admin profili moduli (Prompt 8) — "ish o'rni" (Position) qaysi rolga
# mo'ljallanganini bildiradi. Bu USER_ROLES BILAN BIR XIL EMAS: "nurse"
# (hamshira) tizimga kirish huquqiga ega bo'lgan login-roli emas (u
# USER_ROLES'da yo'q), shuning uchun alohida, kichikroq ro'yxat sifatida
# aniqlangan (schemas.py shu yerdan import qiladi, boshqa *_STATUSES /
# *_ROLES konstantalari bilan bir xil naqsh).
POSITION_ROLES = ("doctor", "nurse", "lab_doctor", "cashier", "reception")

# ── Xodimning o'z profilida parolni almashtirish limiti ──────────────
# Xodim o'z profilidan ("Sozlamalar" sahifasi) ketma-ket ko'pi bilan shu
# marta parolni o'zi almashtira oladi. Shu limitga yetgach, tizim uni
# admin bilan bog'lanishga yo'naltiradi — admin admin-reset-password
# orqali yangi vaqtinchalik parol bergach, User.self_password_change_count
# 0'ga qaytariladi va xodimga yana SELF_PASSWORD_CHANGE_LIMIT marta o'zi
# almashtirish imkoni beriladi (qarang: modules/auth_module.py).
SELF_PASSWORD_CHANGE_LIMIT = 3

# ── Sozlamalar moduli (Prompt 12) — 2-qatlam parol bilan himoyalangan
# xavfli amallar ──────────────────────────────────────────────────────
# POST /settings/dangerous-action shu ro'yxatdagi action_type'lardan
# birini qabul qiladi (schemas.DangerousActionRequest shu yerdan import
# qiladi, boshqa *_STATUSES/*_ROLES konstantalari bilan bir xil naqsh).
# Har biri modules/settings_module.py'da alohida handler'ga ega.
DANGEROUS_ACTION_TYPES = (
    "clear_db",
    "delete_all_patients",
    "restart_system",
    "reset_sessions",
)


class User(Base):
    """🔐 Tizim foydalanuvchilari — rol asosida ruxsatlar (reception,
    doctor, cashier, admin, assistant_admin, lab_doctor) uchun. Parol
    hech qachon ochiq matnda saqlanmaydi (auth.py'dagi hash_password()
    bilan xeshlanadi)."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    fullname = Column(String, nullable=False)
    role = Column(String, nullable=False, default="reception")
    # Agar rol="doctor" YOKI "lab_doctor" bo'lsa, shu foydalanuvchi qaysi
    # Doctor yozuviga mos kelishini bildiradi (shifokor/lab shifokori
    # faqat o'ziga tegishli navbatlar/bemorlar/tahlillarni ko'rishi uchun).
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=True)

    # 🔐 Xodimning "Sozlamalar" sahifasidan o'zi nechta marta parolni
    # ketma-ket almashtirganini sanaydi (SELF_PASSWORD_CHANGE_LIMIT bilan
    # solishtiriladi). Admin admin-reset-password orqali vaqtinchalik
    # parol berganda yoki xodim yangi qo'shilganda 0'ga qaytariladi —
    # shu bilan bu "ketma-ket, oxirgi admin tekshiruvidan beri" hisoblagich
    # bo'lib qoladi, umrbod cheklov emas.
    self_password_change_count = Column(Integer, nullable=False, default=0)

    # 🆕 Sozlamalar moduli (Prompt 12) — "Profil" bo'limida xodim o'zi
    # tahrirlaydigan qo'shimcha shaxsiy ma'lumotlar. fullname (majburiy,
    # login/audit/kvitansiyalarda ishlatiladi) bilan almashtirilmaydi —
    # bular unga QO'SHIMCHA, ixtiyoriy maydonlar, shuning uchun barchasi
    # nullable. email/phone HECH QANDAY login/SMS logikasida ishlatilmaydi
    # (bemor SMS'lari uchun alohida Patient.phone bor) — faqat ko'rsatish/
    # bog'lanish uchun.
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)

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

    # 🖼️ Shifokor rasmi: bemor rasmi bilan bir xil pattern (patient.photo_path
    # ga qara) — shifrlanmaydi, static/uploads/doctors/{id}.jpg ga ishora
    # qiluvchi URL yo'l. Rasm bo'lmasa NULL, shablon placeholder ko'rsatadi.
    photo_path = Column(String, nullable=True)

    # 🪪 Litsenziya raqami — maxfiy emas, tibbiy amaliyot litsenziyasi
    # raqami, tekshiruv/hisobot maqsadida ko'rsatiladi.
    license_number = Column(String, nullable=True)

    # 📈 Ish tajribasi (to'liq yil hisobida).
    experience_years = Column(Integer, nullable=True)

    # 🏅 Malaka toifasi — masalan "Oliy toifa" / "Birinchi toifa" /
    # "Ikkinchi toifa". Erkin matn (bemordagi blood_type kabi cheklangan
    # qiymatlar to'plami emas, chunki toifalar davlat tomonidan vaqti-vaqti
    # bilan yangilanishi mumkin — frontendda select orqali cheklanadi).
    qualification_category = Column(String, nullable=True)

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

    # 🖼️ Bemor rasmi: shifrlanmagan — bu maxfiy ma'lumot emas, shunchaki
    # static/uploads/patients/{id}.jpg fayliga ishora qiluvchi URL yo'l
    # (masalan "/static/uploads/patients/5.jpg"). Rasm mavjud bo'lmasa —
    # NULL, shablon (template) o'rniga placeholder avatar ko'rsatadi.
    photo_path = Column(String, nullable=True)

    # 🩸 Qon guruhi (masalan "A+", "O-", "AB+"). Shifrlanmagan — favqulodda
    # holatda barcha rollarga (admin/reception/doctor/cashier) tezkor
    # ko'rinishi kerak, shifrlash bu maqsadga qarshi ishlar edi.
    blood_type = Column(String(3), nullable=True)

    # 🚨 Favqulodda holat kontakti — shaxsiy ma'lumot (PII), shuning uchun
    # phone/address bilan bir xil pattern: EncryptedString (crypto_fields.py).
    emergency_contact_name = Column(
        EncryptedString(64, aad_context="patient.emergency_name"), nullable=True
    )
    emergency_contact_phone = Column(
        EncryptedString(32, aad_context="patient.emergency_phone"), nullable=True
    )

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

    # 🛏️ Palata (statsionar davolash) — Prompt 7. Bemor palataga
    # yotqizilganda room_number/admitted_at to'ldiriladi va
    # is_admitted=True bo'ladi; chiqarilganda discharged_at yoziladi va
    # is_admitted=False bo'ladi. Tarix saqlanadi: discharge qilingandan
    # keyin ham admitted_at/discharged_at/room_number o'chirilmaydi —
    # keyingi safar qayta yotqizilganda ular ustiga yoziladi (bir vaqtning
    # o'zida faqat bitta "joriy" yotqizilish bo'ladi, chunki bu oddiy
    # Column'lar, alohida tarix jadvali emas).
    room_number = Column(String(20), nullable=True)
    admitted_at = Column(DateTime, nullable=True)
    discharged_at = Column(DateTime, nullable=True)
    is_admitted = Column(Boolean, nullable=False, default=False)

    # 🏛️ Prompt 10 — davlat identifikatsiya tizimi (OneID va o'xshash)
    # integratsiyasi. pinfl/passport phone/address bilan bir xil pattern:
    # EncryptedString (crypto_fields.py) + qidiruv uchun blind index
    # (aynan phone_bidx kabi — AES-GCM tasodifiy nonce ishlatgani uchun
    # ustunning o'zi bo'yicha to'g'ridan-to'g'ri UNIQUE/qidiruv ishlamaydi).
    # Talabnomadagi xom "unique=True oddiy String" o'rniga shu loyihadagi
    # PII-shifrlash standarti qo'llanildi.
    pinfl = Column(EncryptedString(14, aad_context="patient.pinfl"), nullable=True)  # JShShIR
    pinfl_bidx = Column(String(64), unique=True, index=True, nullable=True)
    passport_series = Column(
        EncryptedString(4, aad_context="patient.passport_series"), nullable=True
    )
    passport_number = Column(
        EncryptedString(16, aad_context="patient.passport_number"), nullable=True
    )
    # Davlat tizimi (yoki mock rejim) orqali tekshirilganmi. Faqat
    # modules/gov_integration.py'dagi /patients/register-with-gov yoki
    # /patients/{id}/verify-gov orqali True qilinadi — qo'lda PatientUpdate
    # orqali o'rnatib bo'lmaydi (schemas.PatientBase'da yo'q).
    is_verified = Column(Boolean, nullable=False, default=False)

    appointments = relationship(
        "Appointment", back_populates="patient", cascade="all, delete-orphan"
    )
    payments = relationship(
        "Payment", back_populates="patient", cascade="all, delete-orphan"
    )
    lab_results = relationship(
        "LabResult", back_populates="patient", cascade="all, delete-orphan"
    )
    allergies = relationship(
        "Allergy", back_populates="patient", cascade="all, delete-orphan"
    )
    chronic_conditions = relationship(
        "ChronicCondition", back_populates="patient", cascade="all, delete-orphan"
    )
    treatment_history = relationship(
        "TreatmentHistory",
        back_populates="patient",
        cascade="all, delete-orphan",
        order_by="TreatmentHistory.date.desc()",
    )


@event.listens_for(Patient, "before_insert")
@event.listens_for(Patient, "before_update")
def _sync_patient_phone_bidx(mapper, connection, target: "Patient") -> None:
    """phone_bidx har doim joriy `phone` bilan mos bo'lishini ta'minlaydi
    (qidiruv/dublikat tekshiruvi shu ustun orqali, plaintext saqlanmaydi)."""
    target.phone_bidx = blind_index(target.phone) if target.phone else None
    target.pinfl_bidx = blind_index(target.pinfl) if target.pinfl else None


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
    bog'langan bo'lishi shart.

    Prompt 6: ikki bosqichli qaytarim — `status` asl to'lovning holatini
    bildiradi ("completed" -> admin bekor qiladi -> "cancelled" (= pul
    hali qaytarilmagan, "qaytarish kutilmoqda") -> kassir haqiqiy pulni
    qaytaradi -> "refunded"). Pul harakati faqat "refunded"ga o'tganda
    (is_refund=True qaytarim yozuvi qo'shilganda) sodir bo'ladi — shu
    bilan appointment.debt/dashboard tushumi eski hisob-kitob mantig'i
    bilan mos qoladi."""

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

    # ── Prompt 6: ikki bosqichli qaytarim holati va audit izi ──────────
    status = Column(String, nullable=False, default="completed", index=True)
    cancelled_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    refunded_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    refunded_at = Column(DateTime, nullable=True)
    refund_reason = Column(String, nullable=True)

    patient = relationship("Patient", back_populates="payments")
    
    # 🔴 XATOLIK SHU YERDA EDI (oldin appointment = relationship(..., back_populates="payments") edi)
    # Buni to'g'ri bog'lanishga o'zgartiramiz:
    appointment = relationship("Appointment", back_populates="payments")
    
    refund_of = relationship("Payment", remote_side=[id], backref="refund", foreign_keys=[refund_of_payment_id])
    cancelled_by = relationship("User", foreign_keys=[cancelled_by_id])
    refunded_by = relationship("User", foreign_keys=[refunded_by_id])


# Payment.status uchun ruxsat etilgan qiymatlar (single source of truth).
PAYMENT_STATUSES = ("completed", "cancelled", "refunded")


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


class Allergy(Base):
    """⚠️ Bemor allergiyalari — modules/patients.py'dagi
    /patients/{id}/allergies CRUD shu modelga tayanadi. Bemor o'chirilsa,
    uning allergiya yozuvlari ham o'chadi (Patient.allergies,
    cascade="all, delete-orphan")."""

    __tablename__ = "allergies"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    substance = Column(String, nullable=False)  # masalan: "Penitsillin"
    reaction = Column(String, nullable=True)  # masalan: "toshma, qichishish"
    severity = Column(String, nullable=True)  # ALLERGY_SEVERITIES dan biri
    noted_at = Column(DateTime, default=datetime.datetime.utcnow)

    patient = relationship("Patient", back_populates="allergies")


class ChronicCondition(Base):
    """🩺 Surunkali kasalliklar — modules/patients.py'dagi
    /patients/{id}/chronic-conditions CRUD shu modelga tayanadi. `notes`
    maydoni EncryptedText bilan shifrlangan (crypto_fields.py'dagi
    patternga mos — medical_notes bilan bir xil), chunki bu erkin matn
    tibbiy tafsilotlar (masalan davolash tarixi) o'z ichiga olishi mumkin."""

    __tablename__ = "chronic_conditions"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    name = Column(String, nullable=False)  # masalan: "Qandli diabet II tur"
    diagnosed_date = Column(Date, nullable=True)
    status = Column(String, nullable=False, default="faol")  # CHRONIC_CONDITION_STATUSES
    notes = Column(EncryptedText(aad_context="patient.chronic_notes"), nullable=True)

    patient = relationship("Patient", back_populates="chronic_conditions")


class TreatmentHistory(Base):
    """💊 Davolanishlar tarixi (Prompt 2) — bemorga qo'yilgan tashxis va
    tayinlangan davolash rejasi, sana bo'yicha vaqt chizig'i sifatida
    ko'rsatiladi (templates/patient_detail.html, "Davolanishlar" tab).

    `diagnosis` va `treatment` ikkalasi ham tibbiy sir hisoblanadi,
    shuning uchun ChronicCondition.notes bilan bir xil patternga mos —
    EncryptedText (crypto_fields.py) orqali AES-256-GCM bilan shifrlangan,
    har biri o'zining aad_context'i bilan (boshqa maydonning shifrlangan
    matni bu yerga "ko'chirib qo'yilishi" mumkin bo'lmasligi uchun).

    appointment_id va doctor_id ATAYLAB nullable — yozuv aniq bir tashrif
    yoki shifokorga bog'lanmasdan ham kiritilishi mumkin (masalan eski
    qog'oz kartadan ko'chirilgan tarix). LabResult.doctor_id bilan bir xil
    pattern: shifokor yoki tashrif keyinchalik o'chirilsa ham (agar shunday
    funksiya qo'shilsa), davolanish yozuvining o'zi yo'qolmaydi — faqat
    bog'lanishi bo'shab qoladi (ORM darajasida FK NULL bo'lib qoladi,
    bu yerda ON DELETE CASCADE ishlatilmagan)."""

    __tablename__ = "treatment_history"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    appointment_id = Column(
        Integer, ForeignKey("appointments.id"), nullable=True, index=True
    )
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=True, index=True)
    diagnosis = Column(
        EncryptedText(aad_context="treatment.diagnosis"), nullable=True
    )
    treatment = Column(EncryptedText(aad_context="treatment.plan"), nullable=True)
    date = Column(Date, nullable=False, default=datetime.date.today)

    patient = relationship("Patient", back_populates="treatment_history")
    appointment = relationship("Appointment")
    doctor = relationship("Doctor")


class PatientLoginOTP(Base):
    """📲 FAZA 2 — Bemor portali uchun SMS orqali bir martalik login kodi
    (OTP). Har bir kod so'rovi shu jadvalga bitta qator qo'shadi.

    MUHIM: kod (6 xonali raqam) bazada HECH QACHON ochiq holda
    saqlanmaydi — faqat `code_hash` orqali (bir tomonlama xesh). Kod
    juda qisqa muddatli (bir necha daqiqa) va bir martalik bo'lgani
    uchun sha256 kifoya (auth.py'dagi Argon2id — foydalanuvchi
    parollari kabi uzoq muddatli maxfiy narsalar uchun ishlatiladi;
    bu yerda vazifa boshqacha: tezkor, ko'p yozuvli tekshiruv)."""

    __tablename__ = "patient_login_otp"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    code_hash = Column(String(128), nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    # Tavsiya etilgan amal muddati: created_at + 5 daqiqa (qarang:
    # modules/patient_portal.py, OTP_TTL_SECONDS).
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    # Noto'g'ri kod kiritish urinishlari soni — MAX_VERIFY_ATTEMPTS'dan
    # oshsa, bu kod endi haqiqiy bo'lsa ham rad etiladi (brute-force
    # himoyasi, rate_limiter.py'dagi so'rov-darajasidagi cheklovga
    # qo'shimcha ikkinchi qatlam).
    attempt_count = Column(Integer, nullable=False, default=0)

    patient = relationship("Patient")


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


SECURITY_MESSAGE_PRIORITIES = ("low", "medium", "high")


class SecurityMessage(Base):
    """📨 Xavfsizlik markazi (Prompt 9) — shifokorlar (doctor/lab_doctor)
    tomonidan adminga yuboriladigan savol/xabarlar. from_user_id AuditLog
    bilan bir xil naqsh bo'yicha nullable — yuboruvchi keyinchalik
    o'chirilsa ham (users.id -> SET NULL emas, lekin FK nullable bo'lgani
    uchun) xabarning o'zi yo'qolmaydi. is_read faqat admin (yoki
    assistant_admin/xavfsizlikni ko'rish huquqiga ega shaxs emas — yozish
    amali, shuning uchun faqat admin) tomonidan belgilanadi; o'chirish ham
    faqat admin uchun (qarang: modules/security_center.py)."""

    __tablename__ = "security_messages"

    id = Column(Integer, primary_key=True, index=True)
    from_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    subject = Column(String(200), nullable=False)
    message = Column(Text, nullable=False)
    priority = Column(String(20), nullable=False, default="medium")  # low, medium, high
    is_read = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)

    from_user = relationship("User")


class SystemError(Base):
    """🛑 Tizim xatoliklari jurnali (Prompt 9) — main.py'dagi global
    (500) exception handler va require_role/_require_module orqali
    tutilgan ruxsatsiz kirish urinishlari (401/403) avtomatik shu yerga
    yozadi (qarang: modules/security_center.py -> record_system_error).
    Yozuvlar hech qachon tahrirlanmaydi/o'chirilmaydi — haqiqiy xatolik
    tarixi bo'lib qoladi."""

    __tablename__ = "system_errors"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    endpoint = Column(String(255), nullable=False)
    error_message = Column(Text, nullable=False)
    traceback = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)

    user = relationship("User")


class LoginLog(Base):
    """🔑 Kirish (login) jurnali (Prompt 9) — har bir login urinishi,
    muvaffaqiyatli yoki muvaffaqiyatsiz, modules/auth_module.py login()
    tomonidan shu yerga yoziladi. username AuditLog bilan bir xil
    naqsh bo'yicha alohida matn sifatida saqlanadi (foydalanuvchi
    topilmagan urinishlarda ham, yoki keyinchalik User o'chirilganda ham
    tarix o'qilishi uchun). 3 marta ketma-ket muvaffaqiyatsiz urinish
    aniqlansa, security_center.py adminga avtomatik SecurityMessage
    yaratadi."""

    __tablename__ = "login_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    username = Column(String, nullable=False, index=True)
    success = Column(Boolean, nullable=False, index=True)
    ip_address = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True)

    user = relationship("User")


class AdminProfileSettings(Base):
    """🏥 Admin profili moduli (Prompt 8) — klinika haqida umumiy
    ma'lumotlar (nomi, manzili, telefoni, litsenziyasi, ish vaqti).

    GovIntegrationSettings bilan bir xil naqsh: bitta QATOR uchun
    mo'ljallangan sozlamalar jadvali (modules/admin_profile.py'dagi
    _get_settings() get-or-create orqali oladi).

    MUHIM (talab #6): "ish o'rinlari" (positions) va "sohalar"
    (departments) alohida jadvallar EMAS — ular shu bitta jadvalning
    `positions` / `departments` JSON ustunlarida ro'yxat sifatida
    saqlanadi. Har bir element o'z "id" maydoniga ega (butun son,
    ketma-ket o'suvchi — next_position_id / next_department_id orqali
    hisoblanadi), lekin bu DB darajasidagi PK/FK EMAS, JSON ichidagi
    oddiy maydon, xolos — /positions/{id}, /departments/{id} kabi
    endpointlar shu "id" bo'yicha JSON ro'yxat ichidan qidiradi.

    Yozuvlar HAR DOIM yangi list/dict obyekti sifatida qayta
    o'rnatiladi (masalan `settings.positions = new_list`), joyida
    (in-place) o'zgartirilmaydi — aks holda SQLAlchemy JSON ustunidagi
    o'zgarishni sezmay, commit vaqtida hech narsa yozilmay qolishi
    mumkin edi (Mutable tracking ishlatilmagani uchun)."""

    __tablename__ = "admin_profile_settings"

    id = Column(Integer, primary_key=True, index=True)

    # ── Klinika ma'lumotlari ────────────────────────────────────────
    clinic_name = Column(String, nullable=True)
    address = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    license_number = Column(String, nullable=True)
    working_hours = Column(String, nullable=True)

    # ── Ish o'rinlari / Sohalar (JSON ro'yxatlar, qarang class docstring) ──
    positions = Column(JSON, nullable=False, default=list)
    departments = Column(JSON, nullable=False, default=list)
    next_position_id = Column(Integer, nullable=False, default=1)
    next_department_id = Column(Integer, nullable=False, default=1)

    # 🆕 Sozlamalar moduli (Prompt 12) — navbat/qabul oralig'i (daqiqa).
    # Klinika ma'lumotlari (nomi/manzili/telefoni/ish vaqti) allaqachon
    # shu jadvalda bo'lgani uchun (Prompt 8), ClinicSettingsUpdate ham
    # ALOHIDA jadval EMAS, shu qatorga bitta ustun qo'shib ishlaydi —
    # ikkita joyda bir xil "klinika ma'lumotlari"ni saqlash chalkashlik
    # keltirib chiqarardi.
    queue_interval_minutes = Column(Integer, nullable=False, default=15)

    updated_at = Column(DateTime, nullable=True)


class SystemSettings(Base):
    """⚙️ Sozlamalar moduli (Prompt 12) — tizim darajasidagi umumiy
    sozlamalar (vaqt zonasi, sana formati, sessiya muddati, login
    urinishlar limiti).

    AdminProfileSettings/GovIntegrationSettings bilan BIR XIL naqsh:
    bitta QATOR uchun mo'ljallangan (get-or-create, qarang
    modules/settings_module.py'dagi _get_system_settings()). Faqat
    admin PUT /settings/system orqali o'zgartira oladi.

    MUHIM: bu yerdagi session_timeout_minutes/max_login_attempts hozircha
    FAQAT saqlanadi va ko'rsatiladi — auth.py'dagi haqiqiy sessiya
    muddati/login-urinish cheklovi bular bilan hali ulanmagan (bu alohida
    keyingi bosqich, chunki auth.py konstantalari import vaqtida
    o'qiladi, DB qatoridan emas). Shu bois PUT /settings/system javobida
    va logda bu aniq eslatiladi.
    """

    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True, index=True)
    timezone = Column(String, nullable=False, default="Asia/Tashkent")
    date_format = Column(String, nullable=False, default="dd.MM.yyyy")
    session_timeout_minutes = Column(Integer, nullable=False, default=480)  # 8 soat
    max_login_attempts = Column(Integer, nullable=False, default=5)
    updated_at = Column(DateTime, nullable=True)


class GovIntegrationSettings(Base):
    """🏛️ Prompt 10 — Davlat raqamli platformasi (OneID, MyGov va h.k.)
    bilan integratsiya sozlamalari.

    AdminProfileSettings bilan bir xil naqsh: bitta QATOR (get-or-create,
    qarang modules/gov_integration.py'dagi _get_settings()).

    Xavfsizlik: api_key/api_secret HECH QACHON ochiq matnda saqlanmaydi —
    crypto_fields.EncryptedString (AES-256-GCM, `cryptography` kutubxonasi)
    orqali, patients jadvalidagi phone/pinfl bilan bir xil mexanizm.
    Bu qiymatlar faqat is_enabled=True bo'lganda va admin tomonidan
    kiritilgandan keyin tashqi so'rovlarda ishlatiladi — is_enabled=False
    bo'lsa (standart holat), modules/gov_integration.py hech qanday tashqi
    API bilan gaplashmaydi, faqat mock ma'lumot qaytaradi (talab #5).
    """

    __tablename__ = "gov_integration_settings"

    id = Column(Integer, primary_key=True, index=True)
    integration_name = Column(String(100), nullable=True)  # "OneID", "MyGov", va h.k.
    is_enabled = Column(Boolean, nullable=False, default=False)
    api_url = Column(String(500), nullable=True)
    api_key = Column(
        EncryptedString(500, aad_context="gov_integration.api_key"), nullable=True
    )
    api_secret = Column(
        EncryptedString(500, aad_context="gov_integration.api_secret"), nullable=True
    )
    organization_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, nullable=True, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, nullable=True)


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