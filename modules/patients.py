# modules/patients.py
"""
Patient registration, listing, lookup, edit, delete + per-patient
financial summary and history (fixes audit point #6: "island pages" —
you can now open one patient and see every appointment + payment they
have, instead of manually cross-referencing three separate tables).

Route ordering matters here: static/action paths (/add, /list) are
declared BEFORE the dynamic /{patient_id} path parameter, so FastAPI
never mistakes "/add" for a patient_id.
"""
import datetime
import io
import os
import time
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from PIL import Image, UnidentifiedImageError
from sqlalchemy import false as sa_false
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query as SAQuery, Session, joinedload

import models
import schemas
from audit import log_action
from auth import get_current_user, require_role
from database import get_db
from model_utils import apply_update

# 🖼️ Bemor rasmi — yuklash cheklovlari va saqlash joyi.
# Loyiha ildizi: bu fayl <root>/modules/patients.py'da yotadi, shuning
# uchun ikki qavat yuqoriga chiqib <root>/static/uploads/patients'ga
# yetamiz (main.py'dagi StaticFiles("/static") shu "static" papkani ochiq
# qiladi, demak natijaviy rasm /static/uploads/patients/{id}.jpg orqali
# ko'rinadi).
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_MODULE_DIR)
PATIENT_PHOTOS_DIR = os.path.join(_PROJECT_ROOT, "static", "uploads", "patients")

MAX_PHOTO_SIZE_BYTES = 2 * 1024 * 1024  # 2 MB
ALLOWED_PHOTO_CONTENT_TYPES = {"image/jpeg", "image/jpg", "image/png"}
ALLOWED_PHOTO_FORMATS = {"JPEG", "PNG"}
PHOTO_THUMBNAIL_SIZE = (400, 400)

# ⬅️ YANGI (1-band, KRITIK): dependencies=[Depends(get_current_user)] — bu
# modulning BARCHA endpointlari (shu jumladan hozirgi va kelajakda
# qo'shiladigan har qanday GET) uchun login majburiy qilinadi. Ilgari faqat
# write (POST/PUT/DELETE) endpointlarda require_role bor edi, GET /list va
# GET /{id} esa hech qanday auth tekshiruvisiz OCHIQ edi — login qilmasdan
# ham barcha bemorlarning shaxsiy/tibbiy ma'lumotlarini o'qish mumkin edi.
router = APIRouter(
    prefix="/api/patients",
    tags=["Patients"],
    dependencies=[Depends(get_current_user)],
)


def compute_financials(db: Session, patient_id: int) -> schemas.PatientFinancials:
    """Bemorning qarzi HISOBLANADI (price - to'langan), hech qanday
    saqlangan 'balance' maydoniga tayanmaydi — audit hisobotidagi #2-band."""
    # joinedload(payments): appointment.paid_amount pastda har bir qabul
    # uchun a.payments'ga murojaat qiladi — eager load bo'lmasa, har bir
    # qabul uchun ALOHIDA so'rov ketadi (klassik N+1: 100 ta qabul = 101
    # so'rov o'rniga shu yerda bittagina JOIN so'rov bo'ladi).
    appointments = (
        db.query(models.Appointment)
        .options(joinedload(models.Appointment.payments))
        .filter(
            models.Appointment.patient_id == patient_id,
            models.Appointment.status != "cancelled",
        )
        .all()
    )
    total_charged = sum(a.price for a in appointments)
    total_paid = sum(a.paid_amount for a in appointments)
    return schemas.PatientFinancials(
        total_charged=total_charged,
        total_paid=total_paid,
        total_debt=max(total_charged - total_paid, 0),
    )


def _get_patient_or_404(db: Session, patient_id: int) -> models.Patient:
    """Soft-delete qilingan (is_deleted=True) bemorlar bu yerda "yo'q"
    deb hisoblanadi — allaqachon o'chirilgan bemorni tahrirlash/ko'rish/
    yana o'chirishga urinish oddiy 404 qaytaradi, xuddi u haqiqatan ham
    DB'da bo'lmagandek (garchi qator moliyaviy/tibbiy tarix uchun
    jismonan saqlanib qolsa ham)."""
    patient = (
        db.query(models.Patient)
        .filter(models.Patient.id == patient_id, models.Patient.is_deleted.is_(False))
        .first()
    )
    if patient is None:
        raise HTTPException(status_code=404, detail="Bemor topilmadi")
    return patient


