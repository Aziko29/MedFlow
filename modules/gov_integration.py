# modules/gov_integration.py
"""
🏛️ Prompt 10 — Davlat identifikatsiya tizimi (OneID / DAVLAT RO'YXATI)
integratsiyasi.

Ikkita mustaqil ruxsat darajasi bor, shuning uchun bu faylda IKKITA
alohida APIRouter e'lon qilinadi va register_module() ularni bitta
"wrapper" routerga birlashtiradi (ClinicFlowEngine bitta modul uchun
faqat bitta "router" kaliti kutadi — qarang main.py'dagi
ClinicFlowEngine.auto_discover_modules):

  1. `admin_router` (/api/admin/gov-integration/settings)
     — FAQAT admin (require_role("admin")): sozlamalarni ko'rish/tahrirlash.
  2. `patient_router` (/api/patients/verify, /api/patients/register-with-gov)
     — admin + reception (modules/patients.py'dagi add_patient bilan bir
       xil ruxsat darajasi — bemor qo'shish huquqi bo'lgan xodim
       tekshirish/ro'yxatdan o'tkazishni ham qila oladi).

Mock/Stub rejimi (talab #5):
    GovIntegrationSettings.is_enabled=False bo'lsa (standart holat —
    hech qanday real OneID shartnomasi/spetsifikatsiyasi hali yo'q),
    _call_gov_api() hech qachon tashqi tarmoqqa chiqmaydi — faqat
    berilgan PINFL/pasport asosida DETERMINISTIK, ochiqchasiga
    "mock" deb belgilangan test ma'lumotini qaytaradi. is_enabled=True
    bo'lganda (admin haqiqiy api_url/api_key/api_secret kiritgandan
    keyin) https:// orqali haqiqiy so'rov yuboriladi.

Xavfsizlik (talab #4):
    - api_key/api_secret hech qachon logga yozilmaydi va hech qachon
      API javobida (GovIntegrationSettingsResponse) ochiq qaytarilmaydi.
    - Bemorning PINFL/pasport ma'lumotlari ham logga yozilmaydi (faqat
      audit.log_action orqali "kim, qachon, qaysi bemor uchun" — hech
      qanday PII qiymatisiz, boshqa audit yozuvlari bilan bir xil uslub).
    - GovIntegrationSettingsBase.api_url validatori https:// bo'lmagan
      manzillarni butunlay rad etadi (SSL/TLS majburiy).
"""
import datetime
import hashlib
import logging
from typing import Dict, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models
import schemas
from audit import log_action
from auth import get_current_user, require_role
from crypto_fields import blind_index
from database import get_db

logger = logging.getLogger("medflow.gov_integration")

admin_router = APIRouter(
    prefix="/api/admin/gov-integration",
    tags=["Admin / Davlat integratsiyasi"],
    dependencies=[Depends(require_role("admin"))],
)

patient_router = APIRouter(
    prefix="/api/patients",
    tags=["Patients / Davlat tizimi tekshiruvi"],
    dependencies=[Depends(require_role("admin", "reception"))],
)


# ── Yordamchi funksiyalar ────────────────────────────────────────────
def _get_settings(db: Session) -> models.GovIntegrationSettings:
    """Yagona sozlamalar qatorini oladi, mavjud bo'lmasa yaratadi
    (AdminProfileSettings bilan bir xil get-or-create naqsh). Standart
    holatda is_enabled=False — mock rejim."""
    settings = db.query(models.GovIntegrationSettings).first()
    if settings is None:
        settings = models.GovIntegrationSettings(
            integration_name="OneID",
            is_enabled=False,
            created_at=datetime.datetime.utcnow(),
        )
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def _settings_to_response(settings: models.GovIntegrationSettings) -> schemas.GovIntegrationSettingsResponse:
    return schemas.GovIntegrationSettingsResponse(
        id=settings.id,
        integration_name=settings.integration_name,
        is_enabled=settings.is_enabled,
        api_url=settings.api_url,
        organization_id=settings.organization_id,
        api_key_configured=bool(settings.api_key),
        api_secret_configured=bool(settings.api_secret),
        created_at=settings.created_at,
        updated_at=settings.updated_at,
    )


