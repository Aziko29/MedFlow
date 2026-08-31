# schemas.py
"""
Pydantic v2 schemas for request/response validation.
Field names mirror models.py exactly so that
`Model(**schema.model_dump())` (or an explicit field-by-field assignment)
always works without silent remapping bugs.
"""
import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

import re

from models import (
    ALLERGY_SEVERITIES,
    APPOINTMENT_STATUSES,
    CHRONIC_CONDITION_STATUSES,
    SELF_PASSWORD_CHANGE_LIMIT,
    USER_ROLES,
)

_USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_]+$")


# ── Auth / Users ─────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class CurrentUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    fullname: str
    role: str
    doctor_id: Optional[int] = None


class ChangePasswordResult(BaseModel):
    """POST /api/auth/change-password javobi — xodim "Sozlamalar"
    sahifasida qancha marta o'zi parolni almashtirganini va yana nechta
    imkoni qolganini ko'rishi uchun (SELF_PASSWORD_CHANGE_LIMIT, 3-band)."""
    status: str = "ok"
    message: str
    changes_used: int
    changes_remaining: int
    limit: int = SELF_PASSWORD_CHANGE_LIMIT


class ChangePasswordRequest(BaseModel):
    """JSON body for POST /api/auth/change-password.

    Previously old_password/new_password were plain function parameters
    with no Pydantic schema, so FastAPI treated them as URL QUERY
    parameters instead of a JSON body — passwords ended up in the URL,
    which means server access logs and browser history. A real schema
    forces them into the request body like every other write endpoint.
    """
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6)


# ── Users CRUD (admin panel) ─────────────────────────────────────────
class UserCreate(BaseModel):
    """POST /api/auth/users — admin panelidan yangi xodim qo'shish.

    Parol shu yerda kiritilmaydi: admin-reset-password bilan bir xil
    tamoyil — server tasodifiy vaqtinchalik parol generatsiya qiladi va
    uni bir marta javobda qaytaradi (schemas.UserCreateResponse), xodim
    keyin o'zi change-password orqali almashtiradi.
    """

    username: str = Field(..., min_length=3, max_length=64)
    fullname: str = Field(..., min_length=1)
    role: str
    doctor_id: Optional[int] = None

    @field_validator("username")
    @classmethod
    def _validate_username(cls, v: str) -> str:
        if not _USERNAME_PATTERN.match(v):
            raise ValueError(
                "Login faqat lotin harflari, raqamlar va pastki chiziqchadan iborat bo'lishi kerak"
            )
        return v

    @field_validator("role")
    @classmethod
    def _validate_role(cls, v: str) -> str:
        if v not in USER_ROLES:
            raise ValueError(f"Noto'g'ri rol — ruxsat etilganlar: {', '.join(USER_ROLES)}")
        return v


class UserCreateResponse(BaseModel):
    user: CurrentUser
    temporary_password: str
    note: str = "Bu vaqtinchalik parol faqat bir marta ko'rsatiladi — uni xodimga xavfsiz yo'l bilan yetkazing."


class UserUpdate(BaseModel):
    """PUT /api/auth/users/{id} — faqat fullname/role/doctor_id.

    Parol bu yerda hech qachon o'zgartirilmaydi (parol faqat
    admin-reset-password yoki o'zining change-password orqali).
    """

    fullname: str = Field(..., min_length=1)
    role: str
    doctor_id: Optional[int] = None

    @field_validator("role")
    @classmethod
    def _validate_role(cls, v: str) -> str:
        if v not in USER_ROLES:
            raise ValueError(f"Noto'g'ri rol — ruxsat etilganlar: {', '.join(USER_ROLES)}")
        return v


# ── Doctor ────────────────────────────────────────────────────────────
class DoctorBase(BaseModel):
    fullname: str = Field(..., min_length=1)
    specialty: str = Field(..., min_length=1)
    room: Optional[str] = None
    consultation_price: int = Field(default=0, ge=0)
    working_hours: Optional[str] = None
    license_number: Optional[str] = None
    experience_years: Optional[int] = Field(default=None, ge=0, le=80)
    qualification_category: Optional[str] = None


class DoctorCreate(DoctorBase):
    pass


class DoctorUpdate(DoctorBase):
    is_active: bool = True


