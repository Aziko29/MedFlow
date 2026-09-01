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
    DANGEROUS_ACTION_TYPES,
    POSITION_ROLES,
    SECURITY_MESSAGE_PRIORITIES,
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

    # 🏛️ Prompt 10 — faqat o'qish uchun: bular PatientCreate/PatientUpdate'da
    # YO'Q, chunki faqat modules/gov_integration.py orqali (davlat tizimi
    # tekshiruvidan keyin) o'rnatiladi, oddiy bemor qo'shish/tahrirlash
    # formasi orqali emas.
    pinfl: Optional[str] = None
    passport_series: Optional[str] = None
    passport_number: Optional[str] = None
    is_verified: bool = False


class PatientPhotoUploadResponse(BaseModel):
    """POST /api/patients/{patient_id}/photo javobi."""
    photo_path: str


# ── Palata (statsionar davolash, Prompt 7) ──────────────────────────
class PatientRoomUpdate(BaseModel):
    """Palataga yotqizish/chiqarish uchun umumiy so'rov tanasi.

    POST /{id}/admit — room_number MAJBURIY (endpoint darajasida
    tekshiriladi), admitted_at ixtiyoriy (berilmasa serverdagi joriy
    vaqt ishlatiladi).
    POST /{id}/discharge — faqat discharged_at ishlatiladi (ixtiyoriy,
    berilmasa serverdagi joriy vaqt ishlatiladi), room_number/admitted_at
    e'tiborga olinmaydi.
    """
    room_number: Optional[str] = Field(default=None, max_length=20)
    admitted_at: Optional[datetime.datetime] = None
    discharged_at: Optional[datetime.datetime] = None

    @field_validator("room_number")
    @classmethod
    def _room_number_not_blank(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("Palata raqami bo'sh bo'lishi mumkin emas")
        return value.strip() if value else value


class PatientRoomResponse(BaseModel):
    """Bemorning joriy palata holati — /admit, /discharge, /admitted,
    /rooms javoblarida ishlatiladi."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    fullname: str
    room_number: Optional[str] = None
    admitted_at: Optional[datetime.datetime] = None
    discharged_at: Optional[datetime.datetime] = None
    is_admitted: bool


class RoomGroup(BaseModel):
    """GET /patients/rooms javobidagi bitta palata guruhi — shu palatada
    HOZIR yotgan (is_admitted=True) bemorlar ro'yxati."""
    room_number: str
    patients: List[PatientRoomResponse]


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


# Prompt 6: kassir qaytarimni yakunlaganda sabab MAJBURIY.
class PaymentRefundRequest(BaseModel):
    reason: str = Field(..., min_length=1)

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Qaytarim sababini kiriting")
        return value


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    patient_id: int
    appointment_id: int
    amount: int
    note: Optional[str] = None
    is_refund: bool
    refund_of_payment_id: Optional[int] = None
    status: str
    cancelled_by_id: Optional[int] = None
    cancelled_at: Optional[datetime.datetime] = None
    refunded_by_id: Optional[int] = None
    refunded_at: Optional[datetime.datetime] = None
    refund_reason: Optional[str] = None
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
    status: str
    cancelled_by_id: Optional[int] = None
    cancelled_at: Optional[datetime.datetime] = None
    refunded_by_id: Optional[int] = None
    refunded_at: Optional[datetime.datetime] = None
    refund_reason: Optional[str] = None
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


# Prompt 4: rol-asosida filtrlangan dashboard. Har bir maydon Optional —
# foydalanuvchi roliga tegishli bo'lmagan ko'rsatkichlar None qaytadi va
# frontend buni "—" sifatida ko'rsatadi.
class RoleDashboardSummary(BaseModel):
    role: str
    total_patients: Optional[int] = None
    today_appointments: Optional[int] = None
    waiting_patients: Optional[int] = None
    completed_appointments: Optional[int] = None
    today_revenue: Optional[str] = None
    total_debt: Optional[str] = None
    today_payments_count: Optional[int] = None
    pending_payments: Optional[int] = None
    today_refunds: Optional[str] = None
    new_patients_today: Optional[int] = None
    my_today_appointments: Optional[int] = None
    my_waiting_patients: Optional[int] = None
    my_completed_today: Optional[int] = None
    my_patients_count: Optional[int] = None
    my_lab_pending: Optional[int] = None
    my_lab_completed: Optional[int] = None
    today_lab_requests: Optional[int] = None
    staff_count: Optional[int] = None
    # Prompt 6: ikki bosqichli qaytarim ko'rsatkichlari.
    pending_refunds: Optional[int] = None  # kassir: admin bekor qilgan, hali qaytarilmagan
    cancelled_payments: Optional[int] = None  # admin: bekor qilingan to'lovlar soni
    # Prompt 7: hozir palatada yotgan bemorlar soni (faqat admin/reception).
    admitted_patients_count: Optional[int] = None
    # Prompt 9: xavfsizlik markazi — faqat admin/assistant_admin.
    unread_security_messages: Optional[int] = None
    system_errors_24h: Optional[int] = None
    failed_logins_24h: Optional[int] = None


# ── Xavfsizlik markazi (Prompt 9, modules/security_center.py) ─────────
class SecurityMessageCreate(BaseModel):
    subject: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1)
    priority: str = Field(default="medium")

    @field_validator("priority")
    @classmethod
    def _validate_priority(cls, v: str) -> str:
        if v not in SECURITY_MESSAGE_PRIORITIES:
            raise ValueError(
                f"priority quyidagilardan biri bo'lishi kerak: {', '.join(SECURITY_MESSAGE_PRIORITIES)}"
            )
        return v


