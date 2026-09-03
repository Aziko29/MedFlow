# modules/patient_portal.py
"""
🧑‍🦱 FAZA 2 — Bemor portali: telefon + SMS-kod (OTP) bilan parolsiz login.

Bu modul xodimlar admin-paneli (main.py, boshqa modules/*.py) bilan HECH
QANDAY umumiy sessiya/state ULASHMAYDI:
  - Cookie:   PATIENT_SESSION_COOKIE_NAME ("mf_patient_session"), auth.py
              — SESSION_COOKIE_NAME ("cf_session") bilan aralashmaydi.
  - Shablon:  templates/portal/*.html — templates/base.html'dagi
              sidebar/menyu HTML tuzilmasi qayta ishlatilmaydi (faqat
              CSS custom property'lar, uslubiy izchillik uchun).

XAVFSIZLIK — MUHIM INVARIANT: /portal/dashboard, /portal/lab-results,
/portal/payments — HAR BIRI faqat require_patient() orqali sessiyadan
olingan `patient.id` bo'yicha filtrlaydi. Hech qanday route tashqi
(URL/query/form) patient_id qabul qilmaydi — bemor boshqa bemorning
yozuvlarini ko'ra olishining oldini olish shu tarzda ta'minlanadi.
"""
import datetime
import hashlib
import hmac
import logging
import re
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

import models
import schemas
from audit import log_action
from auth import (
    PATIENT_SESSION_COOKIE_NAME,
    PATIENT_SESSION_MAX_AGE_SECONDS,
    create_patient_session_token,
    get_current_patient,
    require_patient,
)
from crypto_fields import blind_index
from database import get_db
from eskiz_client import send_sms
from modules.auth_module import IS_PRODUCTION
from modules.lab_results import _parse_result_data
from rate_limiter import limiter

logger = logging.getLogger("medflow.patient_portal")

router = APIRouter(prefix="/portal", tags=["Patient Portal"])
templates = Jinja2Templates(directory="templates")

# ==============================================
# OTP KONFIGURATSIYASI
# ==============================================

OTP_LENGTH = 6
OTP_TTL_SECONDS = 5 * 60  # 5 daqiqa
MAX_VERIFY_ATTEMPTS = 5  # shu kod uchun ruxsat etilgan noto'g'ri urinishlar

# Telefon-darajasidagi (patient_id bo'yicha) qo'shimcha cheklov —
# rate_limiter.py'dagi IP-darajasidagi (slowapi) cheklovga IKKINCHI
# QATLAM sifatida qo'shiladi. Faqat bazada mavjud bemorlar uchun
# qo'llanadi (mavjud bo'lmagan raqam uchun OTP yozuvi umuman
# yaratilmaydi — FK talabi — shuning uchun bunday raqamlar faqat
# pastdagi IP-darajasidagi @limiter.limit bilan himoyalangan).
PHONE_MIN_INTERVAL_SECONDS = 60        # bitta raqam uchun daqiqada 1 marta
PHONE_MAX_PER_HOUR = 5                 # bitta raqam uchun soatiga 5 marta


def _ctx(request: Request, patient: Optional[models.Patient], extra: Optional[dict] = None) -> dict:
    base = {
        "request": request,
        "current_patient": patient,
        "year": datetime.datetime.now().year,
    }
    if extra:
        base.update(extra)
    return base


def _find_patient_by_phone(db: Session, phone_raw: str) -> Optional[models.Patient]:
    """patients.py/reminder_service.py bilan bir xil blind-index qidiruv
    naqshi: raqam ikki shaklda ham (bilan/siz "+") tekshiriladi, chunki
    ro'yxatdan o'tish paytida qanday formatda kiritilgani turlicha
    bo'lishi mumkin."""
    digits_only = re.sub(r"[^\d+]", "", (phone_raw or "").strip())
    if not digits_only:
        return None
    phone_norm = digits_only if digits_only.startswith("+") else f"+{digits_only}"

    bidx = blind_index(phone_norm)
    patient = db.query(models.Patient).filter(models.Patient.phone_bidx == bidx).first()
    if patient:
        return patient

    bidx_alt = blind_index(digits_only)
    return db.query(models.Patient).filter(models.Patient.phone_bidx == bidx_alt).first()


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _generate_code() -> str:
    # secrets.randbelow — kriptografik jihatdan xavfsiz tasodifiy son
    # (random.randint EMAS — OTP kabi xavfsizlik-muhim qiymatlar uchun
    # doim `secrets` moduli ishlatiladi).
    return f"{secrets.randbelow(10 ** OTP_LENGTH):0{OTP_LENGTH}d}"


