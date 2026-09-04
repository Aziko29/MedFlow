# modules/settings_module.py
"""
⚙️ Sozlamalar moduli (Prompt 12.1) — profil, parol, klinika va tizim
sozlamalari, hamda 2-qatlam parol bilan himoyalangan xavfli amallar.

Ruxsatlar:
  - GET /settings                 — istalgan tizimga kirgan xodim (o'z
                                     profilini ko'rish uchun).
  - PUT /settings/profile         — istalgan xodim, FAQAT O'ZINING
                                     profilini (get_current_user orqali
                                     aniqlangan foydalanuvchi).
  - PUT /settings/password        — istalgan xodim, FAQAT O'ZINING
                                     parolini; ichida
                                     modules/auth_module.change_password
                                     bilan BIR XIL biznes-qoidalarga
                                     tayanadi (eski parolni tekshirish,
                                     SELF_PASSWORD_CHANGE_LIMIT) — mantiq
                                     ikki joyda ikki xil yozilib
                                     qolmasligi uchun o'sha funksiya
                                     to'g'ridan-to'g'ri chaqiriladi.
  - PUT /settings/clinic          — FAQAT admin (require_role("admin")).
  - PUT /settings/system          — FAQAT admin (require_role("admin")).
  - POST /settings/dangerous-action — FAQAT admin + qo'shimcha 2-qatlam
                                     parol (CLINICFLOW_ADMIN_ACTIONS_PASSWORD).

Saqlash naqshi (admin_profile.py bilan bir xil): ClinicSettingsUpdate
va SystemSettingsUpdate uchun bitta-qator (singleton) jadvallar
ishlatiladi — get-or-create yordamchi funksiyalar orqali.
"""
import hmac
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from rate_limiter import limiter
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

import models
import schemas
from audit import log_action
from auth import (
    clear_user_cache,
    get_current_user,
    get_current_user_optional,
    require_role,
)
from database import get_db
from modules.auth_module import change_password as _change_password_impl

logger = logging.getLogger("clinicflow.settings")

router = APIRouter(prefix="/settings", tags=["Sozlamalar"])
templates = Jinja2Templates(directory="templates")


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


# ── Get-or-create yordamchilar (AdminProfileSettings/GovIntegrationSettings
#    bilan bir xil naqsh — qarang modules/admin_profile.py) ─────────────
def _get_clinic_settings(db: Session) -> models.AdminProfileSettings:
    settings = db.query(models.AdminProfileSettings).first()
    if settings is None:
        settings = models.AdminProfileSettings(positions=[], departments=[])
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def _get_system_settings(db: Session) -> models.SystemSettings:
    settings = db.query(models.SystemSettings).first()
    if settings is None:
        settings = models.SystemSettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def _clinic_settings_read(settings: models.AdminProfileSettings) -> schemas.ClinicSettingsRead:
    return schemas.ClinicSettingsRead(
        name=settings.clinic_name,
        address=settings.address,
        phone=settings.phone,
        working_hours=settings.working_hours,
        queue_interval_minutes=settings.queue_interval_minutes,
    )


# ── GET /settings — sahifa ──────────────────────────────────────────────
@router.get("", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    if user is None:
        return RedirectResponse(url="/login")

    changes_used = user.self_password_change_count
    changes_remaining = max(models.SELF_PASSWORD_CHANGE_LIMIT - changes_used, 0)

    clinic_settings_ctx = None
    system_settings_ctx = None
    if user.role == "admin":
        clinic_settings_ctx = _clinic_settings_read(_get_clinic_settings(db))
        system_settings_ctx = schemas.SystemSettingsRead.model_validate(_get_system_settings(db))

    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=_ctx(
            request, user, "settings",
            {
                "profile": schemas.ProfileRead.model_validate(user),
                "password_limit": models.SELF_PASSWORD_CHANGE_LIMIT,
                "changes_used": changes_used,
                "changes_remaining": changes_remaining,
                "limit_reached": changes_remaining == 0,
                "clinic_settings": clinic_settings_ctx,
                "system_settings": system_settings_ctx,
                "dangerous_action_types": models.DANGEROUS_ACTION_TYPES,
            },
        ),
    )