class SecurityMessageOut(BaseModel):
    id: int
    from_user_id: Optional[int] = None
    from_user_name: Optional[str] = None
    subject: str
    message: str
    priority: str
    is_read: bool
    created_at: datetime.datetime


class SystemErrorCreate(BaseModel):
    """Frontend (JS xatolik ushlagichi) yoki boshqa backend qismlari
    tomonidan yuboriladi. endpoint — xatolik yuz bergan sahifa/route,
    error_message — qisqa tavsif, traceback — ixtiyoriy, faqat backend
    o'zi (main.py global handler) to'ldiradi."""

    endpoint: str = Field(..., min_length=1, max_length=255)
    error_message: str = Field(..., min_length=1)
    traceback: Optional[str] = None


class SystemErrorOut(BaseModel):
    id: int
    user_id: Optional[int] = None
    username: Optional[str] = None
    endpoint: str
    error_message: str
    traceback: Optional[str] = None
    created_at: datetime.datetime


class LoginLogOut(BaseModel):
    id: int
    user_id: Optional[int] = None
    username: str
    success: bool
    ip_address: Optional[str] = None
    created_at: datetime.datetime


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


# ── Admin profili moduli (Prompt 8) ────────────────────────────────
class AdminProfileRead(BaseModel):
    """GET /api/admin/profile javobi — klinika haqida umumiy ma'lumot."""
    model_config = ConfigDict(from_attributes=True)
    clinic_name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    license_number: Optional[str] = None
    working_hours: Optional[str] = None
    updated_at: Optional[datetime.datetime] = None


class AdminProfileUpdate(BaseModel):
    """PUT /api/admin/profile so'rov tanasi — barcha maydonlar
    ixtiyoriy (sozlamalar formasi kabi, faqat berilganlari yangilanadi
    emas — bu yerda oddiylik uchun BARCHA maydon qayta yoziladi, xuddi
    boshqa *Update schemalar kabi to'liq almashtirish tamoyilida)."""
    clinic_name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    license_number: Optional[str] = None
    working_hours: Optional[str] = None


