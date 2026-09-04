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
import traceback as traceback_module
import base64
import hashlib
import re
from contextlib import asynccontextmanager, closing
from datetime import date, datetime
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler as default_http_exception_handler
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy.orm import Session, joinedload, defer
from sqlalchemy import or_

import models
import schemas

from models import SELF_PASSWORD_CHANGE_LIMIT
from auth import get_current_user_optional, get_current_user, get_cache_stats, clear_user_cache
from audit import log_action  
from rate_limiter import limiter
from database import engine as db_engine
from database import get_db
from database import SessionLocal as SessionLocalForErrorLookup
from utils.timezone import now_in_clinic_tz, today_in_clinic_tz
from eskiz_client import sms_enabled
from modules.appointments import list_appointments_all as list_appointments, list_appointments as list_appointments_paged
from modules.dashboard import get_dashboard_summary, get_dashboard_summary_by_role, get_live_queue
from modules.doctors import list_doctors
from modules.patients import compute_financials, list_patients_all as list_patients, list_patients as list_patients_paged
from modules.payments import list_payments_all as list_payments, list_payments as list_payments_paged
from modules.lab_results import _parse_result_data
from modules.security_center import record_system_error_isolated, record_unauthorized_access
from modules.reports import (
    get_cancel_reasons,
    get_doctor_performance,
    get_hourly_load,
    get_patient_retention,
    get_period_trend,
    get_report_overview,
    get_status_breakdown,
    get_weekday_load,
    parse_date_range,
)
from reminder_service import start_reminder_service, stop_reminder_service
import backup_manager

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
    backup_manager.start_backup_scheduler()
    try:
        yield
    finally:
        stop_reminder_service()
        backup_manager.stop_backup_scheduler()


# Productionda API sxemasini (barcha endpoint/parametr nomlari) ochiq
# qoldirmaslik uchun /docs, /redoc, /openapi.json faqat development'da yoqiq.
_IS_PRODUCTION = os.environ.get("ENV") == "production"

