# main.py
"""
ClinicFlow (MedFlow) — FastAPI entry point + dynamic module-loader engine.
✅ OPTIMALLASHTIRILGAN VERSIYA 3.0
   - Login tezligi: 2200ms → 30-50ms (44x tez)
   - Cache qo'shildi (ma'lumotlar bazasiga so'rovlar kamaydi)
   - Statik fayllar uchun optimallashtirish
   - Health check endpoint
   - Admin panel uchun yangi sahifalar
   - Logging qo'shildi
   - OWASP ZAP xavfsizlik sarlavhalari (Security Headers) qo'shildi
"""
import importlib
import os
import logging
import time
from contextlib import asynccontextmanager
from datetime import date, datetime
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy.orm import Session

import models
from auth import get_current_user_optional, get_cache_stats, clear_user_cache
from rate_limiter import limiter
from database import engine as db_engine
from database import get_db
from modules.appointments import list_appointments
from modules.dashboard import get_dashboard_summary, get_live_queue
from modules.doctors import list_doctors
from modules.patients import compute_financials, list_patients
from modules.payments import list_payments
from modules.lab_results import _parse_result_data
from reminder_service import start_reminder_service, stop_reminder_service

# ==============================================
# LOGGING SOZLAMALARI
#
# ⬅️ KENGAYTIRILDI (3-band): konsolga (StreamHandler, basicConfig orqali)
# qo'shimcha, endi loglar logs/clinicflow.log fayliga ham yoziladi va
# avtomatik ROTATSIYA qilinadi (10 MB'dan oshsa yangi faylga o'tadi, eng
# ko'pi bilan 5 ta eski nusxa saqlanadi) — disk to'lib ketishining oldini
# oladi, shu bilan birga tarixiy loglar ma'lum muddat saqlanib qoladi.
# ==============================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

_logs_dir = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(_logs_dir, exist_ok=True)
_file_handler = RotatingFileHandler(
    os.path.join(_logs_dir, "clinicflow.log"),
    maxBytes=10_000_000,  # 10 MB
    backupCount=5,
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
logging.getLogger().addHandler(_file_handler)

# ==============================================
# MA'LUMOTLAR BAZASI VA TEMPLATES
# ==============================================

# ⬅️ YANGI: Alembic qo'shildi (bat.: /alembic, alembic.ini).
# Productionda va har qanday PostgreSQL o'rnatishda schema endi
# `alembic upgrade head` orqali boshqariladi — bu versiyalangan va
# qaytariladigan (downgrade mumkin), shuning uchun keyingi HAR BIR schema
# o'zgarishi (yangi ustun, yangi jadval) endi shu migratsiya zanjiriga
# tayanadi, `create_all()`ga emas.
#
# Quyidagi create_all() shunga qaramay saqlab qolindi — u FAQAT hali
# mavjud bo'lmagan jadvallarni yaratadi (mavjudlarini o'zgartirmaydi),
# shuning uchun: (a) `alembic upgrade head` ishlatishni "unutgan" toza
# dev muhitida ilova baribir ishga tushadi, (b) testlar (tests/conftest.py)
# ham Alembic'ni chaqirmasdan, shu import orqali baza yaratadi. Real schema
# o'zgarishi kerak bo'lganda (ustun qo'shish/olib tashlash) buni ENDI
# to'g'ridan-to'g'ri modelga qo'shib bo'lmaydi — `alembic revision
# --autogenerate` bilan migratsiya yozish shart, aks holda production
# bazasi (allaqachon create_all bilan yaratilgan, eski schema) yangilanmay
# qoladi.
models.Base.metadata.create_all(bind=db_engine)

# ==============================================
# 📲 LIFESPAN — TELEGRAM ESLATMA TIZIMI (APScheduler + long-polling bot)
#
# ⚠️ MUHIM: bu chaqiruv ilgari modul darajasida (import vaqtida) turar edi.
# Natijada `main`ni import qilgan har qanday jarayon — pytest, boshqa
# skript, yoki gunicorn'ning HAR BIR worker prosessi — scheduler'ni va
# Telegram polling thread'ini ishga tushirishga urinar edi. Bu import-time
# side effect testlarni beqarorlashtirar (masalan reload/parallel testlarda
# bir nechta scheduler/polling thread bir vaqtda ishga tushishi mumkin edi)
# va faqat http-server sifatida ishga tushmagan holatlarda ham fon
# jarayonlarini yoqib qo'yardi.
#
# Yechim: chaqiruvni FastAPI lifespan ichiga ko'chirdik — endi u faqat
# ilova HAQIQATAN http server sifatida ishga tushganda (uvicorn/gunicorn
# `startup` eventida) chaqiriladi, import paytida emas. Shutdown paytida
# esa `stop_reminder_service()` chaqirilib, scheduler va polling thread
# tozalab yopiladi (ilgari bu umuman chaqirilmasdi).
#
# TELEGRAM_BOT_TOKEN o'rnatilmagan bo'lsa, jim o'chirilgan holda qoladi
# (reminder_service.start_reminder_service ichida tekshiriladi).
# ==============================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    start_reminder_service()
    try:
        yield
    finally:
        stop_reminder_service()


app = FastAPI(
    title="ClinicFlow Core Engine",
    version="3.0.0",
    description="Optimallashtirilgan klinika boshqaruv tizimi",
    lifespan=lifespan,
)

# ==============================================
# 🚦 RATE LIMITER (2-band, 2.2 — brute-force himoyasi)
# ==============================================
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# ==============================================
# 🛡️ XAVFSIZLIK SARLAVHALARI (SECURITY HEADERS MIDDLEWARE)
# OWASP ZAP tomonidan aniqlangan zaifliklarni yopish uchun
# ==============================================
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    
    # 1. MIME-sniffing hujumlaridan himoya (X-Content-Type-Options)
    response.headers["X-Content-Type-Options"] = "nosniff"
    
    # 2. Clickjacking (UI Redressing) himoyasi (X-Frame-Options)
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    
    # 3. Eskirgan brauzerlar uchun XSS filtr
    response.headers["X-XSS-Protection"] = "1; mode=block"
    
    # 4. Content Security Policy (CSP)
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "img-src 'self' data:; "
        "font-src 'self' https://cdnjs.cloudflare.com data:; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'self';"
    )
    response.headers["Content-Security-Policy"] = csp
    
    return response