def _phone_rate_limited(db: Session, patient_id: int) -> bool:
    """True — shu bemor (patient_id) uchun so'nggi 1 daqiqada/1 soatda
    ruxsat etilgan urinishlar soni allaqachon to'lgan, yangi kod
    yubormaslik kerak."""
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    recent = (
        db.query(models.PatientLoginOTP)
        .filter(models.PatientLoginOTP.patient_id == patient_id)
        .filter(models.PatientLoginOTP.created_at >= now - datetime.timedelta(hours=1))
        .all()
    )
    if len(recent) >= PHONE_MAX_PER_HOUR:
        return True
    if any(
        (r.created_at or now) >= now - datetime.timedelta(seconds=PHONE_MIN_INTERVAL_SECONDS)
        for r in recent
    ):
        return True
    return False


def _set_patient_cookie(response, patient_id: int) -> None:
    token = create_patient_session_token(patient_id)
    response.set_cookie(
        key=PATIENT_SESSION_COOKIE_NAME,
        value=token,
        max_age=PATIENT_SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=IS_PRODUCTION,
    )


# ==============================================
# GET /portal/login — telefon raqami kiritish formasi
# ==============================================
@router.get("/login", response_class=HTMLResponse)
def portal_login_page(request: Request, db: Session = Depends(get_db)):
    patient = get_current_patient(request.cookies.get(PATIENT_SESSION_COOKIE_NAME), db)
    if patient:
        return RedirectResponse(url="/portal/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request=request, name="portal/login.html", context=_ctx(request, None)
    )


# ==============================================
# POST /portal/login/request-code
# ==============================================
@router.post("/login/request-code", response_model=schemas.PatientPortalCodeResponse)
@limiter.limit("10/hour")  # IP-darajasidagi tashqi himoya qatlami (defense-in-depth)
def request_login_code(
    request: Request,
    payload: schemas.PatientPortalCodeRequest,
    db: Session = Depends(get_db),
):
    """Har doim (topilsa ham, topilmasa ham) BIR XIL neytral javob
    qaytaradi — telefon raqami tizimda ro'yxatdan o'tganmi yoki yo'qmi
    javobdan bilinmasligi kerak (enumeration hujumidan himoya)."""
    patient = _find_patient_by_phone(db, payload.phone)

    if patient is not None:
        if _phone_rate_limited(db, patient.id):
            # Haddan tashqari ko'p urinish — baribir NEYTRAL javob
            # qaytaramiz (aks holda javob vaqti/mazmuni orqali raqam
            # mavjudligini bilib olish mumkin bo'lardi). Kod shunchaki
            # yuborilmaydi.
            logger.info("Bemor OTP so'rovi rate-limit qilindi: patient_id=%s", patient.id)
        else:
            code = _generate_code()
            now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            otp = models.PatientLoginOTP(
                patient_id=patient.id,
                code_hash=_hash_code(code),
                created_at=now,
                expires_at=now + datetime.timedelta(seconds=OTP_TTL_SECONDS),
            )
            db.add(otp)
            db.commit()

            send_sms(
                payload.phone,
                f"MedFlow: sizning tasdiqlash kodingiz — {code}. Kod {OTP_TTL_SECONDS // 60} "
                f"daqiqa amal qiladi. Kodni hech kimga aytmang.",
            )
    else:
        logger.info("Bemor OTP so'rovi — mos bemor topilmadi (enumeration himoyasi: jim o'tiladi)")

    return schemas.PatientPortalCodeResponse()