# ── PUT /settings/profile ────────────────────────────────────────────────
@router.put("/profile", response_model=schemas.ProfileRead)
def update_profile(
    payload: schemas.ProfileUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> models.User:
    """Faqat kiritilgan (None bo'lmagan) maydonlarni yangilaydi —
    fullname/username/role bu yerdan o'zgartirilmaydi."""
    updates = payload.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in updates.items():
        setattr(user, field, value)

    if updates:
        db.commit()
        db.refresh(user)
        clear_user_cache(user.username)
        log_action(
            db, user, "user.profile_update", "User", user.id,
            f"o'zgargan maydonlar: {', '.join(sorted(updates.keys()))}",
        )
    return user


# ── PUT /settings/password ───────────────────────────────────────────────
@router.put("/password", response_model=schemas.ChangePasswordResult)
def update_password(
    payload: schemas.PasswordChange,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> schemas.ChangePasswordResult:
    """confirm_password mosligini schemas.PasswordChange allaqachon
    tekshirgan (server tomonida, faqat front-end JS'ga ishonib
    qolinmaydi). Qolgan barcha biznes-qoidalar (eski parolni tekshirish,
    SELF_PASSWORD_CHANGE_LIMIT, keshni tozalash, audit) uchun
    modules/auth_module.change_password BEVOSITA chaqiriladi — shu bilan
    /api/auth/change-password va /settings/password ikkalasi ham AYNAN
    bitta joyda saqlanadigan mantiqqa tayanadi."""
    inner_payload = schemas.ChangePasswordRequest(
        old_password=payload.old_password,
        new_password=payload.new_password,
    )
    return _change_password_impl(payload=inner_payload, db=db, user=user)


# ── PUT /settings/clinic — faqat admin ───────────────────────────────────
@router.put(
    "/clinic",
    response_model=schemas.ClinicSettingsRead,
    dependencies=[Depends(require_role("admin"))],
)
def update_clinic_settings(
    payload: schemas.ClinicSettingsUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> schemas.ClinicSettingsRead:
    settings = _get_clinic_settings(db)
    settings.clinic_name = payload.name
    settings.address = payload.address
    settings.phone = payload.phone
    settings.working_hours = payload.working_hours
    settings.queue_interval_minutes = payload.queue_interval_minutes
    settings.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    db.refresh(settings)

    log_action(
        db, user, "settings.clinic_update", "AdminProfileSettings", settings.id,
        f"name={settings.clinic_name}, queue_interval={settings.queue_interval_minutes}",
    )
    return _clinic_settings_read(settings)


# ── PUT /settings/system — faqat admin ───────────────────────────────────
@router.put(
    "/system",
    response_model=schemas.SystemSettingsRead,
    dependencies=[Depends(require_role("admin"))],
)
def update_system_settings(
    payload: schemas.SystemSettingsUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> models.SystemSettings:
    """Prompt 18: session_timeout_minutes endi auth.py bilan haqiqatan
    ulangan (qarang models.SystemSettings docstring) — bu yerda
    saqlangan qiymat keyingi so'rovlardan boshlab amal qiladi.
    max_login_attempts hamon FAQAT saqlanadi/ko'rsatiladi — login
    urinish cheklovi bilan hali ulanmagan (alohida keyingi bosqich)."""
    settings = _get_system_settings(db)
    settings.timezone = payload.timezone
    settings.date_format = payload.date_format
    settings.session_timeout_minutes = payload.session_timeout_minutes
    settings.max_login_attempts = payload.max_login_attempts
    settings.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    db.refresh(settings)

    log_action(
        db, user, "settings.system_update", "SystemSettings", settings.id,
        f"timezone={settings.timezone}, session_timeout={settings.session_timeout_minutes}min",
    )
    return settings


# ── POST /settings/dangerous-action — 2-qatlam parol ─────────────────────
def _handle_clear_db(db: Session, user: models.User) -> str:
    """STUB: bazani to'liq tozalash. Ataylab HAQIQIY o'chirish
    bajarilmaydi — bu son-og'riqli/qaytarib bo'lmas amal, haqiqiy
    bajarilishi alohida, ehtiyotkorlik bilan (masalan avtomatik backup +
    ikkinchi tasdiqlash bosqichi bilan) ulanishi kerak. Hozircha faqat
    audit yozuvi qoldiradi.
    """
    return "clear_db — stub bajarildi (haqiqiy o'chirish hali ulanmagan)."


def _handle_delete_all_patients(db: Session, user: models.User) -> str:
    """STUB — sababi yuqoridagi _handle_clear_db bilan bir xil."""
    return "delete_all_patients — stub bajarildi (haqiqiy o'chirish hali ulanmagan)."


def _handle_restart_system(db: Session, user: models.User) -> str:
    """STUB — tizimni qayta ishga tushirish uchun deploy/process-manager
    darajasidagi integratsiya kerak (masalan systemd/supervisor orqali),
    bu ilova jarayonining o'zidan xavfsiz bajarilmaydi."""
    return "restart_system — stub bajarildi (haqiqiy qayta ishga tushirish hali ulanmagan)."


def _handle_reset_sessions(db: Session, user: models.User) -> str:
    """Haqiqiy amal: foydalanuvchi keshini (auth.py get_user_cached)
    to'liq tozalaydi — shundan so'ng har bir so'rov yangilangan
    parol/rol ma'lumotini bazadan qayta o'qiydi. DIQQAT: bu FAOL cookie
    sessiyalarini bekor qilmaydi (ular auth.py'da imzolangan, statless
    token — server tomonida bekor qilish ro'yxati yo'q), faqat kesh
    tozalanadi. Haqiqiy "hammani chiqarib yuborish" uchun
    CLINICFLOW_SECRET_KEY'ni almashtirish kerak bo'lardi — bu ESA
    joriy admin sessiyasini ham bekor qilib, deploy'siz amalga
    oshirib bo'lmaydigan yon ta'sirga ega, shuning uchun bu yerda
    bajarilmaydi."""
    clear_user_cache()
    return "reset_sessions — foydalanuvchi keshi tozalandi."


_DANGEROUS_ACTION_HANDLERS = {
    "clear_db": _handle_clear_db,
    "delete_all_patients": _handle_delete_all_patients,
    "restart_system": _handle_restart_system,
    "reset_sessions": _handle_reset_sessions,
}


@router.post(
    "/dangerous-action",
    response_model=schemas.DangerousActionResult,
    dependencies=[Depends(require_role("admin"))],
)
@limiter.limit("5/minute")  # ⬅️ YANGI: 2-qatlam parolni brute-force qilishning oldini olish
def perform_dangerous_action(
    request: Request,
    payload: schemas.DangerousActionRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> schemas.DangerousActionResult:
    """2-qatlam parol bilan himoyalangan xavfli amallar.

    MUHIM: bu yerdagi confirmation_password foydalanuvchining login
    paroli EMAS — .env'dagi CLINICFLOW_ADMIN_ACTIONS_PASSWORD bilan
    solishtiriladigan, faqat shu turdagi amallar uchun mo'ljallangan
    ALOHIDA parol (2-qatlam himoya: hisobni bilish yetarli emas, bu
    qo'shimcha maxfiy qiymatni ham bilish kerak).
    """
    # require_role("admin") dependency allaqachon rol!="admin" holatini
    # 403 bilan rad etadi — bu yerdagi qo'shimcha tekshiruv talab #1
    # ("User roli admin ekanligi tekshirilsin") ni ANIQ, ikki qatlamli
    # qilib ko'rsatish uchun qoldirilgan (defense-in-depth).
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Bu amal faqat admin uchun")

    admin_actions_password = os.environ.get("CLINICFLOW_ADMIN_ACTIONS_PASSWORD")
    if not admin_actions_password:
        logger.error(
            "CLINICFLOW_ADMIN_ACTIONS_PASSWORD sozlanmagan — xavfli amallar bloklandi."
        )
        raise HTTPException(
            status_code=500,
            detail="Server sozlamasi to'liq emas: CLINICFLOW_ADMIN_ACTIONS_PASSWORD o'rnatilmagan",
        )

    # hmac.compare_digest — vaqt-hujumiga (timing attack) qarshi doimiy
    # vaqtli solishtirish, auth.py'dagi boshqa parol/imzo tekshiruvlari
    # bilan bir xil naqsh.
    if not hmac.compare_digest(payload.confirmation_password, admin_actions_password):
        log_action(
            db, user, "settings.dangerous_action_denied", "User", user.id,
            f"action_type={payload.action_type} — noto'g'ri tasdiqlash paroli",
        )
        raise HTTPException(status_code=403, detail="Tasdiqlash paroli noto'g'ri")

    handler = _DANGEROUS_ACTION_HANDLERS.get(payload.action_type)
    if handler is None:
        # schemas.DangerousActionRequest allaqachon action_type'ni
        # DANGEROUS_ACTION_TYPES bilan tekshiradi, shuning uchun bu
        # amalda yetib bo'lmaydigan holat — himoya sifatida qoldirilgan.
        raise HTTPException(status_code=400, detail="Noma'lum action_type")

    message = handler(db, user)

    logger.warning(
        f"⚠️ Xavfli amal bajarildi: {payload.action_type} — admin: {user.username}"
    )
    log_action(
        db, user, "settings.dangerous_action", "User", user.id,
        f"action_type={payload.action_type}",
    )

    return schemas.DangerousActionResult(
        action_type=payload.action_type,
        message=message,
    )


def register_module() -> Dict[str, object]:
    return {
        "module_name": "Settings",
        "version": "1.0.0",
        "router": router,
    }