def _mock_verify(passport_series: Optional[str], passport_number: Optional[str], pinfl: Optional[str]) -> schemas.PatientVerifyResponse:
    """Mock/stub rejim (is_enabled=False yoki real API sozlanmagan):
    berilgan identifikatorlar asosida DETERMINISTIK (bir xil kirish —
    bir xil chiqish) soxta ma'lumot qaytaradi, hech qanday tashqi
    so'rov yubormaydi. Faqat test/dev maqsadida — response.source
    hamisha "mock" deb ochiq belgilanadi."""
    seed_source = f"{passport_series or ''}{passport_number or ''}{pinfl or ''}"
    digest = hashlib.sha256(seed_source.encode("utf-8")).hexdigest()
    seed_int = int(digest[:8], 16)

    first_names = ["Alisher", "Dilnoza", "Bekzod", "Madina", "Sardor", "Nilufar"]
    last_names = ["Karimov", "Yusupova", "Rashidov", "Tosheva", "Nazarov", "Aliyeva"]
    regions = [
        "Toshkent shahri, Chilonzor tumani",
        "Samarqand viloyati, Samarqand shahri",
        "Farg'ona viloyati, Farg'ona shahri",
        "Buxoro viloyati, Buxoro shahri",
    ]

    fullname = f"{last_names[seed_int % len(last_names)]} {first_names[seed_int % len(first_names)]}"
    address = regions[seed_int % len(regions)]

    # PINFL berilgan bo'lsa, undagi 7-12 pozitsiyalar (YYMMDD) orqali
    # tug'ilgan sanani hisoblashga urinib ko'ramiz — muvaffaqiyatsiz
    # bo'lsa (format mos kelmasa), seed asosida taxminiy sana ishlatiladi.
    birth_date = None
    if pinfl and len(pinfl) == 14 and pinfl[5:11].isdigit():
        try:
            yy, mm, dd = int(pinfl[5:7]), int(pinfl[7:9]), int(pinfl[9:11])
            year = 2000 + yy if yy < 30 else 1900 + yy
            birth_date = datetime.date(year, max(mm, 1), max(dd, 1))
        except ValueError:
            birth_date = None
    if birth_date is None:
        birth_date = datetime.date(1980 + (seed_int % 40), 1 + (seed_int % 12), 1 + (seed_int % 28))

    return schemas.PatientVerifyResponse(
        fullname=fullname,
        birth_date=birth_date,
        address=address,
        gender="M" if seed_int % 2 == 0 else "F",
        pinfl=pinfl or f"3{digest[:13]}".ljust(14, "0")[:14],
        passport_series=(passport_series or "AA").upper(),
        passport_number=passport_number or digest[:7],
        source="mock",
    )