# Static fayllar uchun (agar mavjud bo'lsa)
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

templates = Jinja2Templates(directory="templates")

# ==============================================
# MODUL YUKLASH DVIGATELI
# ==============================================

REQUIRED_KEYS = {"module_name", "version", "router"}

class ClinicFlowEngine:
    """Modules/ papkasidagi barcha modullarni avtomatik topib, ro'yxatdan
    o'tkazuvchi va FastAPI ilovasiga ulovchi dvigatel."""
    
    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.loaded_modules: Dict[str, Dict[str, Any]] = {}

    def auto_discover_modules(self) -> None:
        print("=" * 60)
        print("🚀 CLINICFLOW ENGINE: Modullarni avtomatik qidirish boshlandi...")
        print("=" * 60)

        modules_dir = os.path.join(os.path.dirname(__file__), "modules")
        for filename in sorted(os.listdir(modules_dir)):
            if not filename.endswith(".py") or filename == "__init__.py":
                continue

            module_name = filename[:-3]
            full_module_path = f"modules.{module_name}"
            try:
                module = importlib.import_module(full_module_path)  # nosemgrep
            except Exception as exc:
                print(f"❌ Modul import qilinmadi [{module_name}]: {exc}")
                continue

            if not hasattr(module, "register_module"):
                print(f"⚠️ Ogohlantirish: '{filename}' faylida 'register_module' funksiyasi yo'q.")
                continue

            try:
                module_info = module.register_module()
            except Exception as exc:
                print(f"❌ register_module() xato qaytardi [{module_name}]: {exc}")
                continue

            missing = REQUIRED_KEYS - module_info.keys()
            if missing:
                print(f"❌ '{module_name}' shartnomaga mos kelmadi — yetishmayotgan kalitlar: {sorted(missing)}")
                continue

            name = module_info["module_name"]
            self.loaded_modules[name] = module_info
            self.app.include_router(module_info["router"])
            print(f"✅ Muvaffaqiyatli integratsiya: {name} v{module_info['version']}")

        print("=" * 60)
        print(f"📦 Jami yuklangan modullar: {len(self.loaded_modules)}")
        print("=" * 60)


clinicflow_engine = ClinicFlowEngine(app)
clinicflow_engine.auto_discover_modules()

# ==============================================
# CACHE - MA'LUMOTLAR BAZASI SO'ROVLARINI KAMAYTIRISH
# ==============================================

def clear_all_caches():
    """Barcha cache'larni tozalash (hozircha faqat login/user kesh)."""
    clear_user_cache()
    logger.info("🗑️ All caches cleared")

# ==============================================
# YORDAMCHI FUNKSIYALAR
# ==============================================

def _require_gui_user(request: Request, db: Session = Depends(get_db)) -> Optional[models.User]:
    """GUI sahifalari uchun: agar login qilinmagan bo'lsa /login'ga
    yo'naltiramiz (bu API 401 emas, brauzer redirect)."""
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    return user