app = FastAPI(
    title="ClinicFlow Core Engine",
    version="3.0.0",
    description="Optimallashtirilgan klinika boshqaruv tizimi",
    lifespan=lifespan,
    docs_url=None if _IS_PRODUCTION else "/docs",
    redoc_url=None if _IS_PRODUCTION else "/redoc",
    openapi_url=None if _IS_PRODUCTION else "/openapi.json",
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
#
# 🐛 FIX (CSP 'unsafe-inline'): ilgari script-src'da 'unsafe-inline'
# bor edi — bu har qanday inline <script> yoki onclick="..." kabi
# atributni (shu jumladan XSS orqali INJEKSIYA qilingan kodni ham)
# bemalol ishga tushirishga ruxsat berardi, ya'ni CSP'ning XSS'ga
# qarshi asosiy himoyasini deyarli ma'nosiz qilardi.
#
# To'g'ridan-to'g'ri 'unsafe-inline'ni olib tashlab, nonce qo'shish —
# bu yerda ishlamaydi: ilovada 17 ta shablon bo'ylab 140+ ta
# onclick="..." kabi inline hodisa-ishlovchisi bor, va CSP spetsifikatsiyasiga
# ko'ra 'nonce-'/'sha256-' manbasi qo'shilgan zahoti 'unsafe-inline'
# ZAMONAVIY brauzerlarda BUTUNLAY e'tiborsiz qoldiriladi — ya'ni nonce
# faqat <script> teglariga qo'yilsa ham, barcha onclick tugmalari
# ishlamay qoladi (butun UI buziladi).
#
# Shuning uchun HASH-ASOSLI (allow-list) yondashuv qo'llanadi: har bir
# javob (response) HTML bo'lsa, RENDER QILINGANDAN KEYIN unda haqiqatda
# mavjud bo'lgan <script>...</script> va onXXX="..." atribut
# tarkiblarining SHA-256 xeshlari hisoblanadi va CSP'ga aniq shu
# xeshlar RUXSAT ETILGAN manba sifatida qo'shiladi (`'sha256-...'` va
# hodisa-atributlar uchun `'unsafe-hashes'`). Natijada:
#   - Shablonlarda mavjud, LEGITIM inline kod (server tomonidan
#     render qilingan) ishlashda davom etadi — HECH bir shablonni
#     qayta yozish shart emas.
#   - Hujumchi tomonidan XSS orqali INJEKSIYA qilingan har qanday
#     yangi <script> yoki onXXX="..." — uning xeshi allow-list'da
#     bo'lmagani uchun — brauzer tomonidan BLOKLANADI.
# Bu — 'unsafe-inline'ning o'zi bergan ("istalgan inline kod
# ishlaydi") umumiy ruxsatdan farqli, "faqat AYNAN shu javobda
# render qilingan kod ishlaydi" tamoyiliga asoslangan qat'iy CSP.
_INLINE_SCRIPT_RE = re.compile(
    r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_INLINE_EVENT_ATTR_RE = re.compile(
    r"""\bon[a-z]+\s*=\s*(?:"([^"]*)"|'([^']*)')""",
    re.IGNORECASE,
)
# 🐛 FIX (CSP 'unsafe-inline' — #3-band, style-src): script-src'dagi bilan
# AYNAN bir xil muammo style-src'da ham bor edi — 'unsafe-inline' istalgan
# inline <style>...</style> blokini VA istalgan style="..." atributini
# (shu jumladan XSS orqali INJEKSIYA qilingan, masalan
# style="background:url(javascript:...)" yoki CSS orqali ma'lumot sizib
# chiqadigan uslublarni) bemalol ishga tushirishga ruxsat berardi.
# script-src'dagi bilan bir xil hash-asoslik yondashuv qo'llaniladi:
#   - <style>...</style> tarkibi   -> oddiy 'sha256-...' (script bilan bir xil)
#   - style="..." atribut qiymati  -> 'unsafe-hashes' + 'sha256-...' (CSS
#     spetsifikatsiyasiga ko'ra atribut-ichidagi hash faqat 'unsafe-hashes'
#     birga bo'lsa ishlaydi — xuddi onXXX="..." hodisa-atributlari kabi).
_INLINE_STYLE_TAG_RE = re.compile(
    r"<style[^>]*>(.*?)</style>",
    re.IGNORECASE | re.DOTALL,
)
_INLINE_STYLE_ATTR_RE = re.compile(
    r"""\bstyle\s*=\s*(?:"([^"]*)"|'([^']*)')""",
    re.IGNORECASE,
)


def _sha256_b64(content: str) -> str:
    return base64.b64encode(hashlib.sha256(content.encode("utf-8")).digest()).decode("ascii")


def _collect_inline_script_hashes(html: str) -> tuple[set[str], set[str]]:
    """HTML matnidan inline <script> tarkiblari va onXXX="..." hodisa
    atributlari qiymatlarining SHA-256 (base64) xeshlarini yig'adi.
    Qaytadi: (script_hashes, event_attr_hashes)."""
    script_hashes: set[str] = set()
    for match in _INLINE_SCRIPT_RE.finditer(html):
        body = match.group(1)
        if body.strip():
            script_hashes.add(_sha256_b64(body))

    event_hashes: set[str] = set()
    for match in _INLINE_EVENT_ATTR_RE.finditer(html):
        value = match.group(1) if match.group(1) is not None else match.group(2)
        # CSP 'unsafe-hashes' xeshi HTML-unescape qilinmagan, brauzer
        # ko'radigan HAM XUDDI shu (attribute-encoded) matn ustidan
        # hisoblanadi — shuning uchun bu yerda ham xom (raw) qiymat
        # ishlatiladi, qo'shimcha dekodlash qilinmaydi.
        if value.strip():
            event_hashes.add(_sha256_b64(value))

    return script_hashes, event_hashes


def _collect_inline_style_hashes(html: str) -> tuple[set[str], set[str]]:
    """HTML matnidan inline <style> tarkiblari va style="..." atribut
    qiymatlarining SHA-256 (base64) xeshlarini yig'adi (#3-band).
    Qaytadi: (style_tag_hashes, style_attr_hashes)."""
    style_tag_hashes: set[str] = set()
    for match in _INLINE_STYLE_TAG_RE.finditer(html):
        body = match.group(1)
        if body.strip():
            style_tag_hashes.add(_sha256_b64(body))

    style_attr_hashes: set[str] = set()
    for match in _INLINE_STYLE_ATTR_RE.finditer(html):
        value = match.group(1) if match.group(1) is not None else match.group(2)
        if value.strip():
            style_attr_hashes.add(_sha256_b64(value))

    return style_tag_hashes, style_attr_hashes


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)

    # 1. MIME-sniffing hujumlaridan himoya (X-Content-Type-Options)
    response.headers["X-Content-Type-Options"] = "nosniff"

    # 2. Clickjacking (UI Redressing) himoyasi (X-Frame-Options)
    response.headers["X-Frame-Options"] = "SAMEORIGIN"

    # 3. Eskirgan brauzerlar uchun XSS filtr
    response.headers["X-XSS-Protection"] = "1; mode=block"

    # 4. Content Security Policy (CSP) — script-src endi hash-asoslik
    # allow-list bilan qat'iylashtirilgan (yuqoridagi izohga qarang).
    # Faqat HTML javoblar tekshiriladi (JSON/statik fayllar CSP'dan
    # mustaqil — ularda ijro etiladigan inline skript bo'lmaydi).
    content_type = response.headers.get("content-type", "")
    script_src_extra = ""
    style_src_extra = ""
    if "text/html" in content_type:
        body_chunks = [chunk async for chunk in response.body_iterator]
        body = b"".join(body_chunks)
        html_text = body.decode("utf-8", errors="ignore")

        script_hashes, event_hashes = _collect_inline_script_hashes(html_text)
        parts = [f"'sha256-{h}'" for h in sorted(script_hashes)]
        if event_hashes:
            parts.append("'unsafe-hashes'")
            parts.extend(f"'sha256-{h}'" for h in sorted(event_hashes))
        if parts:
            script_src_extra = " " + " ".join(parts)

        # #3-band: style-src uchun ham xuddi shunday — <style> tarkiblari
        # oddiy sha256 sifatida, style="..." atribut qiymatlari esa
        # 'unsafe-hashes' bilan birga qo'shiladi.
        style_tag_hashes, style_attr_hashes = _collect_inline_style_hashes(html_text)
        style_parts = [f"'sha256-{h}'" for h in sorted(style_tag_hashes)]
        if style_attr_hashes:
            style_parts.append("'unsafe-hashes'")
            style_parts.extend(f"'sha256-{h}'" for h in sorted(style_attr_hashes))
        if style_parts:
            style_src_extra = " " + " ".join(style_parts)

        # body_iterator bir marta sarflanadi — qayta o'qilgan baytlar
        # bilan yangi Response yaratamiz, aks holda javob tanasi bo'sh
        # jo'natiladi.
        response = Response(
            content=body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"

    csp = (
        "default-src 'self'; "
        f"script-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com{script_src_extra}; "
        f"style-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://fonts.googleapis.com{style_src_extra}; "
        "img-src 'self' data:; "
        "font-src 'self' https://cdnjs.cloudflare.com https://fonts.gstatic.com data:; "
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


def _merge_query_string(request: Request, **overrides: Any) -> str:
    """Joriy URL query-parametrlarini saqlab qolib, faqat berilganlarini
    almashtiradi/qo'shadi (qiymati None bo'lsa — olib tashlaydi). Sort/
    pagination havolalari uchun (masalan patients.html'dagi allergiya
    filtri sahifalash/saralash bosilganda ham saqlanib qoladi) — Prompt 25/1,
    templates/_pagination.html shu funksiyani chaqiradi."""
    params = dict(request.query_params)
    for key, value in overrides.items():
        if value is None:
            params.pop(key, None)
        else:
            params[key] = str(value)
    return urlencode(params)


templates.env.globals["merge_qs"] = _merge_query_string

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

def _calc_age(birth_date: Optional[date], db: Optional[Session] = None) -> Optional[int]:
    """birth_date asosida to'liq yil hisobidagi yoshni qaytaradi (hali tug'ilgan
    kuni yetib kelmagan bo'lsa 1 yil ayiradi). birth_date bo'lmasa None."""
    if birth_date is None:
        return None
    today = today_in_clinic_tz(db)  # ⬅️ Prompt 16: server emas, klinika vaqt zonasi
    age = today.year - birth_date.year
    if (today.month, today.day) < (birth_date.month, birth_date.day):
        age -= 1
    return age

def _ctx(request: Request, user: models.User, active_page: str, extra: Optional[dict] = None) -> dict:
    base = {
        "request": request,
        "active_page": active_page,
        "current_user": user,
        "app_version": "3.0.0",
        "year": now_in_clinic_tz().year,  # ⬅️ Prompt 16
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

    # Prompt 4: har bir rol faqat o'ziga tegishli ko'rsatkichlarni ko'radi
    # (modules/dashboard.py -> get_dashboard_summary_by_role / get_live_queue).
    summary = get_dashboard_summary_by_role(db, user)
    queue = get_live_queue(db, user)

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
                "today": today_in_clinic_tz(db).isoformat(),  # ⬅️ Prompt 16
                "load_time": f"{elapsed:.2f}ms"
            }
        ),
    )

# ==============================================
# 👥 PATIENTS
# ==============================================

_PATIENT_SORT_KEYS = {"id", "fullname", "created_at", "birth_date"}


@app.get("/patients", response_class=HTMLResponse)
def patients_page(
    request: Request, 
    db: Session = Depends(get_db),
    allergy: Optional[str] = None,
    chronic_condition: Optional[str] = None,
    sort_by: str = "id",
    sort_order: str = "desc",
    page: int = 1,
) -> HTMLResponse:
    start_time = time.time()
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    if user is None:
        return RedirectResponse(url="/login")
    _require_module(user, "patients")

    # 🔐 Allergiya/surunkali kasallik bo'yicha filtr tibbiy ma'lumot —
    # cashier uchun sahifada input umuman ko'rinmaydi (patients.html), lekin
    # kimdir URL'ga qo'lda ?allergy=... qo'shsa ham, backend (list_patients)
    # buni cashier uchun jim ravishda e'tiborsiz qoldiradi — 403 bilan butun
    # sahifani buzish o'rniga, oddiy (filtrsiz) ro'yxat qaytadi.
    can_filter_medical = user.role in ("admin", "reception", "doctor")

    # Prompt 25/1: sarlavha-saralash va sahifalash — mavjud (Prompt 24)
    # sahifalangan /api/patients/list backend'ining o'ziga, oddiy Python
    # chaqiruvi orqali murojaat qilinadi (URL orqali emas), noto'g'ri
    # sort_by/page URL'dan qo'lda kiritilsa ham backend xatoga tushmaydi.
    sort_by = sort_by if sort_by in _PATIENT_SORT_KEYS else "id"
    sort_order = sort_order if sort_order in ("asc", "desc") else "desc"
    page = max(page, 1)
    result = list_patients_paged(
        db=db,
        user=user,
        allergy=allergy if can_filter_medical else None,
        chronic_condition=chronic_condition if can_filter_medical else None,
        search=None,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=20,
    )
    # list_patients_paged (Prompt 24 API) items — pydantic PatientRead
    # obyektlari (from_attributes=True), ORM Patient emas — shuning uchun
    # ular ustiga qo'shimcha maydon (yo'q, hisoblanadigan .age) to'g'ridan
    # -to'g'ri o'rnatib bo'lmaydi (pydantic modelida e'lon qilinmagan
    # atribut). Shuning uchun dict'ga aylantirib, "age"ni shu yerda
    # qo'shamiz — Jinja p.age ham dict kaliti bo'yicha ishlaydi.
    patients_list = []
    for p in result.items:
        pd = p.model_dump()
        pd["age"] = _calc_age(pd.get("birth_date"), db)
        patients_list.append(pd)

    elapsed = (time.time() - start_time) * 1000
    logger.info(f"👥 Patients loaded in {elapsed:.2f}ms for user: {user.username}")
    
    return templates.TemplateResponse(
        request=request, 
        name="patients.html", 
        context=_ctx(
            request, user, "patients", 
            {
                "patients": patients_list,
                "total": result.total,
                "page": result.page,
                "total_pages": result.total_pages,
                "sort_by": sort_by,
                "sort_order": sort_order,
                "load_time": f"{elapsed:.2f}ms",
                "can_filter_medical": can_filter_medical,
                "filter_allergy": allergy or "",
                "filter_chronic_condition": chronic_condition or "",
            }
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
    _require_module(user, "patients")

    patient = db.query(models.Patient).filter(models.Patient.id == patient_id).first()
    if patient is None:
        raise HTTPException(status_code=404, detail="Bemor topilmadi")

    # ⬅️ YANGI: lab_doctor faqat O'ZIGA biriktirilgan bemorning sahifasini
    # ochishi mumkin — patients.html ro'yxatida ko'rinmasa ham, to'g'ridan
    # -to'g'ri /patients/{id} URL orqali boshqa lab shifokorining
    # bemorini ko'rish mumkin bo'lmasligi kerak.
    if user.role == "lab_doctor":
        has_access = (
            db.query(models.LabResult)
            .filter(
                models.LabResult.patient_id == patient_id,
                models.LabResult.doctor_id == user.doctor_id,
            )
            .first()
            is not None
        )
        if not has_access:
            raise HTTPException(status_code=403, detail="Bu bemor sizga biriktirilmagan")
    
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

    # ⚠️ Allergiyalar va surunkali kasalliklar — bemor sahifasidagi
    # xavfsizlik banneri (barcha rollarga ko'rinadi) va tahrirlash
    # bo'limi (faqat admin/reception/doctor) shu ro'yxatlarga tayanadi.
    allergies = (
        db.query(models.Allergy)
        .filter(models.Allergy.patient_id == patient_id)
        .order_by(models.Allergy.id.desc())
        .all()
    )
    chronic_conditions = (
        db.query(models.ChronicCondition)
        .filter(models.ChronicCondition.patient_id == patient_id)
        .order_by(models.ChronicCondition.id.desc())
        .all()
    )

    # 💊 Davolanishlar tarixi + shifokorlar ro'yxati (yozuv qo'shish
    # formasidagi tanlov uchun) — FAQAT admin/reception/doctor uchun
    # so'raladi. Allergiya/kasallik kabi "har doim so'rab, shablonda
    # yashirish" o'rniga bu yerda backend darajasida ham so'rov
    # yubormaslikni tanladik: TreatmentHistory.diagnosis/treatment eng
    # nozik (shifrlangan) tibbiy maydonlar, shuning uchun cashier uchun
    # ularni DB'dan hech deshifrlamaslik — ikki qavatli himoya
    # (medical-security-auditor: least privilege / defence in depth).
    can_view_treatment = user.role in ("admin", "reception", "doctor")
    treatment_history = []
    treatment_doctors = []
    if can_view_treatment:
        treatment_history = (
            db.query(models.TreatmentHistory)
            .filter(models.TreatmentHistory.patient_id == patient_id)
            .order_by(
                models.TreatmentHistory.date.desc(),
                models.TreatmentHistory.id.desc(),
            )
            .all()
        )
        treatment_doctors = (
            db.query(models.Doctor)
            .filter(models.Doctor.is_active == True)  # noqa: E712
            .order_by(models.Doctor.fullname)
            .all()
        )

    # 🔬 Tahlil natijalari: shu bemorga tegishli barcha LabResult yozuvlari
    # — /lab-results/ sahifasidagi bilan bir xil ko'rinishda (parsed
    # ko'rsatkichlar yoki eski erkin-matn), shunda ma'lumot ikkala joyda
    # ham SINXRON ko'rinadi (bitta yozuv, ikkita ko'rinish).
    # Prompt 5: faqat admin/doctor/lab_doctor ko'radi — cashier/reception/
    # assistant_admin uchun bemor sahifasida ham lab natijalari yashirin.
    lab_rows = []
    if user.role in ("admin", "doctor", "lab_doctor"):
        lab_results = (
            db.query(models.LabResult)
            .filter(models.LabResult.patient_id == patient_id)
            .order_by(models.LabResult.created_at.desc())
            .all()
        )
        for r in lab_results:
            parsed = _parse_result_data(r.result_data)
            lab_rows.append({
                "obj": r,
                "parsed": parsed,
                "abnormal_count": parsed.get("abnormal_count", 0) if parsed else None,
                "raw_text": None if parsed else r.result_data,
            })

    # 🛏️ TUZATISH (#11-band): Palata (statsionar) tarixi — API
    # (`GET /api/patients/{id}`, modules/patients.py::get_patient) buni
    # to'g'ri qaytaradi, lekin bu server-render HTML sahifasi uni
    # umuman so'ramas/kontekstga uzatmas edi, shuning uchun
    # patient_detail.html'da hech qachon ko'rinmasdi. `patient.admissions`
    # relationship'i models.py'da `order_by="PatientAdmission.admitted_at.desc()"`
    # bilan e'lon qilingan, shuning uchun bu yerda qo'shimcha saralash
    # shart emas. Ruxsat: palata/rooms/admitted bilan bir xil naqsh
    # (admin/reception/doctor) — statsionar ma'lumot cashier/lab_doctor'ga
    # kerak emas.
    admissions = patient.admissions if can_view_treatment else []

    return templates.TemplateResponse(
        request=request,
        name="patient_detail.html",
        context=_ctx(
            request,
            user,
            "patients",
            {
                "patient": patient,
                "patient_age": _calc_age(patient.birth_date, db),
                "financials": financials,
                "appointments": appointments,
                "payments": payments,
                "refunded_ids": refunded_ids,
                "lab_rows": lab_rows,
                "allergies": allergies,
                "chronic_conditions": chronic_conditions,
                "treatment_history": treatment_history,
                "treatment_doctors": treatment_doctors,
                "can_view_treatment": can_view_treatment,
                "admissions": admissions,
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
    _require_module(user, "doctors")
    
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
    _require_module(user, "doctors")
    
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

_APPOINTMENT_SORT_KEYS = {"id", "scheduled_time", "price", "status"}


@app.get("/appointments", response_class=HTMLResponse)
def appointments_page(
    request: Request, 
    db: Session = Depends(get_db),
    sort_by: str = "scheduled_time",
    sort_order: str = "desc",
    page: int = 1,
) -> HTMLResponse:
    start_time = time.time()
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    if user is None:
        return RedirectResponse(url="/login")
    _require_module(user, "appointments")

    # ⬅️ To'liq (sahifalanmagan) ro'yxat — faqat "Yangi To'lov" modalidagi
    # ochiq qabullar select'ini to'ldirish uchun kerak (jadval sahifalanган
    # bo'lsa ham, bu select HAMMA qarzdor qabullarni ko'rsatishi kerak).
    appointments_all = list_appointments(db)
    patients = list_patients(db, user)
    # Prompt 8: bu ro'yxat "Yangi Qabul Band Qilish" select'ini to'ldiradi
    # (templates/base.html #a-doctor) — faolsizlantirilgan shifokor yangi
    # qabullar uchun tanlanmasin, shuning uchun faqat is_active=True.
    doctors = list_doctors(db, active_only=True)
    
    open_appointments = _open_appointments_for_select(
        [a for a in appointments_all if a.status != "cancelled" and a.debt > 0]
    )

    # Prompt 25/1: jadval o'zi — saralangan va sahifalangan (Prompt 24
    # /api/appointments/list backend'iga to'g'ridan-to'g'ri Python
    # chaqiruvi orqali).
    sort_by = sort_by if sort_by in _APPOINTMENT_SORT_KEYS else "scheduled_time"
    sort_order = sort_order if sort_order in ("asc", "desc") else "desc"
    page = max(page, 1)
    result = list_appointments_paged(
        db=db,
        search=None,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=20,
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
                "appointments": result.items,
                "total": result.total,
                "page": result.page,
                "total_pages": result.total_pages,
                "sort_by": sort_by,
                "sort_order": sort_order,
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

_PAYMENT_SORT_KEYS = {"id", "amount", "status", "created_at"}


@app.get("/payments", response_class=HTMLResponse)
def payments_page(
    request: Request,
    db: Session = Depends(get_db),
    sort_by: str = "id",
    sort_order: str = "desc",
    page: int = 1,
) -> HTMLResponse:
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    if user is None:
        return RedirectResponse(url="/login")
    _require_module(user, "payments")

    # Prompt 25/1: jadval — saralangan va sahifalangan (Prompt 24
    # /api/payments/list backend'iga to'g'ridan-to'g'ri Python chaqiruvi).
    sort_by = sort_by if sort_by in _PAYMENT_SORT_KEYS else "id"
    sort_order = sort_order if sort_order in ("asc", "desc") else "desc"
    page = max(page, 1)
    result = list_payments_paged(
        db=db,
        search=None,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        page_size=20,
    )
    # 🛏 Prompt 11 — N+1 tuzatish: pastda `_open_appointments_for_select`
    # har bir Appointment uchun uchta narsaga murojaat qiladi:
    #   - `.debt` (property) -> `.payments` (models.py: Appointment.debt
    #     -> paid_amount -> self.payments),
    #   - `.patient.fullname` (agar patient_name oldindan hisoblanmagan
    #     bo'lsa — aynan shu yerda, xom models.Appointment obyektlari
    #     uchun shunday),
    #   - `.doctor.fullname` (xuddi shunday).
    # Eager load qilmasak, ochiq qabullar ro'yxatidagi HAR BIR
    # appointment uchun 2-3 ta alohida SQL so'rov ketadi (klassik N+1).
    # `joinedload(...)` uchalasini ham bitta LEFT JOIN so'rovga
    # birlashtiradi. Appointment<->Payment bir-ko'pga (1:N) bog'liq
    # bo'lgani uchun bitta appointment bir nechta to'lovga ega bo'lsa,
    # JOIN natijasida shu appointment qatori bir necha marta qaytishi
    # mumkin edi — legacy `db.query()` (Query) API buni Python
    # darajasida AVTOMATIK unique() qiladi (identity-map bo'yicha),
    # shuning uchun natija baribir dublikatsiz qaytadi
    # (tests/test_appointments_n_plus_one.py bu holatni aniq
    # tekshiradi).
    raw_open = (
        db.query(models.Appointment)
        .options(
            joinedload(models.Appointment.payments),
            joinedload(models.Appointment.patient),
            joinedload(models.Appointment.doctor),
        )
        .filter(models.Appointment.status != "cancelled")
        .all()
    )
    open_appointments = _open_appointments_for_select([a for a in raw_open if a.debt > 0])
    return templates.TemplateResponse(
        request=request,
        name="payments.html",
        context=_ctx(
            request,
            user,
            "payments",
            {
                "payments": result.items,
                "total": result.total,
                "page": result.page,
                "total_pages": result.total_pages,
                "sort_by": sort_by,
                "sort_order": sort_order,
                "open_appointments": open_appointments,
            },
        ),
    )

# ==============================================
# 📊 REPORTS
# ==============================================

@app.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    if user is None:
        return RedirectResponse(url="/login")
    if "reports" not in ROLE_MODULE_ACCESS.get(user.role, {"dashboard"}):
        return RedirectResponse(url="/dashboard")

    date_from = request.query_params.get("date_from")
    date_to = request.query_params.get("date_to")
    start, end = parse_date_range(date_from, date_to)

    summary = get_dashboard_summary(db)
    overview = get_report_overview(db, start, end)
    status_breakdown = get_status_breakdown(db, start, end)
    doctor_performance = get_doctor_performance(db, start, end)
    cancel_reasons = get_cancel_reasons(db, start, end)
    hourly_load = get_hourly_load(db, start, end)
    weekday_load = get_weekday_load(db, start, end)
    retention = get_patient_retention(db, start, end)
    period_trend = get_period_trend(db)

    return templates.TemplateResponse(
        request=request, 
        name="reports.html", 
        context=_ctx(
            request, user, "reports",
            {
                "summary": summary,
                "overview": overview,
                "status_breakdown": status_breakdown,
                "doctor_performance": doctor_performance,
                "cancel_reasons": cancel_reasons,
                "hourly_load": [row.model_dump() for row in hourly_load],
                "weekday_load": [row.model_dump() for row in weekday_load],
                "retention": retention,
                "period_trend": [row.model_dump() for row in period_trend],
                "date_from_str": start.strftime("%Y-%m-%d"),
                "date_to_str": end.strftime("%Y-%m-%d"),
            },
        )
    )

# ==============================================
# ⚙️ SOZLAMALAR — har bir xodim uchun o'z profili paneli
#
# Bu — /users (faqat admin, hamma xodimlarni ko'radi/tahrirlaydi) dan
# FARQLI: bu yerda HAR QANDAY tizimga kirgan xodim FAQAT o'zining
# ma'lumotlarini ko'radi va faqat o'ziga tegishli amallarni bajaradi
# (parolni almashtirish). "Kerakli menular" — xodimning ROLIGA mos
# bo'limlarga tezkor havolalar (masalan shifokorga to'lovlar ko'rsatilmaydi,
# chunki /payments unga baribir 403 qaytaradi — qarang: payments_page).
# ==============================================

def _quick_links_for_role(role: str) -> list[dict]:
    all_links = [
        {"key": "dashboard", "href": "/dashboard", "icon": "fa-chart-pie", "label": "Dashboard",
         "desc": "Bugungi navbat va umumiy statistika"},
        {"key": "patients", "href": "/patients", "icon": "fa-user-injured", "label": "Bemorlar",
         "desc": "Bemorlar ro'yxati va tarixi"},
        {"key": "appointments", "href": "/appointments", "icon": "fa-calendar-check", "label": "Qabul",
         "desc": "Navbatlarni band qilish va boshqarish"},
        {"key": "doctors", "href": "/doctors", "icon": "fa-user-md", "label": "Doktorlar",
         "desc": "Shifokorlar va ularning jadvali"},
        {"key": "lab_results", "href": "/lab-results/", "icon": "fa-vial", "label": "Tahlil natijalari",
         "desc": "Laboratoriya natijalarini ko'rish/kiritish"},
        {"key": "payments", "href": "/payments", "icon": "fa-credit-card", "label": "To'lovlar",
         "desc": "To'lov va qaytarimlar"},
        {"key": "reports", "href": "/reports", "icon": "fa-file-invoice-dollar", "label": "Hisobot",
         "desc": "Moliyaviy va statistik hisobotlar"},
        {"key": "users", "href": "/users", "icon": "fa-users-gear", "label": "Foydalanuvchilar",
         "desc": "Xodimlarni boshqarish (admin)"},
        {"key": "audit_log", "href": "/admin/audit-log", "icon": "fa-clipboard-list", "label": "Audit jurnal",
         "desc": "Kim, qachon, nima qildi (admin)"},
        {"key": "backup", "href": "/admin/backup", "icon": "fa-database", "label": "Zaxira nusxa",
         "desc": "Bazani avtomatik/qo'lda zaxiralash (admin)"},
    ]
    allowed = ROLE_MODULE_ACCESS.get(role, {"dashboard"})
    return [link for link in all_links if link["key"] in allowed]


# ==============================================
# 🔐 ROL → MODUL RUXSATLARI (yagona manba)
#
# Bu lug'at IKKI joyda ishlatiladi: (1) yuqoridagi _quick_links_for_role
# va sidebar (base.html) uchun — qaysi havolalar KO'RINADI; (2) pastdagi
# _require_module (va har bir sahifa route'i) uchun — qaysi sahifaga
# to'g'ridan-to'g'ri URL orqali kirish ham RUXSAT ETILADI. Ikkalasi bitta
# manbadan olingani uchun "sidebar'da yashirilgan, lekin URL orqali ochiq"
# nomuvofiqligi bo'lmaydi.
#
# Yozish (create/update/delete) huquqlari BU YERDA emas — ular har bir
# modulning o'zida require_role(...) bilan alohida-alohida cheklanadi
# (masalan modules/patients.py: add/edit/delete faqat admin+reception).
# Shu lug'at faqat "ko'rish/sahifaga kirish" darajasini bildiradi.
#
#   assistant_admin — hisobotlar, xodimlar (ko'rish+qo'shish), bemorlar
#                      (ko'rish), navbatlar (ko'rish), dashboard,
#                      xavfsizlik monitoringi (audit_log) — barchasi
#                      FAQAT O'QISH uchun.
#   lab_doctor      — faqat dashboard (o'ziga xos), bemorlar (faqat
#                      biriktirilganlar) va lab-results (kiritish+ko'rish).
#                      Boshqa modullarga (appointments/doctors/payments/
#                      reports/users/audit_log/backup) kira olmaydi.
#   cashier, reception, assistant_admin — talab bo'yicha lab-results'ga
#                      UMUMAN KIRA OLMAYDI (avval reception/cashier
#                      sidebar'da ko'rinardi, endi olib tashlandi).
# ==============================================
ROLE_MODULE_ACCESS = {
    "admin": {"dashboard", "patients", "appointments", "doctors", "lab_results",
              "payments", "reports", "users", "audit_log", "backup", "admin_profile",
              "security"},
    "reception": {"dashboard", "patients", "appointments", "doctors",
                  "payments"},
    "cashier": {"dashboard", "patients", "appointments", "doctors", "payments"},
    "doctor": {"dashboard", "patients", "appointments", "doctors", "lab_results"},
    # ⬅️ YANGI (Prompt 9): "security" — xavfsizlik markazi (shifokor
    # xabarlari, tizim xatoliklari, kirish loglari) sahifasiga kirish.
    # assistant_admin bu yerda ham FAQAT O'QISH huquqiga ega (yozish
    # amallari — o'qilgan deb belgilash/o'chirish — faqat admin uchun
    # modules/security_center.py'da alohida require_role("admin") bilan
    # cheklangan, ROLE_MODULE_ACCESS faqat sahifaga kirishni bildiradi).
    "assistant_admin": {"dashboard", "patients", "appointments", "reports",
                         "users", "audit_log", "admin_profile", "security"},
    "lab_doctor": {"dashboard", "patients", "lab_results"},
}


def _require_module(user: models.User, module_key: str) -> None:
    """Sahifa darajasidagi ruxsat tekshiruvi — ROLE_MODULE_ACCESS'ga
    tayanadi. Ruxsat yo'q bo'lsa 403 qaytaradi (link yashirilgan bo'lsa
    ham, to'g'ridan-to'g'ri URL orqali chetlab o'tib bo'lmasligi uchun)."""
    if module_key not in ROLE_MODULE_ACCESS.get(user.role, {"dashboard"}):
        raise HTTPException(status_code=403, detail="Bu bo'limga kirish huquqingiz yo'q")


# ==============================================
# 🔎 GLOBAL QIDIRUV (Prompt 25/3)
# ==============================================
# Topbar'dagi bitta qidiruv input'i — bemor, shifokor, qabul va to'lovlar
# orasida BIR VAQTDA qidiradi, natijalarni turi bo'yicha guruhlab
# qaytaradi. Har bir guruh faqat foydalanuvchi ROLE_MODULE_ACCESS'da
# tegishli modulga ruxsati bo'lsagina to'ldiriladi — masalan lab_doctor
# uchun "appointments"/"payments"/"doctors" har doim bo'sh qaytadi,
# sahifa darajasidagi kirish cheklovi bilan bir xil bo'lishi uchun.
# Bemorlar qidiruvi qo'shimcha ravishda list_patients_paged (modules/
# patients.py) orqali o'tadi — u lab_doctor uchun "faqat biriktirilgan
# bemorlar" qatorini ham qo'llaydi, shu logikani bu yerda takrorlamaslik
# uchun.
_SEARCH_RESULT_LIMIT = 5


@app.get("/api/search", response_model=schemas.GlobalSearchResponse)
def global_search(
    q: str = "",
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> schemas.GlobalSearchResponse:
    q = q.strip()
    allowed = ROLE_MODULE_ACCESS.get(user.role, {"dashboard"})
    result = schemas.GlobalSearchResponse()
    if len(q) < 2:
        return result

    if "patients" in allowed:
        patients_page = list_patients_paged(
            db=db, user=user, search=q, sort_by="fullname", sort_order="asc",
            page=1, page_size=_SEARCH_RESULT_LIMIT,
        )
        result.patients = [
            schemas.SearchResultItem(
                id=p.id, title=p.fullname, subtitle=p.phone, url=f"/patients/{p.id}"
            )
            for p in patients_page.items
        ]

    if "doctors" in allowed:
        like = f"%{q}%"
        doctors = (
            db.query(models.Doctor)
            .filter(or_(models.Doctor.fullname.ilike(like), models.Doctor.specialty.ilike(like)))
            .order_by(models.Doctor.fullname.asc())
            .limit(_SEARCH_RESULT_LIMIT)
            .all()
        )
        result.doctors = [
            schemas.SearchResultItem(
                id=d.id, title=d.fullname, subtitle=d.specialty, url=f"/doctors/{d.id}"
            )
            for d in doctors
        ]

    if "appointments" in allowed:
        appointments_page = list_appointments_paged(
            db=db, search=q, sort_by="scheduled_time", sort_order="desc",
            page=1, page_size=_SEARCH_RESULT_LIMIT,
        )
        result.appointments = [
            schemas.SearchResultItem(
                id=a.id,
                title=f"{a.patient_name} — {a.doctor_name}",
                subtitle=a.scheduled_time.strftime("%Y-%m-%d %H:%M"),
                url="/appointments",
            )
            for a in appointments_page.items
        ]

    if "payments" in allowed:
        payments_page = list_payments_paged(
            db=db, search=q, sort_by="created_at", sort_order="desc",
            page=1, page_size=_SEARCH_RESULT_LIMIT,
        )
        result.payments = [
            schemas.SearchResultItem(
                id=pay.id,
                title=f"{pay.patient_name} — {pay.amount:,} UZS",
                subtitle=pay.note or None,
                url="/payments",
            )
            for pay in payments_page.items
        ]

    return result


@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    if user is None:
        return RedirectResponse(url="/login")

    changes_used = user.self_password_change_count
    changes_remaining = max(SELF_PASSWORD_CHANGE_LIMIT - changes_used, 0)

    # Prompt 10: haqiqiy holat — bazadagi gov_integration_settings
    # qatorini o'qib, joriy yoqilgan/o'chirilgan holatni va integratsiya
    # nomini ko'rsatamiz. Sozlamalarning o'zi (yoqish/o'chirish, API
    # kalitlari) faqat /api/admin/gov-integration/settings orqali
    # (modules/gov_integration.py) o'zgartiriladi — bu yerda faqat
    # o'qish uchun.
    gov_integration_enabled = False
    gov_integration_name = None
    if user.role == "admin":
        # 🔒 with_entities bilan FAQAT is_enabled/integration_name
        # tanlanadi — api_key/api_secret (EncryptedString) ustunlari
        # SQL SELECT'ga umuman kirmaydi, shuning uchun ORM ularni
        # deshifrlashga urinmaydi. Aks holda (butun qatorni yuklasa)
        # CLINICFLOW_FIELD_KEY o'rnatilmagan muhitda profil sahifasi
        # FieldEncryptionError bilan qulab tushardi, hatto admin faqat
        # yoqilgan/o'chirilgan holatni ko'rmoqchi bo'lsa ham.
        gov_row = (
            db.query(
                models.GovIntegrationSettings.is_enabled,
                models.GovIntegrationSettings.integration_name,
            )
            .first()
        )
        if gov_row is not None:
            gov_integration_enabled = gov_row.is_enabled
            gov_integration_name = gov_row.integration_name

    return templates.TemplateResponse(
        request=request,
        name="profile.html",
        context=_ctx(
            request, user, "profile",
            {
                "quick_links": _quick_links_for_role(user.role),
                "password_limit": SELF_PASSWORD_CHANGE_LIMIT,
                "changes_used": changes_used,
                "changes_remaining": changes_remaining,
                "limit_reached": changes_remaining == 0,
                "gov_integration_enabled": gov_integration_enabled,
                "gov_integration_name": gov_integration_name,
            },
        ),
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
    # 🛡️ Prompt 9: har qanday 500 xatolik avtomatik SystemError jadvaliga
    # yoziladi. Alohida ("isolated") SessionLocal ishlatiladi — bu handler
    # aynan so'rovning o'z DB seansi buzuq/rollback holatda bo'lishi
    # mumkin bo'lgan paytda ishlaydi, shuning uchun Depends(get_db)ga
    # tayanib bo'lmaydi.
    cf_user = None
    try:
        isolated_db = SessionLocalForErrorLookup()
        try:
            cf_user = get_current_user_optional(request.cookies.get("cf_session"), isolated_db)
        finally:
            isolated_db.close()
    except Exception:
        cf_user = None
    record_system_error_isolated(
        endpoint=f"{request.method} {request.url.path}",
        error_message=str(exc) or exc.__class__.__name__,
        traceback_str=traceback_module.format_exc(),
        user_id=cf_user.id if cf_user else None,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Server xatosi yuz berdi. Administratorga murojaat qiling."},
    )


@app.exception_handler(FastAPIHTTPException)
async def http_exception_with_security_logging(request: Request, exc: FastAPIHTTPException) -> JSONResponse:
    """🛡️ Prompt 9: 403 ("ruxsat yo'q" — autentifikatsiya qilingan
    foydalanuvchi o'ziga tegishli bo'lmagan rol/resursga urinishi)
    javoblari SystemError'ga "ruxsatsiz kirish urinishi" sifatida
    yoziladi. 401 (sessiya yo'q/muddati tugagan) ATAYLAB bu yerga
    KIRMAYDI — u odatiy holat (login sahifasiga oddiy tashrif, sessiya
    tabiiy tugashi) va login endpointidagi muvaffaqiyatsiz urinishlar
    allaqachon o'zining maxsus, buning uchun mo'ljallangan jadvalida —
    LoginLog'da — record_login_attempt() orqali qayd etiladi (qarang:
    modules/auth_module.py), shuning uchun bu yerda ikki marta
    yozilmaydi. Javobning o'zi FastAPI'ning standart HTTPException
    handleri orqali o'zgarishsiz qaytariladi (status_code/detail bir
    xil qoladi)."""
    if exc.status_code == 403:
        cf_user = None
        try:
            # 🩹 Prompt 15: avval `db_gen = get_db(); db = next(db_gen)`
            # so'ng qo'lda `db.close()` chaqirilardi — bu SESSIYANI
            # yopadi, lekin `get_db()` GENERATOR'ining o'zini hech qachon
            # yopmaydi (uning ichidagi `finally: db.close()` bloki hech
            # qachon ishga tushmaydi, chunki generator na tugaydi, na
            # aniq `.close()` olinadi). CPython'da bu odatda generator
            # refsanoq nolga tushganda GC orqali "tasodifan" yopiladi,
            # lekin bunga tayanish shart emas — `contextlib.closing`
            # `db_gen.close()`ni KAFOLATLI, generator ichidagi kod
            # (get_current_user_optional/record_unauthorized_access)
            # xatolik bersa ham chaqiradi.
            db_gen = get_db()
            with closing(db_gen):
                db = next(db_gen)
                cf_user = get_current_user_optional(request.cookies.get("cf_session"), db)
                record_unauthorized_access(
                    db,
                    endpoint=f"{request.method} {request.url.path}",
                    detail=str(exc.detail),
                    status_code=exc.status_code,
                    user=cf_user,
                )
        except Exception:
            logger.exception("401/403 xatoligini SystemError'ga yozib bo'lmadi")

        # 🛡️ Prompt 14.1: xunuk JSON o'rniga — FAQAT UI (brauzer) so'rovlari
        # uchun chiroyli 403 sahifa. API/AJAX chaqiruvlar (JSON kutayotgan
        # yoki /api/ prefiksli) hamon standart JSON javobini olishda davom
        # etadi — frontend JS xato-ishlovchilari buzilmasligi uchun.
        accept = request.headers.get("accept", "")
        is_ui_request = (
            request.method == "GET"
            and "text/html" in accept
            and not request.url.path.startswith("/api/")
        )
        if is_ui_request and cf_user is not None:
            return templates.TemplateResponse(
                "errors/403.html",
                {"request": request, "current_user": cf_user, "active_page": None},
                status_code=403,
            )
    return await default_http_exception_handler(request, exc)

# ==============================================
# ✅ HEALTH CHECK
# ==============================================

@app.get("/health")
def health_check() -> dict:
    return {
        "status": "healthy",
        "version": "3.0.0",
        "timestamp": now_in_clinic_tz().isoformat(),  # ⬅️ Prompt 16
        "modules_loaded": len(clinicflow_engine.loaded_modules),
        "user_cache": get_cache_stats(),
        # 📩 FAZA 1: Eskiz.uz SMS servisi sozlanganmi (credential bor-yo'qligi,
        # tarmoq holati emas) — eskiz_client.py
        "sms_enabled": sms_enabled(),
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
    # ⬅️ YANGI: assistant_admin "xavfsizlik monitoringi"ni (audit jurnal)
    # FAQAT O'QISH tarzida ko'ra oladi — sahifada hech qanday yozuv
    # (create/update/delete) amali yo'q, shuning uchun bu xavfsiz.
    if "audit_log" not in ROLE_MODULE_ACCESS.get(user.role, {"dashboard"}):
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
# 🗄️ ZAXIRA NUSXA (BACKUP) — faqat admin
# ==============================================

def _require_admin(request: Request, db: Session) -> models.User:
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    if user is None:
        raise HTTPException(status_code=401, detail="Kirish talab qilinadi")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Faqat admin uchun")
    return user


@app.get("/admin/backup", response_class=HTMLResponse)
def backup_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    if user is None:
        return RedirectResponse(url="/login")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Faqat admin uchun")

    status = backup_manager.get_status()
    history = backup_manager.get_history(limit=15)
    return templates.TemplateResponse(
        request=request,
        name="backup.html",
        context=_ctx(request, user, "backup", {"status": status, "history": history}),
    )


@app.post("/admin/backup/settings")
def backup_save_settings(
    payload: schemas.BackupSettingsUpdate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    user = _require_admin(request, db)
    destinations = [d.model_dump() for d in payload.destinations]
    settings = backup_manager.save_settings(destinations)
    enabled = [d["label"] for d in settings["destinations"] if d.get("enabled")]
    log_action(db, user, "backup_settings_update", "backup", None,
               f"Yoqilgan manzillar: {', '.join(enabled) if enabled else 'yo\u2019q'}")
    return {"status": "ok", "settings": settings}


@app.post("/admin/backup/check-path")
def backup_check_path(
    payload: schemas.BackupPathCheck,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    _require_admin(request, db)
    result = backup_manager.check_path(payload.path)
    # ⬅️ TUZATILDI (Prompt 15.1): avval bu yerda "yo'l topilmadi" holati ham
    # 200 OK bilan {"exists": false, ...} sifatida qaytardi — chaqiruvchi
    # (frontend yoki API-consumer) buni faqat body'ni tekshirib aniqlashi
    # kerak edi. Talab aniq "400 Bad Request" deb belgilagan, shuning uchun
    # mavjud bo'lmagan/yozib bo'lmaydigan yo'l uchun endi HTTPException(400)
    # tashlanadi — backup_manager.check_path() o'zi 200/400'ni bilmaydi
    # (sof funksiya bo'lib qoladi), qaror shu yerda, HTTP qatlamida qabul
    # qilinadi.
    if not result.get("exists"):
        raise HTTPException(
            status_code=400,
            detail=result.get("message") or "Yo'l mavjud emas",
        )
    return result


@app.post("/admin/backup/create-path")
def backup_create_path(
    payload: schemas.BackupPathCheck,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    user = _require_admin(request, db)
    result = backup_manager.create_path(payload.path)
    if result.get("exists"):
        log_action(db, user, "backup_path_create", "backup", None,
                   f"Yaratilgan/tekshirilgan yo'l: {payload.path}")
    return result


@app.post("/admin/backup/run")
def backup_run_now(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    user = _require_admin(request, db)
    result = backup_manager.run_backup(trigger="manual", actor=user.username)
    if result.get("ok"):
        log_action(db, user, "backup_manual_run", "backup", None,
                   f"Fayl: {result.get('filename')}, hajmi: {result.get('size_bytes')} bayt")
    else:
        log_action(db, user, "backup_manual_run_failed", "backup", None,
                   f"Xato: {result.get('error')}")
    return result

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
    # ⬅️ YANGI: assistant_admin sahifani KO'RA oladi (talab: "xodimlarni
    # ko'rish va qo'shish"); tahrirlash/o'chirish tugmalari baribir
    # ishlamaydi — mos API endpointlar (PUT/DELETE) hamon admin-only
    # (modules/auth_module.py).
    if "users" not in ROLE_MODULE_ACCESS.get(user.role, {"dashboard"}):
        raise HTTPException(status_code=403, detail="Faqat admin uchun")

    # 🔒 FIX: `defer(models.User.password_hash)` — bu ustun SQL SELECT'ga
    # umuman kirmaydi (haqiqiy parol xeshi bazadan o'qilmaydi ham),
    # shuning uchun `User` obyekti (va uni ishlatuvchi `users.html`
    # shabloni) xotirada ham parol xeshini saqlamaydi — ilgari to'liq
    # qator (`password_hash` bilan birga) template'ga uzatilardi.
    users = (
        db.query(models.User)
        .options(defer(models.User.password_hash))
        .order_by(models.User.id)
        .all()
    )
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
    if "users" not in ROLE_MODULE_ACCESS.get(user.role, {"dashboard"}):
        raise HTTPException(status_code=403, detail="Faqat admin uchun")

    # 🔒 FIX: xuddi users_page'dagi kabi — password_hash bazadan
    # o'qilmaydi, shuning uchun `target` (va uning template'ga
    # uzatilishi) xotirada ham parol xeshini saqlamaydi.
    target = (
        db.query(models.User)
        .options(defer(models.User.password_hash))
        .filter(models.User.id == user_id)
        .first()
    )
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