class DoctorRead(DoctorBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    is_active: bool
    # photo_path DoctorCreate/DoctorUpdate'da yo'q — patients.photo_path
    # bilan bir xil pattern: faqat POST /{doctor_id}/photo orqali (fayl
    # yuklab) o'rnatiladi, oddiy shifokor qo'shish/tahrirlash formasi orqali
    # emas.
    photo_path: Optional[str] = None


class DoctorPhotoUploadResponse(BaseModel):
    """POST /api/doctors/{doctor_id}/photo javobi."""
    photo_path: str


# ── Patient ───────────────────────────────────────────────────────────
class PatientBase(BaseModel):
    fullname: str = Field(..., min_length=1)
    phone: str = Field(..., min_length=5)
    gender: Optional[str] = None
    birth_date: Optional[datetime.date] = None
    address: Optional[str] = None
    medical_notes: Optional[str] = None
    blood_type: Optional[str] = Field(default=None, max_length=3)
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None

    @field_validator("gender")
    @classmethod
    def validate_gender(cls, value: Optional[str]) -> Optional[str]:
        if value in (None, ""):
            return None
        if value not in {"M", "F"}:
            raise ValueError("gender must be 'M' or 'F'")
        return value


class PatientCreate(PatientBase):
    pass


class PatientUpdate(PatientBase):
    pass


class PatientRead(PatientBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime.datetime
    # photo_path PatientCreate/PatientUpdate'da yo'q — u faqat
    # POST /{patient_id}/photo orqali (fayl yuklab) o'rnatiladi, oddiy
    # bemor qo'shish/tahrirlash formasi orqali emas.
    photo_path: Optional[str] = None


class PatientPhotoUploadResponse(BaseModel):
    """POST /api/patients/{patient_id}/photo javobi."""
    photo_path: str


class PatientFinancials(BaseModel):
    """Bemorning umumiy moliyaviy holati — HISOBLANADI, saqlanmaydi."""
    total_charged: int
    total_paid: int
    total_debt: int


class PatientDetail(PatientRead):
    financials: PatientFinancials


# ── Allergy ───────────────────────────────────────────────────────────
class AllergyBase(BaseModel):
    substance: str = Field(..., min_length=1)
    reaction: Optional[str] = None
    severity: Optional[str] = None

    @field_validator("severity")
    @classmethod
    def _validate_severity(cls, value: Optional[str]) -> Optional[str]:
        if value in (None, ""):
            return None
        if value not in ALLERGY_SEVERITIES:
            raise ValueError(
                f"severity {', '.join(ALLERGY_SEVERITIES)} dan biri bo'lishi kerak"
            )
        return value


class AllergyCreate(AllergyBase):
    pass


class AllergyRead(AllergyBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    patient_id: int
    noted_at: datetime.datetime


# ── ChronicCondition ──────────────────────────────────────────────────
class ChronicConditionBase(BaseModel):
    name: str = Field(..., min_length=1)
    diagnosed_date: Optional[datetime.date] = None
    status: str = "faol"
    notes: Optional[str] = None

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str) -> str:
        if value not in CHRONIC_CONDITION_STATUSES:
            raise ValueError(
                f"status {', '.join(CHRONIC_CONDITION_STATUSES)} dan biri bo'lishi kerak"
            )
        return value


class ChronicConditionCreate(ChronicConditionBase):
    pass


class ChronicConditionRead(ChronicConditionBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    patient_id: int


# ── TreatmentHistory ─────────────────────────────────────────────────
class TreatmentHistoryBase(BaseModel):
    appointment_id: Optional[int] = None
    doctor_id: Optional[int] = None
    diagnosis: Optional[str] = None
    treatment: Optional[str] = None
    date: Optional[datetime.date] = None


class TreatmentHistoryCreate(TreatmentHistoryBase):
    pass


class TreatmentHistoryRead(TreatmentHistoryBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    patient_id: int
    date: datetime.date


# ── Appointment ───────────────────────────────────────────────────────
class AppointmentCreate(BaseModel):
    patient_id: int
    doctor_id: int
    scheduled_time: datetime.datetime


class AppointmentStatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in APPOINTMENT_STATUSES:
            raise ValueError(f"status must be one of {sorted(APPOINTMENT_STATUSES)}")
        return value


class AppointmentCancel(BaseModel):
    reason: Optional[str] = None


class AppointmentReschedule(BaseModel):
    scheduled_time: datetime.datetime


class AppointmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    patient_id: int
    doctor_id: int
    scheduled_time: datetime.datetime
    status: str
    price: int
    cancel_reason: Optional[str] = None


class AppointmentDetail(AppointmentRead):
    patient_name: str
    doctor_name: str
    paid_amount: int
    debt: int


class AppointmentQueueItem(BaseModel):
    """Dashboard's live-queue row shape — matches dashboard.html exactly."""
    id: str
    patient_id: int
    doctor_id: int
    patient_name: str
    doctor_name: str
    time: str
    status: str
    debt: int


# ── Payment ───────────────────────────────────────────────────────────
class PaymentCreate(BaseModel):
    appointment_id: int
    amount: int = Field(..., gt=0)
    note: Optional[str] = None


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    patient_id: int
    appointment_id: int
    amount: int
    note: Optional[str] = None
    is_refund: bool
    refund_of_payment_id: Optional[int] = None
    created_at: datetime.datetime


class PaymentListItem(BaseModel):
    """To'lovlar jadvali qatori — bemor va qabul ma'lumotlari bilan birga."""
    id: int
    patient_id: int
    patient_name: str
    appointment_id: int
    amount: int
    note: Optional[str] = None
    is_refund: bool
    refund_of_payment_id: Optional[int] = None
    # Hisoblab chiqiladi: shu (asl) to'lov uchun allaqachon qaytarim
    # yozuvi bormi — frontend shu bo'yicha "Qaytarish" tugmasini
    # yashiradi (bug fix: eski versiyada tugma har doim ko'rinar edi).
    is_refunded: bool = False
    created_at: datetime.datetime


# ── Dashboard ─────────────────────────────────────────────────────────
class DashboardSummary(BaseModel):
    total_patients: int
    today_appointments: int
    today_waiting: int
    today_completed: int
    today_revenue: str
    total_revenue: str
    total_debt: str


# ── Reports (admin, modules/reports.py) — 1-bosqich: umumiy hajm ──────
class ReportOverview(BaseModel):
    total_patients: int
    total_doctors_active: int
    total_doctors_inactive: int
    period_appointments: int


# ── Reports — 2-bosqich: status bo'yicha taqsimot ──────────────────────
class StatusCount(BaseModel):
    status: str
    label: str
    count: int
    percentage: float


class ReportStatusBreakdown(BaseModel):
    items: List[StatusCount]
    total: int


# ── Reports — 3-bosqich: shifokor samaradorligi ────────────────────────
class DoctorPerformanceRow(BaseModel):
    doctor_id: int
    doctor_name: str
    specialty: str
    total: int
    completed: int
    cancelled: int
    delayed: int
    no_show: int
    revenue: int


# ── Reports — 4-bosqich: bekor qilish sabablari ────────────────────────
class CancelReasonRow(BaseModel):
    reason: str
    count: int
    percentage: float


# ── Reports — 5-bosqich: band vaqt tahlili ─────────────────────────────
class HourlyLoadRow(BaseModel):
    hour: int
    count: int


class WeekdayLoadRow(BaseModel):
    weekday: int
    weekday_label: str
    count: int


# ── Reports — 6-bosqich: yangi/qaytgan bemorlar nisbati ────────────────
class PatientRetention(BaseModel):
    new_patients: int
    returning_patients: int
    new_percentage: float
    returning_percentage: float


# ── Reports — 7-bosqich: oylik davr taqqoslash ─────────────────────────
class PeriodTrendRow(BaseModel):
    period_label: str
    appointments: int
    completed: int
    cancelled: int
    revenue: int


# ── Audit Log (4-band) ───────────────────────────────────────────────
class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: Optional[int] = None
    username: str
    action: str
    entity_type: str
    entity_id: Optional[int] = None
    details: Optional[str] = None
    created_at: datetime.datetime


class AuditLogPage(BaseModel):
    items: list[AuditLogRead]
    total: int
    page: int
    page_size: int
    total_pages: int


# ── Zaxira nusxa / Backup (admin) ──────────────────────────────────────
class BackupDestination(BaseModel):
    type: str  # "folder" | "network" | "external"
    path: str = ""
    enabled: bool = False


class BackupSettingsUpdate(BaseModel):
    destinations: list[BackupDestination] = Field(default_factory=list)


class BackupPathCheck(BaseModel):
    path: str = ""


# ── Admin parol tiklash (6-band, UX) ─────────────────────────────────
class AdminPasswordResetResponse(BaseModel):
    username: str
    temporary_password: str
    note: str = "Bu vaqtinchalik parol faqat bir marta ko'rsatiladi — uni xodimga xavfsiz yo'l bilan yetkazing."


# ── Bemor portali (FAZA 2) — telefon + SMS-kod bilan login ────────────
class PatientPortalCodeRequest(BaseModel):
    phone: str = Field(..., min_length=5)


class PatientPortalCodeResponse(BaseModel):
    # Har doim bir xil neytral xabar — telefon raqami tizimda mavjud
    # yoki yo'qligini oshkor qilmaslik uchun (enumeration himoyasi).
    message: str = "Agar bunday raqam ro'yxatda mavjud bo'lsa, tasdiqlash kodi SMS orqali yuborildi."


class PatientPortalVerifyRequest(BaseModel):
    phone: str = Field(..., min_length=5)
    code: str = Field(..., min_length=4, max_length=8)