# ── Ish o'rinlari (Positions) ──────────────────────────────────────
class PositionBase(BaseModel):
    title: str = Field(..., min_length=1)
    role: str
    specialty: Optional[str] = None
    is_occupied: bool = False
    salary_min: Optional[int] = Field(default=None, ge=0)
    salary_max: Optional[int] = Field(default=None, ge=0)
    requirements: Optional[str] = None
    department_id: Optional[int] = None

    @field_validator("role")
    @classmethod
    def _validate_position_role(cls, value: str) -> str:
        if value not in POSITION_ROLES:
            raise ValueError(
                f"Noto'g'ri rol — ruxsat etilganlar: {', '.join(POSITION_ROLES)}"
            )
        return value

    @field_validator("salary_max")
    @classmethod
    def _validate_salary_range(cls, value: Optional[int], info) -> Optional[int]:
        salary_min = info.data.get("salary_min")
        if value is not None and salary_min is not None and value < salary_min:
            raise ValueError("salary_max salary_min dan kichik bo'lishi mumkin emas")
        return value


class PositionCreate(PositionBase):
    pass


class PositionUpdate(PositionBase):
    pass


class PositionRead(PositionBase):
    id: int
    # Lavozim bo'sh bo'lgan vaqtdan beri — is_occupied=False bo'lgan
    # paytda backend tomonidan avtomatik hisoblanadi/yangilanadi
    # (qarang: modules/admin_profile.py), so'rovda kiritilmaydi.
    vacant_since: Optional[datetime.datetime] = None


# ── Sohalar (Departments) ──────────────────────────────────────────
class DepartmentBase(BaseModel):
    name: str = Field(..., min_length=1)
    head_doctor_id: Optional[int] = None
    is_active: bool = True


class DepartmentCreate(DepartmentBase):
    pass


class DepartmentUpdate(DepartmentBase):
    pass


class DepartmentRead(DepartmentBase):
    id: int
    # Hisoblanadi (saqlanmaydi): shu sohaga biriktirilgan (department_id
    # mos) va HOZIR band (is_occupied=True) lavozimlar soni.
    staff_count: int


# ── Davlat identifikatsiya tizimi integratsiyasi (Prompt 10) ─────────
# OneID / DAVLAT RO'YXATI orqali bemorni tekshirish va sozlamalarni
# boshqarish uchun schemalar. Naqsh AdminProfile bilan bir xil (bitta
# qator get-or-create), lekin api_key/api_secret HECH QACHON response'da
# ochiq qaytarilmaydi (qarang GovIntegrationSettingsResponse — bu ikki
# maydon butunlay yo'q, faqat "sozlangan/sozlanmagan" bool ko'rsatiladi).
class GovIntegrationSettingsBase(BaseModel):
    integration_name: Optional[str] = Field(default=None, max_length=100)
    is_enabled: bool = False
    api_url: Optional[str] = Field(default=None, max_length=500)
    organization_id: Optional[str] = Field(default=None, max_length=100)

    @field_validator("api_url")
    @classmethod
    def _validate_api_url(cls, value: Optional[str]) -> Optional[str]:
        # Talab #4: "SSL/TLS orqali ulanish majburiy" — http:// manzillar
        # bu yerdayoq rad etiladi, tashqi so'rov hech qachon shifrlanmagan
        # ulanishga ketmasligi kafolatlanadi.
        if value and not value.lower().startswith("https://"):
            raise ValueError("api_url faqat https:// bilan boshlanishi kerak (SSL/TLS majburiy)")
        return value


class GovIntegrationSettingsCreate(GovIntegrationSettingsBase):
    api_key: Optional[str] = None
    api_secret: Optional[str] = None


class GovIntegrationSettingsUpdate(GovIntegrationSettingsBase):
    # api_key/api_secret ixtiyoriy: admin ularni har PUT'da qayta
    # kiritmasa (None/berilmasa), oldingi shifrlangan qiymat saqlanib
    # qoladi — modules/gov_integration.py'dagi update_settings() shu
    # mantiqni amalga oshiradi (bo'sh string bilan None farqlanadi).
    api_key: Optional[str] = None
    api_secret: Optional[str] = None


