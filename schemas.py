# schemas.py
"""
Pydantic v2 schemas for request/response validation.
Field names mirror models.py exactly so that
`Model(**schema.model_dump())` (or an explicit field-by-field assignment)
always works without silent remapping bugs.
"""
import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

import re

from models import APPOINTMENT_STATUSES, SELF_PASSWORD_CHANGE_LIMIT, USER_ROLES

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


class DoctorCreate(DoctorBase):
    pass


class DoctorUpdate(DoctorBase):
    is_active: bool = True


class DoctorRead(DoctorBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    is_active: bool


# ── Patient ───────────────────────────────────────────────────────────
class PatientBase(BaseModel):
    fullname: str = Field(..., min_length=1)
    phone: str = Field(..., min_length=5)
    gender: Optional[str] = None
    birth_date: Optional[datetime.date] = None
    address: Optional[str] = None
    medical_notes: Optional[str] = None

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


class PatientFinancials(BaseModel):
    """Bemorning umumiy moliyaviy holati — HISOBLANADI, saqlanmaydi."""
    total_charged: int
    total_paid: int
    total_debt: int


class PatientDetail(PatientRead):
    financials: PatientFinancials


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