def _call_real_gov_api(
    settings: models.GovIntegrationSettings,
    passport_series: Optional[str],
    passport_number: Optional[str],
    pinfl: Optional[str],
) -> schemas.PatientVerifyResponse:
    """Haqiqiy davlat tizimiga (OneID va h.k.) so'rov. Faqat
    is_enabled=True VA api_url/api_key to'liq sozlangan bo'lsa
    chaqiriladi. SSL/TLS majburiy — api_url https:// bo'lishi allaqachon
    GovIntegrationSettingsBase validatorida tekshirilgan.

    MUHIM: so'rov tanasi (PINFL/pasport) va javob HECH QACHON logga
    yozilmaydi — faqat tarmoq xatosi/HTTP status kodi logga tushadi.
    """
    payload = {
        "organization_id": settings.organization_id,
        "passport_series": passport_series,
        "passport_number": passport_number,
        "pinfl": pinfl,
    }
    headers = {
        "Authorization": f"Bearer {settings.api_key}",
        "X-Api-Secret": settings.api_secret,
        "Content-Type": "application/json",
    }
    try:
        resp = httpx.post(settings.api_url, json=payload, headers=headers, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        # Xato turi va status kodi logga yoziladi, lekin so'rov/javob
        # tanasi (shaxsiy ma'lumot bo'lishi mumkin) YO'Q.
        logger.error("Davlat tizimi (%s) so'rovi muvaffaqiyatsiz: %s", settings.integration_name, type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="Davlat identifikatsiya tizimiga ulanib bo'lmadi. Birozdan so'ng qayta urinib ko'ring.",
        ) from exc

    try:
        return schemas.PatientVerifyResponse(
            fullname=data["fullname"],
            birth_date=data["birth_date"],
            address=data["address"],
            gender=data.get("gender"),
            pinfl=data.get("pinfl", pinfl or ""),
            passport_series=data.get("passport_series", passport_series or ""),
            passport_number=data.get("passport_number", passport_number or ""),
            source="gov_api",
        )
    except (KeyError, TypeError) as exc:
        logger.error("Davlat tizimi javobi kutilgan formatda emas (%s)", settings.integration_name)
        raise HTTPException(
            status_code=502, detail="Davlat tizimidan kelgan javob formati noto'g'ri.",
        ) from exc


def _verify_via_gov(db: Session, request: schemas.PatientVerifyRequest) -> schemas.PatientVerifyResponse:
    settings = _get_settings(db)
    has_gov_config = bool(settings.is_enabled and settings.api_url and settings.api_key)
    if has_gov_config:
        return _call_real_gov_api(
            settings, request.passport_series, request.passport_number, request.pinfl
        )
    return _mock_verify(request.passport_series, request.passport_number, request.pinfl)


# ══════════════════════════════════════════════════════════════════
# Admin — sozlamalar
# ══════════════════════════════════════════════════════════════════
@admin_router.get("/settings", response_model=schemas.GovIntegrationSettingsResponse)
def get_gov_integration_settings(db: Session = Depends(get_db)) -> schemas.GovIntegrationSettingsResponse:
    return _settings_to_response(_get_settings(db))


@admin_router.put("/settings", response_model=schemas.GovIntegrationSettingsResponse)
def update_gov_integration_settings(
    settings_data: schemas.GovIntegrationSettingsUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> schemas.GovIntegrationSettingsResponse:
    settings = _get_settings(db)

    settings.integration_name = settings_data.integration_name
    settings.is_enabled = settings_data.is_enabled
    settings.api_url = settings_data.api_url
    settings.organization_id = settings_data.organization_id

    # api_key/api_secret: faqat berilgan bo'lsa yangilanadi — bo'sh
    # qoldirilsa (None), admin har PUT'da qayta kiritishga majbur emas,
    # oldingi shifrlangan qiymat saqlanadi.
    if settings_data.api_key is not None:
        settings.api_key = settings_data.api_key or None
    if settings_data.api_secret is not None:
        settings.api_secret = settings_data.api_secret or None

    if settings.is_enabled and not (settings.api_url and settings.api_key):
        raise HTTPException(
            status_code=400,
            detail="Integratsiyani yoqish uchun avval api_url va api_key kiritilishi kerak",
        )

    settings.updated_at = datetime.datetime.utcnow()
    db.commit()
    db.refresh(settings)

    # PII/kalitlar YO'Q — faqat qaysi sozlama o'zgargani va kim tomonidan.
    log_action(
        db, user, "gov_integration.settings_update", "GovIntegrationSettings", settings.id,
        f"integration_name={settings.integration_name}, is_enabled={settings.is_enabled}",
    )
    return _settings_to_response(settings)


# ══════════════════════════════════════════════════════════════════
# Bemorni davlat tizimi orqali tekshirish / ro'yxatdan o'tkazish
# ══════════════════════════════════════════════════════════════════
@patient_router.post("/verify", response_model=schemas.PatientVerifyResponse)
def verify_patient(
    request: schemas.PatientVerifyRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> schemas.PatientVerifyResponse:
    if not request.pinfl and not (request.passport_series and request.passport_number):
        raise HTTPException(
            status_code=422,
            detail="PINFL yoki pasport seriya+raqami (ikkalasi ham) kiritilishi shart",
        )

    result = _verify_via_gov(db, request)

    # Audit: PII yo'q, faqat "kim tekshirdi va manba (mock/gov_api)".
    log_action(db, user, "gov_integration.patient_verify", "Patient", None, f"source={result.source}")
    return result


@patient_router.post("/register-with-gov", response_model=schemas.PatientRead, status_code=201)
def register_patient_with_gov(
    request: schemas.PatientRegisterWithGovRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> models.Patient:
    if not request.pinfl and not (request.passport_series and request.passport_number):
        raise HTTPException(
            status_code=422,
            detail="PINFL yoki pasport seriya+raqami (ikkalasi ham) kiritilishi shart",
        )

    verify_request = schemas.PatientVerifyRequest(
        passport_series=request.passport_series,
        passport_number=request.passport_number,
        pinfl=request.pinfl,
    )
    gov_data = _verify_via_gov(db, verify_request)

    # Bir xil PINFL bilan ikkinchi marta ro'yxatdan o'tkazishga urinish —
    # blind index orqali oldindan aniq tekshiruv (IntegrityError'gacha
    # yetib bormasdan, aniqroq xabar bilan).
    pinfl_bidx = blind_index(gov_data.pinfl) if gov_data.pinfl else None
    if pinfl_bidx is not None:
        existing = db.query(models.Patient).filter(models.Patient.pinfl_bidx == pinfl_bidx).first()
        if existing is not None:
            raise HTTPException(
                status_code=409, detail="Bu PINFL bilan bemor allaqachon ro'yxatdan o'tgan"
            )

    new_patient = models.Patient(
        fullname=gov_data.fullname,
        phone=request.phone,
        gender=gov_data.gender,
        birth_date=gov_data.birth_date,
        address=gov_data.address,
        blood_type=request.blood_type,
        emergency_contact_name=request.emergency_contact_name,
        emergency_contact_phone=request.emergency_contact_phone,
        pinfl=gov_data.pinfl,
        passport_series=gov_data.passport_series,
        passport_number=gov_data.passport_number,
        is_verified=True,
    )
    db.add(new_patient)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Bu telefon raqami yoki PINFL allaqachon ro'yxatdan o'tgan",
        )
    db.refresh(new_patient)

    log_action(
        db, user, "gov_integration.patient_register", "Patient", new_patient.id,
        f"source={gov_data.source}, fullname={new_patient.fullname}",
    )
    return new_patient


def register_module() -> Dict[str, object]:
    wrapper = APIRouter()
    wrapper.include_router(admin_router)
    wrapper.include_router(patient_router)
    return {
        "module_name": "GovIntegration",
        "version": "1.0.0",
        "router": wrapper,
    }