class GovIntegrationSettingsResponse(GovIntegrationSettingsBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    # Xavfsizlik: haqiqiy api_key/api_secret QAYTARILMAYDI (talab #4 —
    # sozlamalar faqat admin tomonidan ko'rinadi, lekin kalitning o'zi
    # javobda ham oshkor bo'lmasligi kerak) — faqat "kiritilganmi" bayrog'i.
    api_key_configured: bool = False
    api_secret_configured: bool = False
    created_at: Optional[datetime.datetime] = None
    updated_at: Optional[datetime.datetime] = None


class PatientVerifyRequest(BaseModel):
    """POST /api/patients/verify so'rov tanasi — PINFL YOKI pasport
    seriya/raqami bilan tekshirish mumkin (ikkalasi ham berilishi
    mumkin, lekin kamida bittasi MAJBURIY — validatsiya endpoint
    darajasida, chunki bu ikki maydonning birgalikdagi holati oddiy
    field_validator bilan qulay ifodalanmaydi)."""

    passport_series: Optional[str] = Field(default=None, min_length=2, max_length=2)
    passport_number: Optional[str] = Field(default=None, min_length=7, max_length=7)
    pinfl: Optional[str] = Field(default=None, min_length=14, max_length=14)

    @field_validator("passport_series")
    @classmethod
    def _validate_series(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.upper()
        if not value.isalpha():
            raise ValueError("passport_series faqat harflardan iborat bo'lishi kerak (masalan 'AA')")
        return value

    @field_validator("passport_number")
    @classmethod
    def _validate_number(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.isdigit():
            raise ValueError("passport_number faqat raqamlardan iborat bo'lishi kerak")
        return value

    @field_validator("pinfl")
    @classmethod
    def _validate_pinfl(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.isdigit():
            raise ValueError("pinfl (JShShIR) faqat raqamlardan iborat, 14 xonali bo'lishi kerak")
        return value


class PatientVerifyResponse(BaseModel):
    """Davlat tizimidan (yoki mock rejimda) kelgan ma'lumotlar."""

    fullname: str
    birth_date: datetime.date
    address: str
    gender: Optional[str] = None
    pinfl: str
    passport_series: str
    passport_number: str
    # Javob haqiqiy davlat tizimidan keldimi yoki mock/stub rejimdanmi —
    # front-end/admin buni ko'rib, natijani ehtiyot bilan baholashi uchun.
    source: str = Field(description="'gov_api' yoki 'mock'")


class PatientRegisterWithGovRequest(BaseModel):
    """POST /api/patients/register-with-gov so'rov tanasi — avval
    PatientVerifyRequest bilan bir xil identifikatorlar orqali davlat
    tizimidan tekshiriladi (FIO/tug'ilgan sana/manzil o'sha yerdan
    olinadi), so'ng klinikaga xos qo'shimcha maydonlar shu yerda
    beriladi (davlat tizimida yo'q ma'lumotlar)."""

    passport_series: Optional[str] = Field(default=None, min_length=2, max_length=2)
    passport_number: Optional[str] = Field(default=None, min_length=7, max_length=7)
    pinfl: Optional[str] = Field(default=None, min_length=14, max_length=14)

    phone: str = Field(..., min_length=5)
    blood_type: Optional[str] = Field(default=None, max_length=3)
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None


# ── Sozlamalar moduli (Prompt 12) ──────────────────────────────────────
# GET/PUT/POST /settings/* uchun schemalar. Naqsh boshqa modullar bilan
# bir xil: *Update — PUT so'rov tanasi, *Read — javob (kerak bo'lganda).

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ProfileUpdate(BaseModel):
    """PUT /settings/profile — xodimning o'z profilidagi shaxsiy
    ma'lumotlarini yangilashi (models.User.first_name/last_name/phone/
    email — fullname/username/role bu yerda O'ZGARTIRILMAYDI, ular
    admin panelidan /api/auth/users orqali boshqariladi).

    Barcha maydonlar ixtiyoriy — faqat kiritilganlari yangilanadi
    (qisman/"partial" yangilash), None qoldirilgan maydon tegilmaydi.
    """

    first_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    phone: Optional[str] = Field(default=None, min_length=5, max_length=32)
    email: Optional[str] = Field(default=None, max_length=255)

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not _EMAIL_PATTERN.match(v):
            raise ValueError("email formati noto'g'ri")
        return v


class PasswordChange(BaseModel):
    """PUT /settings/password — ChangePasswordRequest (/api/auth/
    change-password) bilan bir xil vazifa, farqi: bu yerda front-end
    o'zi ikkinchi marta yozdirgan yangi parolni ham (confirm_password)
    serverga yuboradi, shunda tasdiqlash serverdayoq tekshiriladi (faqat
    front-end JS'ga ishonib qolinmaydi)."""

    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6)
    confirm_password: str = Field(..., min_length=6)

    @field_validator("confirm_password")
    @classmethod
    def _passwords_match(cls, v: str, info) -> str:
        new_password = info.data.get("new_password")
        if new_password is not None and v != new_password:
            raise ValueError("Yangi parol va tasdiqlash paroli mos kelmadi")
        return v


class ClinicSettingsUpdate(BaseModel):
    """PUT /settings/clinic — faqat admin. models.AdminProfileSettings
    ustunlariga mos keladi (`name` -> clinic_name, boshqalari xuddi shu
    nomda) — Prompt 8'dagi /api/admin/profile bilan BIR XIL jadval,
    faqat shu yerga queue_interval_minutes qo'shilgan."""

    name: str = Field(..., min_length=1, max_length=255)
    address: str = Field(..., min_length=1)
    phone: str = Field(..., min_length=5, max_length=32)
    working_hours: str = Field(..., min_length=1, max_length=255)
    queue_interval_minutes: int = Field(..., ge=1, le=240)


class SystemSettingsUpdate(BaseModel):
    """PUT /settings/system — faqat admin. models.SystemSettings
    (bitta-qator singleton, GovIntegrationSettings bilan bir xil naqsh)
    ustunlariga mos keladi."""

    timezone: str = Field(..., min_length=1, max_length=64)
    date_format: str = Field(..., min_length=1, max_length=32)
    session_timeout_minutes: int = Field(..., ge=5, le=1440)
    max_login_attempts: int = Field(..., ge=1, le=20)


class DangerousActionRequest(BaseModel):
    """POST /settings/dangerous-action — 2-qatlam parol bilan
    himoyalangan xavfli amallar (bazani tozalash, barcha bemorlarni
    o'chirish, tizimni qayta ishga tushirish, sessiyalarni tiklash).

    confirmation_password — foydalanuvchining o'z login paroli EMAS,
    .env'dagi CLINICFLOW_ADMIN_ACTIONS_PASSWORD bilan solishtiriladigan
    ALOHIDA, faqat shu amallar uchun mo'ljallangan 2-qatlam parol
    (qarang: modules/settings_module.py perform_dangerous_action)."""

    action_type: str
    confirmation_password: str = Field(..., min_length=1)

    @field_validator("action_type")
    @classmethod
    def _validate_action_type(cls, v: str) -> str:
        if v not in DANGEROUS_ACTION_TYPES:
            raise ValueError(
                f"Noto'g'ri action_type — ruxsat etilganlar: {', '.join(DANGEROUS_ACTION_TYPES)}"
            )
        return v


class ProfileRead(BaseModel):
    """GET javoblarida / sahifa kontekstida ishlatiladigan profil
    ko'rinishi."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    fullname: str
    role: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None


class ClinicSettingsRead(BaseModel):
    """Router bu yerga models.AdminProfileSettings'dan qo'lda mos
    keladigan maydonlarni o'tkazadi (clinic_name -> name) — pydantic
    alias/from_attributes orqali emas, chunki ustun nomi (clinic_name)
    va tashqi maydon nomi (name) har xil bo'lgani uchun qo'lda mapping
    aniqroq va xatoga kamroq moyil."""

    name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    working_hours: Optional[str] = None
    queue_interval_minutes: int


class SystemSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    timezone: str
    date_format: str
    session_timeout_minutes: int
    max_login_attempts: int


class DangerousActionResult(BaseModel):
    """POST /settings/dangerous-action javobi."""

    status: str = "ok"
    action_type: str
    message: str