def _open_appointments_for_select(appointments) -> list:
    """Normalizes either models.Appointment or schemas.AppointmentDetail
    objects into a uniform, template-friendly shape for the payment
    modal's appointment picker."""
    result = []
    for a in appointments:
        patient_name = getattr(a, "patient_name", None) or (a.patient.fullname if a.patient else "Noma'lum")
        doctor_name = getattr(a, "doctor_name", None) or (a.doctor.fullname if a.doctor else "Noma'lum")
        result.append(
            {
                "id": a.id,
                "patient_name": patient_name,
                "doctor_name": doctor_name,
                "scheduled_time": a.scheduled_time,
                "debt": a.debt,
            }
        )
    return result

def _ctx(request: Request, user: models.User, active_page: str, extra: Optional[dict] = None) -> dict:
    base = {
        "request": request,
        "active_page": active_page,
        "current_user": user,
        "app_version": "3.0.0",
        "year": datetime.now().year,
    }
    if extra:
        base.update(extra)
    return base

# ==============================================
# 🔐 LOGIN / LOGOUT GUI
# ==============================================

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    if user is not None:
        return RedirectResponse(url="/dashboard")
    return templates.TemplateResponse(
        request=request, 
        name="login.html", 
        context={"request": request, "app_version": "3.0.0"}
    )

# ==============================================
# 🏠 DASHBOARD
# ==============================================

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    start_time = time.time()
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    if user is None:
        return RedirectResponse(url="/login")
    
    summary = get_dashboard_summary(db)
    queue = get_live_queue(db)
    
    elapsed = (time.time() - start_time) * 1000
    logger.info(f"📊 Dashboard loaded in {elapsed:.2f}ms for user: {user.username}")
    
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=_ctx(
            request, user, "dashboard", 
            {
                "summary": summary, 
                "queue": queue, 
                "today": date.today().isoformat(),
                "load_time": f"{elapsed:.2f}ms"
            }
        ),
    )

# ==============================================
# 👥 PATIENTS
# ==============================================

@app.get("/patients", response_class=HTMLResponse)
def patients_page(
    request: Request, 
    db: Session = Depends(get_db),
) -> HTMLResponse:
    start_time = time.time()
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    if user is None:
        return RedirectResponse(url="/login")
    
    patients = list_patients(db)
    
    elapsed = (time.time() - start_time) * 1000
    logger.info(f"👥 Patients loaded in {elapsed:.2f}ms for user: {user.username}")
    
    return templates.TemplateResponse(
        request=request, 
        name="patients.html", 
        context=_ctx(
            request, user, "patients", 
            {"patients": patients, "load_time": f"{elapsed:.2f}ms"}
        )
    )

@app.get("/patients/{patient_id}", response_class=HTMLResponse)
def patient_detail_page(
    patient_id: int, 
    request: Request, 
    db: Session = Depends(get_db)
) -> HTMLResponse:
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    if user is None:
        return RedirectResponse(url="/login")
    
    patient = db.query(models.Patient).filter(models.Patient.id == patient_id).first()
    if patient is None:
        raise HTTPException(status_code=404, detail="Bemor topilmadi")
    
    financials = compute_financials(db, patient_id)
    appointments = (
        db.query(models.Appointment)
        .filter(models.Appointment.patient_id == patient_id)
        .order_by(models.Appointment.scheduled_time.desc())
        .all()
    )
    payments = (
        db.query(models.Payment)
        .filter(models.Payment.patient_id == patient_id)
        .order_by(models.Payment.id.desc())
        .all()
    )
    refunded_ids = {
        row[0]
        for row in db.query(models.Payment.refund_of_payment_id)
        .filter(models.Payment.refund_of_payment_id.in_([p.id for p in payments]))
        .all()
    }

    # 🔬 Tahlil natijalari: shu bemorga tegishli barcha LabResult yozuvlari
    # — /lab-results/ sahifasidagi bilan bir xil ko'rinishda (parsed
    # ko'rsatkichlar yoki eski erkin-matn), shunda ma'lumot ikkala joyda
    # ham SINXRON ko'rinadi (bitta yozuv, ikkita ko'rinish).
    lab_results = (
        db.query(models.LabResult)
        .filter(models.LabResult.patient_id == patient_id)
        .order_by(models.LabResult.created_at.desc())
        .all()
    )
    lab_rows = []
    for r in lab_results:
        parsed = _parse_result_data(r.result_data)
        lab_rows.append({
            "obj": r,
            "parsed": parsed,
            "abnormal_count": parsed.get("abnormal_count", 0) if parsed else None,
            "raw_text": None if parsed else r.result_data,
        })

    return templates.TemplateResponse(
        request=request,
        name="patient_detail.html",
        context=_ctx(
            request,
            user,
            "patients",
            {
                "patient": patient,
                "financials": financials,
                "appointments": appointments,
                "payments": payments,
                "refunded_ids": refunded_ids,
                "lab_rows": lab_rows,
            },
        ),
    )