# ── Static/action routes first ──────────────────────────────────────
@router.post(
    "/add",
    response_model=schemas.PatientRead,
    status_code=201,
    dependencies=[Depends(require_role("admin", "reception"))],
)
def add_patient(
    patient_data: schemas.PatientCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> models.Patient:
    """Yangi bemor qo'shish. Field nomlari models.Patient bilan bir xil
    (fullname) — eski 'name' xatosi shu yerda bartaraf etilgan."""
    new_patient = models.Patient(**patient_data.model_dump())
    db.add(new_patient)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Bu telefon raqami allaqachon ro'yxatdan o'tgan")
    db.refresh(new_patient)
    log_action(db, user, "patient.add", "Patient", new_patient.id, f"fullname={new_patient.fullname}")
    return new_patient


# Prompt 24: sort_by uchun ruxsat etilgan ustunlar allowlist'i — xom
# `sort_by` matnini to'g'ridan-to'g'ri `getattr(models.Patient, ...)`ga
# uzatish SQL-injection emas (SQLAlchemy ustun obyektiga aylantiradi),
# lekin ixtiyoriy/shifrlangan/mavjud bo'lmagan ustunlarga sort qilishga
# yo'l qo'ymaslik uchun baribir allowlist kerak (masalan `phone` —
# EncryptedString, ciphertext bo'yicha "sort" ma'nosiz bo'lardi).
_PATIENT_SORT_COLUMNS = {
    "id": models.Patient.id,
    "fullname": models.Patient.fullname,
    "created_at": models.Patient.created_at,
    "birth_date": models.Patient.birth_date,
}


def _patients_base_query(
    db: Session,
    user: models.User,
    allergy: Optional[str] = None,
    chronic_condition: Optional[str] = None,
    search: Optional[str] = None,
) -> SAQuery:
    """Umumiy filtrlash mantig'i — sahifalangan API (``list_patients``)
    va to'liq (sahifalanmagan, server-render HTML sahifalari uchun)
    ro'yxat (``list_patients_all``) shu yerdan foydalanadi, mantiq
    ikki joyda takrorlanmasligi uchun.

    ``search`` faqat ``fullname`` bo'yicha ishlaydi — ``phone`` va
    boshqa PII maydonlar EncryptedString bo'lib, DB darajasida ILIKE
    bilan qidirib bo'lmaydi (shifrlangan holda saqlanadi).
    """
    query = db.query(models.Patient)

    # ⬅️ lab_doctor faqat O'ZIGA biriktirilgan bemorlarni ko'radi —
    # "biriktirilgan" LabResult.doctor_id == user.doctor_id orqali
    # aniqlanadi. doctor_id sozlanmagan bo'lsa (nazariy holat), har doim
    # bo'sh natija qaytishi kerak — shuning uchun butunlay yolg'on
    # filtr (`false()`) qo'llanadi, boshqa lab shifokorlarining
    # bemorlari sizib chiqmasligi uchun.
    if user.role == "lab_doctor":
        if user.doctor_id is None:
            return query.filter(sa_false())
        query = query.filter(
            models.Patient.id.in_(
                db.query(models.LabResult.patient_id).filter(
                    models.LabResult.doctor_id == user.doctor_id
                )
            )
        )

    if allergy:
        query = query.filter(
            models.Patient.id.in_(
                db.query(models.Allergy.patient_id).filter(
                    models.Allergy.substance.ilike(f"%{allergy}%")
                )
            )
        )
    if chronic_condition:
        query = query.filter(
            models.Patient.id.in_(
                db.query(models.ChronicCondition.patient_id).filter(
                    models.ChronicCondition.name.ilike(f"%{chronic_condition}%")
                )
            )
        )
    if search:
        query = query.filter(models.Patient.fullname.ilike(f"%{search}%"))
    return query


@router.get("/list", response_model=schemas.PatientPage)
def list_patients(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    allergy: Optional[str] = None,
    chronic_condition: Optional[str] = None,
    search: Optional[str] = Query(None, description="F.I.O bo'yicha qidirish (ILIKE)"),
    sort_by: str = Query("id", description="id | fullname | created_at | birth_date"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> schemas.PatientPage:
    """Barcha bemorlar ro'yxati — sahifalangan, saralangan va qidiruvli
    (Prompt 24).

    Ixtiyoriy query-parametrlar orqali filtrlash mumkin (4-band):
      - ``allergy=<matn>``            — Allergy.substance bo'yicha
        (LIKE, katta-kichik harflarga sezgir emas)
      - ``chronic_condition=<matn>``  — ChronicCondition.name bo'yicha
        (xuddi shunday)

    🔐 Bu ikki filtr tibbiy ma'lumot (kim qanday allergiya/kasallikka ega
    ekanini oshkor qiladi), shuning uchun faqat admin/reception/doctor
    ishlata oladi — cashier hatto natijada allergiya matni ko'rsatilmasa
    ham, "qaysi bemorlar filtrga mos keldi" degan yon-kanal orqali tibbiy
    ma'lumotni bilib olmasligi kerak (medical-security-auditor: least
    privilege / data leakage oldini olish).
    """
    if (allergy or chronic_condition) and user.role not in (
        "admin",
        "reception",
        "doctor",
    ):
        raise HTTPException(
            status_code=403,
            detail="Bu filtr faqat admin/reception/doctor uchun ruxsat etilgan (tibbiy ma'lumot)",
        )

    query = _patients_base_query(db, user, allergy, chronic_condition, search)

    sort_column = _PATIENT_SORT_COLUMNS.get(sort_by, models.Patient.id)
    sort_column = sort_column.asc() if sort_order == "asc" else sort_column.desc()

    total = query.count()
    # models.Patient.id.desc() — barqaror ikkinchi tartib mezoni: `sort_by`
    # ustunida bir xil qiymat (masalan bir kunda yaratilgan ko'p bemor)
    # bo'lganda, DB har sahifani so'rashda qatorlarni har xil tartibda
    # qaytarishi mumkin — natijada bitta bemor ikki sahifada ham chiqishi
    # yoki umuman chiqmay qolishi mumkin edi.
    items = (
        query.order_by(sort_column, models.Patient.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return schemas.PatientPage(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max((total + page_size - 1) // page_size, 1),
    )


def list_patients_all(
    db: Session,
    user: models.User,
    allergy: Optional[str] = None,
    chronic_condition: Optional[str] = None,
) -> List[models.Patient]:
    """To'liq (sahifalanmagan) bemorlar ro'yxati — server-render
    HTML sahifalari (main.py :: patients_page, appointments_page) uchun.
    API endpoint EMAS, oddiy Python funksiyasi sifatida chaqiriladi."""
    return _patients_base_query(db, user, allergy, chronic_condition).order_by(
        models.Patient.id.desc()
    ).all()


# ── Palata (statsionar davolash, Prompt 7) ──────────────────────────
# Bu ikkita GET /admitted va /rooms — STATIK yo'llar, shuning uchun
# /{patient_id} dinamik yo'lidan OLDIN e'lon qilinishi shart (aks holda
# FastAPI "admitted"/"rooms"ni patient_id sifatida talqin qilib, 422
# xatolik qaytarardi — fayl boshidagi izohdagi tamoyilning o'zi).
#
# Ruxsat: admin/reception/doctor — allergiya/surunkali kasallik
# ro'yxatlari bilan bir xil patternga mos (tibbiy/statsionar ma'lumot,
# cashier va lab_doctor'ga kerak emas).
@router.get(
    "/admitted",
    response_model=List[schemas.PatientRoomResponse],
    dependencies=[Depends(require_role("admin", "reception", "doctor"))],
)
def list_admitted_patients(db: Session = Depends(get_db)) -> List[models.Patient]:
    """Hozir palatada yotgan (is_admitted=True) barcha bemorlar."""
    return (
        db.query(models.Patient)
        .filter(models.Patient.is_admitted.is_(True))
        .order_by(models.Patient.room_number.asc())
        .all()
    )


@router.get(
    "/rooms",
    response_model=List[schemas.RoomGroup],
    dependencies=[Depends(require_role("admin", "reception", "doctor"))],
)
def list_rooms(db: Session = Depends(get_db)) -> List[schemas.RoomGroup]:
    """Barcha band palatalar va har biridagi (hozir yotgan) bemorlar,
    palata raqami bo'yicha guruhlangan."""
    admitted = (
        db.query(models.Patient)
        .filter(models.Patient.is_admitted.is_(True))
        .order_by(models.Patient.room_number.asc(), models.Patient.id.asc())
        .all()
    )
    rooms: Dict[str, List[models.Patient]] = {}
    for patient in admitted:
        room = patient.room_number or "Noma'lum"
        rooms.setdefault(room, []).append(patient)
    return [
        schemas.RoomGroup(room_number=room, patients=patients)
        for room, patients in sorted(rooms.items())
    ]


# ── Dynamic path parameter routes ───────────────────────────────────
@router.get("/{patient_id}", response_model=schemas.PatientDetail)
def get_patient(patient_id: int, db: Session = Depends(get_db)) -> schemas.PatientDetail:
    patient = _get_patient_or_404(db, patient_id)
    financials = compute_financials(db, patient_id)
    # 🛏️ Prompt 10 — to'liq yotqizilishlar tarixi. `patient.admissions`
    # relationship'i models.py'da `order_by="PatientAdmission.admitted_at.desc()"`
    # bilan e'lon qilingan, shuning uchun bu yerda qo'shimcha saralash
    # shart emas — eng yangi epizod avtomatik birinchi bo'lib keladi.
    admissions = [
        schemas.PatientAdmissionRead.model_validate(a) for a in patient.admissions
    ]
    return schemas.PatientDetail(
        **schemas.PatientRead.model_validate(patient).model_dump(),
        financials=financials,
        admissions=admissions,
    )


@router.put(
    "/{patient_id}",
    response_model=schemas.PatientRead,
    dependencies=[Depends(require_role("admin", "reception"))],
)
def update_patient(
    patient_id: int,
    patient_data: schemas.PatientUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> models.Patient:
    patient = _get_patient_or_404(db, patient_id)
    apply_update(patient, patient_data)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Bu telefon raqami allaqachon ro'yxatdan o'tgan")
    db.refresh(patient)
    log_action(db, user, "patient.update", "Patient", patient.id, f"fullname={patient.fullname}")
    return patient


@router.delete(
    "/{patient_id}",
    status_code=204,
    dependencies=[Depends(require_role("admin"))],
)
def delete_patient(
    patient_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> None:
    """🗑️ Soft Delete (Prompt 6): bemor DB'dan jismonan o'chirilmaydi —
    faqat `is_deleted=True` va `deleted_at=hozir` qilib belgilanadi.

    Bu Prompt 5'dagi "bog'liq yozuvlar bor bemorni o'chirib bo'lmaydi"
    (409) tekshiruvidan ham xavfsizroq: u yerda ham hali xato bilan
    hard-delete qilinishi (yoki kelajakda tekshiruv chetlab o'tilishi)
    mumkin edi, endi esa Patient qatorining o'zi hech qachon
    o'chirilmagani uchun uning appointments/payments/lab_results
    tarixi doim, har qanday holatda ham saqlanib qoladi — shuning
    uchun bog'liqliklarni sanab, 409 qaytarishga endi ehtiyoj yo'q.

    `_get_patient_or_404` allaqachon `is_deleted=False` bilan
    filtrlagani uchun ikki marta o'chirishga urinish avtomatik ravishda
    404 qaytaradi (idempotent emas — bemor "allaqachon o'chirilgan"
    holatda "topilmadi" sifatida ko'rinadi)."""
    patient = _get_patient_or_404(db, patient_id)

    fullname = patient.fullname
    patient.is_deleted = True
    patient.deleted_at = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    db.commit()
    log_action(db, user, "patient.delete", "Patient", patient_id, f"fullname={fullname}")
    return None


# ── Bemor rasmi ──────────────────────────────────────────────────────
@router.post(
    "/{patient_id}/photo",
    response_model=schemas.PatientPhotoUploadResponse,
    dependencies=[Depends(require_role("admin", "reception"))],
)
async def upload_patient_photo(
    patient_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> schemas.PatientPhotoUploadResponse:
    """Bemor rasmini yuklash. Faqat JPG/PNG qabul qilinadi, hajm 2MB dan
    oshmasligi kerak. Pillow bilan 400x400 (proportional, bo'lmagan
    tomonlari kesilmaydi) thumbnaylga kichraytirilib,
    static/uploads/patients/{patient_id}.jpg sifatida saqlanadi va
    Patient.photo_path yangilanadi."""
    patient = _get_patient_or_404(db, patient_id)

    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_PHOTO_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Faqat JPG yoki PNG formatidagi rasm qabul qilinadi",
        )

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Bo'sh fayl yuklab bo'lmaydi")
    if len(raw) > MAX_PHOTO_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="Rasm hajmi 2MB dan oshmasligi kerak")

    # Content-Type sarlavhasi klient tomonidan soxtalashtirilishi mumkin,
    # shuning uchun haqiqiy tekshiruv — Pillow orqali faylni ochib
    # ko'rish. verify() faylni tekshiradi-yu, lekin keyin qayta
    # ishlatib bo'lmaydi (Pillow'ning o'zi shuni talab qiladi), shuning
    # uchun tekshiruvdan so'ng xotiradagi baytlardan qayta ochamiz.
    try:
        probe = Image.open(io.BytesIO(raw))
        probe.verify()
        image = Image.open(io.BytesIO(raw))
    except (UnidentifiedImageError, OSError):
        raise HTTPException(status_code=400, detail="Fayl yaroqli rasm emas")

    if image.format not in ALLOWED_PHOTO_FORMATS:
        raise HTTPException(
            status_code=400,
            detail="Faqat JPG yoki PNG formatidagi rasm qabul qilinadi",
        )

    image = image.convert("RGB")
    image.thumbnail(PHOTO_THUMBNAIL_SIZE, Image.LANCZOS)

    os.makedirs(PATIENT_PHOTOS_DIR, exist_ok=True)
    file_path = os.path.join(PATIENT_PHOTOS_DIR, f"{patient_id}.jpg")
    image.save(file_path, format="JPEG", quality=85)

    # Cache-bust: fayl nomi doim bir xil (patient_id.jpg), shuning uchun
    # eski rasm brauzer keshidan ko'rinib qolmasligi uchun URL'ga versiya
    # so'rov parametri qo'shiladi.
    photo_path = f"/static/uploads/patients/{patient_id}.jpg?v={int(time.time())}"
    patient.photo_path = photo_path
    db.commit()

    log_action(
        db,
        user,
        "patient.photo_upload",
        "Patient",
        patient.id,
        f"fullname={patient.fullname}",
    )
    return schemas.PatientPhotoUploadResponse(photo_path=photo_path)


# ── Palataga yotqizish / chiqarish (bemor sub-resursi, Prompt 7) ────
# Ruxsat: faqat doctor va reception yotqizish/chiqarish qila oladi
# (talab #4). Admin bu yerda YOZISH huquqiga ega emas — talabda admin
# uchun faqat "barcha palata ma'lumotlarini ko'rish" ko'rsatilgan,
# yotqizish/chiqarish esa doctor/reception ishi hisoblanadi.
@router.post(
    "/{patient_id}/admit",
    response_model=schemas.PatientRoomResponse,
    dependencies=[Depends(require_role("doctor", "reception"))],
)
def admit_patient(
    patient_id: int,
    admit_data: schemas.PatientRoomUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> models.Patient:
    """Bemorni palataga yotqizish. room_number MAJBURIY."""
    patient = _get_patient_or_404(db, patient_id)

    if not admit_data.room_number:
        raise HTTPException(status_code=400, detail="Palata raqami ko'rsatilishi shart")

    if patient.is_admitted:
        raise HTTPException(
            status_code=409,
            detail="Bemor allaqachon palatada — avval chiqarish (discharge) kerak",
        )

    # 🛏️ Palata bandligi: shu palata raqamida hozir boshqa bemor
    # yotgan bo'lsa, yangi bemorni yotqizib bo'lmaydi (bir palataga
    # bitta bemor tamoyili).
    occupied_by = (
        db.query(models.Patient)
        .filter(
            models.Patient.room_number == admit_data.room_number,
            models.Patient.is_admitted.is_(True),
            models.Patient.id != patient_id,
        )
        .first()
    )
    if occupied_by is not None:
        raise HTTPException(
            status_code=409,
            detail=f"{admit_data.room_number}-palata band ({occupied_by.fullname})",
        )

    admitted_at = admit_data.admitted_at or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    # 🛏️ Prompt 10 bag-fix: avval bu yerda Patient'ning joriy-holat
    # ustunlari (quyida) ustiga to'g'ridan-to'g'ri yozib yuborilardi —
    # bemor chiqarilib qayta yotqizilganda oldingi epizodning
    # discharged_at/room_number'i saqlanmasdi. Endi HAR bir yotqizilish
    # uchun alohida PatientAdmission qatori OCHILADI (tarix
    # yo'qolmaydi); Patient'dagi ustunlar esa hamon "hozir kim
    # palatada" tezkor so'roviga xizmat qilib qoladi.
    admission = models.PatientAdmission(
        patient_id=patient.id,
        room_number=admit_data.room_number,
        admitted_at=admitted_at,
    )
    db.add(admission)

    patient.room_number = admit_data.room_number
    patient.admitted_at = admitted_at
    patient.discharged_at = None
    patient.is_admitted = True
    db.commit()
    db.refresh(patient)
    log_action(
        db,
        user,
        "patient.admit",
        "Patient",
        patient.id,
        f"fullname={patient.fullname}, room_number={patient.room_number}",
    )
    return patient


@router.post(
    "/{patient_id}/discharge",
    response_model=schemas.PatientRoomResponse,
    dependencies=[Depends(require_role("doctor", "reception"))],
)
def discharge_patient(
    patient_id: int,
    discharge_data: schemas.PatientRoomUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> models.Patient:
    """Bemorni palatadan chiqarish."""
    patient = _get_patient_or_404(db, patient_id)

    if not patient.is_admitted:
        raise HTTPException(status_code=409, detail="Bemor hozir palatada emas")

    discharged_at = discharge_data.discharged_at or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    # 🛏️ Prompt 10 bag-fix: joriy OCHIQ (discharged_at IS NULL) admission
    # qatorini topib, aynan o'sha yozuvga discharged_at/discharged_reason
    # yoziladi — Patient ustunlariga yozib qo'ya qolish o'rniga, shu
    # tarix qatorining o'zi yopiladi va boshqa hech narsa o'zgarmaydi.
    open_admission = (
        db.query(models.PatientAdmission)
        .filter(
            models.PatientAdmission.patient_id == patient.id,
            models.PatientAdmission.discharged_at.is_(None),
        )
        .order_by(models.PatientAdmission.admitted_at.desc())
        .first()
    )
    if open_admission is not None:
        open_admission.discharged_at = discharged_at
        open_admission.discharged_reason = discharge_data.discharged_reason

    patient.discharged_at = discharged_at
    patient.is_admitted = False
    db.commit()
    db.refresh(patient)
    log_action(
        db,
        user,
        "patient.discharge",
        "Patient",
        patient.id,
        f"fullname={patient.fullname}, room_number={patient.room_number}",
    )
    return patient


# ── Allergiyalar (bemor sub-resursi) ────────────────────────────────
# Ruxsat: admin/reception/doctor — cashier bu ma'lumotni ko'ra olmasin
# (moliya xodimiga bemorning tibbiy/allergiya tarixi kerak emas).
def _get_allergy_or_404(db: Session, patient_id: int, allergy_id: int) -> models.Allergy:
    allergy = (
        db.query(models.Allergy)
        .filter(models.Allergy.id == allergy_id, models.Allergy.patient_id == patient_id)
        .first()
    )
    if allergy is None:
        raise HTTPException(status_code=404, detail="Allergiya yozuvi topilmadi")
    return allergy


@router.post(
    "/{patient_id}/allergies",
    response_model=schemas.AllergyRead,
    status_code=201,
    dependencies=[Depends(require_role("admin", "reception", "doctor"))],
)
def add_allergy(
    patient_id: int,
    allergy_data: schemas.AllergyCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> models.Allergy:
    _get_patient_or_404(db, patient_id)
    new_allergy = models.Allergy(patient_id=patient_id, **allergy_data.model_dump())
    db.add(new_allergy)
    db.commit()
    db.refresh(new_allergy)
    log_action(
        db,
        user,
        "patient.allergy_add",
        "Allergy",
        new_allergy.id,
        f"patient_id={patient_id}, substance={new_allergy.substance}",
    )
    return new_allergy


@router.get(
    "/{patient_id}/allergies",
    response_model=List[schemas.AllergyRead],
    dependencies=[Depends(require_role("admin", "reception", "doctor"))],
)
def list_allergies(
    patient_id: int, db: Session = Depends(get_db)
) -> List[models.Allergy]:
    _get_patient_or_404(db, patient_id)
    return (
        db.query(models.Allergy)
        .filter(models.Allergy.patient_id == patient_id)
        .order_by(models.Allergy.id.desc())
        .all()
    )


@router.delete(
    "/{patient_id}/allergies/{allergy_id}",
    status_code=204,
    dependencies=[Depends(require_role("admin", "reception", "doctor"))],
)
def delete_allergy(
    patient_id: int,
    allergy_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> None:
    allergy = _get_allergy_or_404(db, patient_id, allergy_id)
    substance = allergy.substance
    db.delete(allergy)
    db.commit()
    log_action(
        db,
        user,
        "patient.allergy_delete",
        "Allergy",
        allergy_id,
        f"patient_id={patient_id}, substance={substance}",
    )
    return None


# ── Surunkali kasalliklar (bemor sub-resursi) ───────────────────────
def _get_chronic_condition_or_404(
    db: Session, patient_id: int, condition_id: int
) -> models.ChronicCondition:
    condition = (
        db.query(models.ChronicCondition)
        .filter(
            models.ChronicCondition.id == condition_id,
            models.ChronicCondition.patient_id == patient_id,
        )
        .first()
    )
    if condition is None:
        raise HTTPException(status_code=404, detail="Surunkali kasallik yozuvi topilmadi")
    return condition


@router.post(
    "/{patient_id}/chronic-conditions",
    response_model=schemas.ChronicConditionRead,
    status_code=201,
    dependencies=[Depends(require_role("admin", "reception", "doctor"))],
)
def add_chronic_condition(
    patient_id: int,
    condition_data: schemas.ChronicConditionCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> models.ChronicCondition:
    _get_patient_or_404(db, patient_id)
    new_condition = models.ChronicCondition(
        patient_id=patient_id, **condition_data.model_dump()
    )
    db.add(new_condition)
    db.commit()
    db.refresh(new_condition)
    log_action(
        db,
        user,
        "patient.chronic_add",
        "ChronicCondition",
        new_condition.id,
        f"patient_id={patient_id}, name={new_condition.name}",
    )
    return new_condition


@router.get(
    "/{patient_id}/chronic-conditions",
    response_model=List[schemas.ChronicConditionRead],
    dependencies=[Depends(require_role("admin", "reception", "doctor"))],
)
def list_chronic_conditions(
    patient_id: int, db: Session = Depends(get_db)
) -> List[models.ChronicCondition]:
    _get_patient_or_404(db, patient_id)
    return (
        db.query(models.ChronicCondition)
        .filter(models.ChronicCondition.patient_id == patient_id)
        .order_by(models.ChronicCondition.id.desc())
        .all()
    )


@router.delete(
    "/{patient_id}/chronic-conditions/{condition_id}",
    status_code=204,
    dependencies=[Depends(require_role("admin", "reception", "doctor"))],
)
def delete_chronic_condition(
    patient_id: int,
    condition_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> None:
    condition = _get_chronic_condition_or_404(db, patient_id, condition_id)
    name = condition.name
    db.delete(condition)
    db.commit()
    log_action(
        db,
        user,
        "patient.chronic_delete",
        "ChronicCondition",
        condition_id,
        f"patient_id={patient_id}, name={name}",
    )
    return None


# ── Davolanishlar tarixi (bemor sub-resursi, Prompt 2) ──────────────
# Ruxsat matritsasi (audit.py + medical-security-auditor: least privilege):
#   - Yozish (POST)  → faqat admin/doctor: tashxis/davolash rejasini faqat
#     shifokorning o'zi (yoki admin, texnik xizmat/tuzatish uchun) kirita
#     oladi — reception va cashier tibbiy qaror qabul qilmaydi.
#   - O'qish (GET)   → admin/reception/doctor: reception navbat/tashrifni
#     tashkillashtirish uchun tarixni ko'rishi kerak bo'lishi mumkin, lekin
#     cashier (moliyaviy rol) tibbiy tafsilotga umuman kirmasin.
@router.post(
    "/{patient_id}/treatment-history",
    response_model=schemas.TreatmentHistoryRead,
    status_code=201,
    dependencies=[Depends(require_role("doctor", "admin"))],
)
def add_treatment_history(
    patient_id: int,
    treatment_data: schemas.TreatmentHistoryCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> models.TreatmentHistory:
    _get_patient_or_404(db, patient_id)

    # Agar appointment_id berilgan bo'lsa — u shu bemorga tegishli
    # ekanini tekshiramiz (boshqa bemorning tashrifiga yozuvni "yopishtirib
    # qo'yish" — BOLA/IDOR xurujining oldini olish).
    if treatment_data.appointment_id is not None:
        appointment = (
            db.query(models.Appointment)
            .filter(
                models.Appointment.id == treatment_data.appointment_id,
                models.Appointment.patient_id == patient_id,
            )
            .first()
        )
        if appointment is None:
            raise HTTPException(
                status_code=404,
                detail="Ko'rsatilgan tashrif shu bemorga tegishli emas",
            )

    if treatment_data.doctor_id is not None:
        doctor = (
            db.query(models.Doctor)
            .filter(models.Doctor.id == treatment_data.doctor_id)
            .first()
        )
        if doctor is None:
            raise HTTPException(status_code=404, detail="Shifokor topilmadi")

    payload = treatment_data.model_dump()
    # date berilmagan (None) bo'lsa, ustunni umuman o'rnatmaymiz — shunda
    # models.TreatmentHistory.date'ning Python-side default'i
    # (datetime.date.today) ishga tushadi. Agar bu yerda payload["date"]=None
    # aniq o'rnatilsa, SQLAlchemy buni "ataylab None qilingan" deb hisoblab,
    # default'ni chetlab o'tar edi va NOT NULL constraint buzilardi.
    if payload.get("date") is None:
        payload.pop("date", None)

    new_treatment = models.TreatmentHistory(patient_id=patient_id, **payload)
    db.add(new_treatment)
    db.commit()
    db.refresh(new_treatment)
    log_action(
        db,
        user,
        "patient.treatment_add",
        "TreatmentHistory",
        new_treatment.id,
        f"patient_id={patient_id}, date={new_treatment.date}",
    )
    return new_treatment


@router.get(
    "/{patient_id}/treatment-history",
    response_model=List[schemas.TreatmentHistoryRead],
    dependencies=[Depends(require_role("admin", "reception", "doctor"))],
)
def list_treatment_history(
    patient_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> List[models.TreatmentHistory]:
    _get_patient_or_404(db, patient_id)
    history = (
        db.query(models.TreatmentHistory)
        .filter(models.TreatmentHistory.patient_id == patient_id)
        .order_by(
            models.TreatmentHistory.date.desc(), models.TreatmentHistory.id.desc()
        )
        .all()
    )
    # 🧾 Diagnoz/davolash rejasi eng nozik tibbiy ma'lumot hisoblanadi,
    # shuning uchun (boshqa oddiy GET endpointlaridan farqli o'laroq) bu
    # yerda O'QISH ham audit qilinadi — "kim, qachon, qaysi bemorning
    # davolanish tarixini ko'rdi" (medical-security-auditor: immutable
    # audit log, Append-Only). Har bir qatorni emas, bitta ro'yxat
    # so'rovini bitta yozuv sifatida qayd etamiz — aks holda audit_logs
    # jadvali har bir sahifa yuklanishida keraksiz tez to'lib ketardi.
    log_action(
        db,
        user,
        "patient.treatment_view",
        "TreatmentHistory",
        patient_id,
        f"patient_id={patient_id}, count={len(history)}",
    )
    return history


def register_module() -> Dict[str, object]:
    return {
        "module_name": "Patients",
        "version": "3.0.0",
        "router": router,
    }