# ==============================================
# POST /portal/login/verify
# ==============================================
@router.post("/login/verify")
@limiter.limit("10/hour")
def verify_login_code(
    request: Request,
    payload: schemas.PatientPortalVerifyRequest,
    db: Session = Depends(get_db),
):
    generic_error = JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": "Kod noto'g'ri yoki muddati tugagan."},
    )

    patient = _find_patient_by_phone(db, payload.phone)
    if patient is None:
        # Bemor topilmadi — baribir "kod noto'g'ri" (enumeration himoyasi).
        return generic_error

    otp = (
        db.query(models.PatientLoginOTP)
        .filter(models.PatientLoginOTP.patient_id == patient.id)
        .filter(models.PatientLoginOTP.used_at.is_(None))
        .order_by(models.PatientLoginOTP.created_at.desc())
        .first()
    )
    if otp is None:
        return generic_error

    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    if otp.expires_at < now:
        return generic_error

    if otp.attempt_count >= MAX_VERIFY_ATTEMPTS:
        # Bu yerda (patient topildi + OTP mavjud + muddati o'tmagan)
        # generic_error dan FARQLI, aniq xabar qaytariladi — bu
        # enumeration himoyasini buzmaydi, chunki shu tarmoqqa faqat
        # haqiqiy bemor uchun, allaqachon MAX_VERIFY_ATTEMPTS marta
        # (noto'g'ri kod bilan) urinilgandan keyingina yetib boriladi;
        # "bu telefon raqami tizimda bormi" degan savolga hech qanday
        # yangi ma'lumot bermaydi. Maqsad — foydalanuvchiga nima
        # bo'layotganini aniq tushuntirish (yangi kod so'rashi kerak),
        # eski xulq-atvordagi kabi umumiy "kod noto'g'ri" xabari bilan
        # chalkashtirmaslik.
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Urinishlar soni tugadi, yangi kod so'rang."},
        )

    if not hmac.compare_digest(_hash_code(payload.code.strip()), otp.code_hash):
        otp.attempt_count += 1
        db.commit()
        return generic_error

    # ✅ To'g'ri kod
    otp.used_at = now
    db.commit()

    result = JSONResponse(content={"status": "ok"})
    _set_patient_cookie(result, patient.id)

    log_action(
        db, None, "patient.login", "Patient", patient.id,
        f"Bemor portaliga SMS-kod orqali kirdi (patient_id={patient.id}).",
    )

    return result


# ==============================================
# POST /portal/logout
# ==============================================
@router.post("/logout")
def portal_logout():
    result = JSONResponse(content={"status": "ok"})
    # ⬅️ TUZATILDI (session-cookie xavfsizligi, 15.2-band): set_cookie()'da
    # ishlatilgan httponly/samesite/secure bilan BIR XIL parametrlar bilan
    # o'chirilishi kerak — aks holda ba'zi brauzerlarda cookie o'chmay qolishi
    # mumkin (qarang: modules/auth_module.py'dagi xuddi shu tuzatish).
    result.delete_cookie(
        key=PATIENT_SESSION_COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=IS_PRODUCTION,
    )
    return result


# ==============================================
# GET /portal/dashboard — bemorning o'z navbatlari
# ==============================================
@router.get("/dashboard", response_class=HTMLResponse)
def portal_dashboard(
    request: Request,
    patient: models.Patient = Depends(require_patient),
    db: Session = Depends(get_db),
):
    appointments = (
        db.query(models.Appointment)
        .options(joinedload(models.Appointment.doctor))
        .filter(models.Appointment.patient_id == patient.id)
        .order_by(models.Appointment.scheduled_time.desc())
        .all()
    )
    return templates.TemplateResponse(
        request=request,
        name="portal/dashboard.html",
        context=_ctx(request, patient, {"appointments": appointments}),
    )


# ==============================================
# GET /portal/lab-results — FAQAT shu bemorga tegishli tahlillar
# ==============================================
@router.get("/lab-results", response_class=HTMLResponse)
def portal_lab_results(
    request: Request,
    patient: models.Patient = Depends(require_patient),
    db: Session = Depends(get_db),
):
    results = (
        db.query(models.LabResult)
        .filter(models.LabResult.patient_id == patient.id)
        .order_by(models.LabResult.created_at.desc())
        .all()
    )
    rows = []
    for r in results:
        parsed = _parse_result_data(r.result_data)
        rows.append({
            "obj": r,
            "parsed": parsed,
            "raw_text": None if parsed else r.result_data,
        })
    return templates.TemplateResponse(
        request=request,
        name="portal/lab_results.html",
        context=_ctx(request, patient, {"rows": rows}),
    )


# ==============================================
# GET /portal/payments — FAQAT shu bemorga tegishli to'lovlar
# ==============================================
@router.get("/payments", response_class=HTMLResponse)
def portal_payments(
    request: Request,
    patient: models.Patient = Depends(require_patient),
    db: Session = Depends(get_db),
):
    payments = (
        db.query(models.Payment)
        .options(joinedload(models.Payment.appointment))
        .filter(models.Payment.patient_id == patient.id)
        .order_by(models.Payment.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        request=request,
        name="portal/payments.html",
        context=_ctx(request, patient, {"payments": payments}),
    )


# MedFlow dvigateli uchun majburiy ro'yxatdan o'tkazish funksiyasi
def register_module():
    return {
        "module_name": "Patient Portal",
        "version": "1.0.0",
        "router": router,
    }