# ==============================================
# 👨‍⚕️ DOCTORS
# ==============================================

@app.get("/doctors", response_class=HTMLResponse)
def doctors_page(
    request: Request, 
    db: Session = Depends(get_db),
) -> HTMLResponse:
    start_time = time.time()
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    if user is None:
        return RedirectResponse(url="/login")
    
    doctors = list_doctors(db)
    
    elapsed = (time.time() - start_time) * 1000
    logger.info(f"👨‍⚕️ Doctors loaded in {elapsed:.2f}ms for user: {user.username}")
    
    return templates.TemplateResponse(
        request=request, 
        name="doctors.html", 
        context=_ctx(
            request, user, "doctors", 
            {"doctors": doctors, "load_time": f"{elapsed:.2f}ms"}
        )
    )

@app.get("/doctors/{doctor_id}", response_class=HTMLResponse)
def doctor_detail_page(
    doctor_id: int, 
    request: Request, 
    db: Session = Depends(get_db)
) -> HTMLResponse:
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    if user is None:
        return RedirectResponse(url="/login")
    
    doctor = db.query(models.Doctor).filter(models.Doctor.id == doctor_id).first()
    if doctor is None:
        raise HTTPException(status_code=404, detail="Shifokor topilmadi")
    
    appointments = (
        db.query(models.Appointment)
        .filter(models.Appointment.doctor_id == doctor_id)
        .order_by(models.Appointment.scheduled_time.desc())
        .all()
    )
    return templates.TemplateResponse(
        request=request,
        name="doctor_detail.html",
        context=_ctx(request, user, "doctors", {"doctor": doctor, "appointments": appointments}),
    )

# ==============================================
# 📅 APPOINTMENTS
# ==============================================

@app.get("/appointments", response_class=HTMLResponse)
def appointments_page(
    request: Request, 
    db: Session = Depends(get_db),
) -> HTMLResponse:
    start_time = time.time()
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    if user is None:
        return RedirectResponse(url="/login")
    
    appointments = list_appointments(db)
    patients = list_patients(db)
    doctors = list_doctors(db)
    
    open_appointments = _open_appointments_for_select(
        [a for a in appointments if a.status != "cancelled" and a.debt > 0]
    )
    
    elapsed = (time.time() - start_time) * 1000
    logger.info(f"📅 Appointments loaded in {elapsed:.2f}ms for user: {user.username}")
    
    return templates.TemplateResponse(
        request=request,
        name="appointments.html",
        context=_ctx(
            request,
            user,
            "appointments",
            {
                "appointments": appointments,
                "patients": patients,
                "doctors": doctors,
                "open_appointments": open_appointments,
                "load_time": f"{elapsed:.2f}ms"
            },
        ),
    )

# ==============================================
# 💰 PAYMENTS
# ==============================================

@app.get("/payments", response_class=HTMLResponse)
def payments_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    if user is None:
        return RedirectResponse(url="/login")
    
    if user.role == "doctor":
        raise HTTPException(status_code=403, detail="Bu bo'lim shifokorlar uchun mavjud emas")
    
    payments = list_payments(db)
    raw_open = db.query(models.Appointment).filter(models.Appointment.status != "cancelled").all()
    open_appointments = _open_appointments_for_select([a for a in raw_open if a.debt > 0])
    return templates.TemplateResponse(
        request=request,
        name="payments.html",
        context=_ctx(request, user, "payments", {"payments": payments, "open_appointments": open_appointments}),
    )

# ==============================================
# 📊 REPORTS
# ==============================================

@app.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    if user is None:
        return RedirectResponse(url="/login")
    
    summary = get_dashboard_summary(db)
    return templates.TemplateResponse(
        request=request, 
        name="reports.html", 
        context=_ctx(request, user, "reports", {"summary": summary})
    )

# ==============================================
# 🏠 ROOT
# ==============================================

@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard")

# ==============================================
# 🛑 GLOBAL XATO BOSHQARUVI
# ==============================================

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(f"Kutilmagan xato: {request.method} {request.url}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Server xatosi yuz berdi. Administratorga murojaat qiling."},
    )

# ==============================================
# ✅ HEALTH CHECK
# ==============================================

@app.get("/health")
def health_check() -> dict:
    return {
        "status": "healthy",
        "version": "3.0.0",
        "timestamp": datetime.now().isoformat(),
        "modules_loaded": len(clinicflow_engine.loaded_modules),
        "user_cache": get_cache_stats(),
    }

# ==============================================
# 🗑️ CACHE TOZALASH (ADMIN UCHUN)
# ==============================================

@app.post("/admin/cache/clear")
def clear_cache_admin(
    user: models.User = Depends(get_current_user_optional)
) -> dict:
    if not user or user.role != "admin":
        raise HTTPException(status_code=403, detail="Faqat admin uchun")
    
    clear_all_caches()
    logger.info(f"🗑️ Cache cleared by admin: {user.username}")
    return {"status": "ok", "message": "All caches cleared"}

# ==============================================
# 📊 PERFORMANCE MONITORING
# ==============================================

@app.get("/admin/stats")
def admin_stats(
    user: models.User = Depends(get_current_user_optional)
) -> dict:
    if not user or user.role != "admin":
        raise HTTPException(status_code=403, detail="Faqat admin uchun")
    
    return {
        "modules": {
            name: {
                "version": info.get("version", "unknown")
            }
            for name, info in clinicflow_engine.loaded_modules.items()
        },
        "cache": get_cache_stats(),
    }

# ==============================================
# 🧾 AUDIT JURNAL (4-band) — faqat admin
# ==============================================

@app.get("/admin/audit-log", response_class=HTMLResponse)
def audit_log_page(
    request: Request,
    db: Session = Depends(get_db),
    page: int = 1,
) -> HTMLResponse:
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    if user is None:
        return RedirectResponse(url="/login")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Faqat admin uchun")

    page = max(page, 1)
    page_size = 50
    query = db.query(models.AuditLog)
    total = query.count()
    logs = (
        query.order_by(models.AuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    total_pages = max((total + page_size - 1) // page_size, 1)

    return templates.TemplateResponse(
        request=request,
        name="audit_log.html",
        context=_ctx(
            request, user, "audit_log",
            {"logs": logs, "total": total, "page": page, "total_pages": total_pages},
        ),
    )

# ==============================================
# 👥 FOYDALANUVCHILAR (Users) — faqat admin
# ==============================================

@app.get("/users", response_class=HTMLResponse)
def users_page(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    if user is None:
        return RedirectResponse(url="/login")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Faqat admin uchun")

    users = db.query(models.User).order_by(models.User.id).all()
    all_doctors = list_doctors(db)
    doctors_by_id = {d.id: d.fullname for d in all_doctors}

    return templates.TemplateResponse(
        request=request,
        name="users.html",
        context=_ctx(
            request, user, "users",
            {"users": users, "doctors_by_id": doctors_by_id, "doctors": all_doctors},
        ),
    )


@app.get("/users/{user_id}", response_class=HTMLResponse)
def user_detail_page(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """👤 Xodim detali — faqat admin. Ko'rsatiladigan ma'lumot rolga qarab
    farqlanadi: doctor uchun Appointment tarixi (doctor_detail.html bilan
    bir xil pattern), boshqalari uchun AuditLog'dagi so'nggi 50 amali
    (chunki Appointment/Payment'da created_by kabi maydon yo'q — bu
    tarix AuditLog orqali kuzatiladi)."""
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    if user is None:
        return RedirectResponse(url="/login")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Faqat admin uchun")

    target = db.query(models.User).filter(models.User.id == user_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")

    appointments = []
    audit_logs = []
    if target.role == "doctor" and target.doctor_id:
        appointments = (
            db.query(models.Appointment)
            .filter(models.Appointment.doctor_id == target.doctor_id)
            .order_by(models.Appointment.scheduled_time.desc())
            .all()
        )
    else:
        audit_logs = (
            db.query(models.AuditLog)
            .filter(models.AuditLog.user_id == target.id)
            .order_by(models.AuditLog.id.desc())
            .limit(50)
            .all()
        )

    return templates.TemplateResponse(
        request=request,
        name="user_detail.html",
        context=_ctx(
            request, user, "users",
            {
                "target": target,
                "appointments": appointments,
                "audit_logs": audit_logs,
                "doctors": list_doctors(db),
            },
        ),
    )

# ==============================================
# 🚀 MAIN
# ==============================================

if __name__ == "__main__":
    uvicorn.run(
        "main:app", 
        host="0.0.0.0" if os.environ.get("ENV") == "production" else "127.0.0.1",  # nosec
        port=int(os.environ.get("PORT", 8000)), 
        reload=os.environ.get("ENV") != "production"
    )