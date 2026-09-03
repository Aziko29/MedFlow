"""
Doctor registration, listing, edit, delete + narxnoma (consultation_price)
and working_hours — audit points #3 (no price list) and #7 (no schedule,
so double-booking went unchecked; the actual collision check lives in
modules/appointments.py, this module just stores the fields it needs).

Same route-ordering rule as patients.py: static/action paths before /{id}.
"""
from typing import Dict, List

import io
import os
import time
from datetime import date as date_type
from datetime import datetime as dt
from datetime import timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, UploadFile
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

import models
import schemas
from audit import log_action
from auth import get_current_user, require_role
from database import get_db
from modules.appointments import _find_conflicting_appointment, _get_queue_interval_minutes
from reminder_service import _parse_working_hours

# ⬅️ YANGI (1-band, KRITIK): login majburiy — barcha GET endpointlar ochiq
# edi (shifokorlar ro'yxati, narxnoma, ish jadvali login'siz ko'rinardi).
router = APIRouter(
    prefix="/api/doctors",
    tags=["Doctors"],
    dependencies=[Depends(get_current_user)],
)

# 🖼️ Shifokor rasmi — modules/patients.py dagi upload_patient_photo bilan
# BIR XIL pattern (cheklovlar, saqlash joyi, thumbnail o'lchami).
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_MODULE_DIR)
DOCTOR_PHOTOS_DIR = os.path.join(_PROJECT_ROOT, "static", "uploads", "doctors")

MAX_PHOTO_SIZE_BYTES = 2 * 1024 * 1024  # 2 MB
ALLOWED_PHOTO_CONTENT_TYPES = {"image/jpeg", "image/jpg", "image/png"}
ALLOWED_PHOTO_FORMATS = {"JPEG", "PNG"}
PHOTO_THUMBNAIL_SIZE = (400, 400)


def _get_doctor_or_404(db: Session, doctor_id: int) -> models.Doctor:
    doctor = db.query(models.Doctor).filter(models.Doctor.id == doctor_id).first()
    if doctor is None:
        raise HTTPException(status_code=404, detail="Shifokor topilmadi")
    return doctor


# ── Static/action routes first ──────────────────────────────────────
@router.post(
    "/add",
    response_model=schemas.DoctorRead,
    status_code=201,
    dependencies=[Depends(require_role("admin"))],
)
def add_doctor(
    doctor_data: schemas.DoctorCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> models.Doctor:
    new_doctor = models.Doctor(**doctor_data.model_dump())
    db.add(new_doctor)
    db.commit()
    db.refresh(new_doctor)
    log_action(db, user, "doctor.add", "Doctor", new_doctor.id, f"fullname={new_doctor.fullname}")
    return new_doctor


@router.get("/list", response_model=List[schemas.DoctorRead])
def list_doctors(
    db: Session = Depends(get_db),
    active_only: bool = Query(
        default=False,
        description=(
            "True bo'lsa, faqat is_active=True shifokorlar qaytariladi — "
            "masalan yangi qabul/tahlil band qilishda faolsizlantirilgan "
            "shifokor tanlov ro'yxatida ko'rinmasin desa shu ishlatiladi. "
            "Standart holatda (False) admin ro'yxatlari uchun BARCHA "
            "shifokorlar (faolsizlar ham) qaytadi — aks holda ularni "
            "boshqarish (qayta faollashtirish) imkonsiz bo'lib qolardi."
        ),
    ),
) -> List[models.Doctor]:
    # ⬅️ YANGI (Prompt 8): db birinchi pozitsion parametr sifatida qoldi —
    # main.py'dagi ichki chaqiruvlar (masalan `list_doctors(db)`,
    # `list_doctors(db, active_only=True)`) buzilmasligi uchun.
    query = db.query(models.Doctor)
    if active_only:
        query = query.filter(models.Doctor.is_active == True)  # noqa: E712
    return query.order_by(models.Doctor.id.desc()).all()


# ── Dynamic path parameter routes ───────────────────────────────────
@router.get("/{doctor_id}", response_model=schemas.DoctorRead)
def get_doctor(
    doctor_id: int = Path(..., ge=1, le=2147483647),
    db: Session = Depends(get_db)
) -> models.Doctor:
    return _get_doctor_or_404(db, doctor_id)


@router.get(
    "/{doctor_id}/available-slots",
    response_model=schemas.DoctorAvailableSlotsResponse,
)
def get_available_slots(
    doctor_id: int = Path(..., ge=1, le=2147483647),
    target_date: date_type = Query(
        default=None,
        alias="date",
        description=(
            "Qaysi kun uchun bo'sh vaqtlar so'ralayotgani (YYYY-MM-DD). "
            "Berilmasa — bugungi kun."
        ),
    ),
    db: Session = Depends(get_db),
) -> schemas.DoctorAvailableSlotsResponse:
    """Shifokorning berilgan kundagi BO'SH vaqt oynalarini qaytaradi
    (Prompt 14).

    Har bir slot ikki shart bilan aniqlanadi:
      1. shifokorning ish vaqti (`Doctor.working_hours`, masalan
         "09:00 - 18:00") ichida turishi — parser
         reminder_service._parse_working_hours bilan bir xil (Telegram
         bot ham xuddi shu formatni ishlatadi, ikkita alohida qoidalar
         to'plami bo'lib qolmasligi uchun);
      2. `_find_conflicting_appointment` (modules/appointments.py —
         book_appointment ham AYNAN shu funksiyani ishlatadi) bo'yicha
         hech qanday faol/tugagan qabul bilan to'qnashmasligi.

    Slotlar orasidagi qadam — Sozlamalar modulidagi
    `queue_interval_minutes` (BOOKING_SLOT_MINUTES emas!): shu orqali
    bu ro'yxatdagi vaqt darhol POST /api/appointments/book'ga
    yuborilganda, book_appointment'ning o'z tekshiruvi bilan mos
    keladi — ikkita alohida "band/bo'sh" mantig'i bir-biridan
    farqlanib, foydalanuvchiga "bo'sh" ko'rsatilgan vaqt aslida rad
    etilishi ehtimolini yo'qqa chiqaradi.

    E'tibor: bu ro'yxat faqat SO'ROV PAYTIDAGI holatni aks ettiradi —
    ikkita xodim bir xil vaqtni bir vaqtda ko'rib, ikkalasi ham band
    qilishga urinishi mumkin. Buning oldini olish
    `book_appointment`ning o'zidagi DB darajasidagi qulf
    (`_lock_doctor_for_booking`) zimmasida, shuning uchun bu yerda
    qo'shimcha qulf SHART emas — bu endpoint faqat O'QIYDI, yozmaydi."""
    doctor = _get_doctor_or_404(db, doctor_id)
    day = target_date or date_type.today()

    interval_minutes = _get_queue_interval_minutes(db)
    (start_hour, start_minute), (end_hour, end_minute) = _parse_working_hours(doctor.working_hours)
    day_start = dt.combine(day, dt.min.time()).replace(hour=start_hour, minute=start_minute)
    day_end = dt.combine(day, dt.min.time()).replace(hour=end_hour, minute=end_minute)

    now = dt.now().replace(second=0, microsecond=0)
    step = timedelta(minutes=interval_minutes)

    slots: List[dt] = []
    cursor = day_start
    while cursor < day_end:
        if cursor >= now and _find_conflicting_appointment(db, doctor_id, cursor, interval_minutes) is None:
            slots.append(cursor)
        cursor += step

    return schemas.DoctorAvailableSlotsResponse(
        doctor_id=doctor_id,
        date=day,
        interval_minutes=interval_minutes,
        slots=slots,
    )


@router.put(
    "/{doctor_id}",
    response_model=schemas.DoctorRead,
    dependencies=[Depends(require_role("admin"))],
)
def update_doctor(
    doctor_id: int = Path(..., ge=1, le=2147483647),
    doctor_data: schemas.DoctorUpdate = ...,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> models.Doctor:
    doctor = _get_doctor_or_404(db, doctor_id)
    for field, value in doctor_data.model_dump().items():
        setattr(doctor, field, value)
    db.commit()
    db.refresh(doctor)
    log_action(db, user, "doctor.update", "Doctor", doctor.id, f"fullname={doctor.fullname}")
    return doctor


@router.delete(
    "/{doctor_id}",
    status_code=204,
    dependencies=[Depends(require_role("admin"))],
)
def delete_doctor(
    doctor_id: int = Path(..., ge=1, le=2147483647),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> None:
    """Prompt 7 tuzatishi: ilgari faqat Appointment tekshirilar edi —
    LabResult/TreatmentHistory/bog'langan xodim hisobi (User.doctor_id)
    e'tiborsiz qoldirilgani uchun, agar shifokorning FAQAT shu turdagi
    bog'liqligi bo'lsa-yu, Appointment yozuvi bo'lmasa, kod to'g'ridan-
    to'g'ri `db.delete(doctor)`ga o'tib, DB darajasida (PRAGMA
    foreign_keys=ON, database.py) IntegrityError bilan 500 xatolik
    berardi — foydalanuvchiga tushunarsiz "server xatosi".

    Endi doctor_id FK'ga ega BARCHA modellar (Appointment, LabResult,
    TreatmentHistory) hamda shu shifokorga bog'langan xodim hisoblari
    (User.doctor_id) oldindan tekshiriladi; biror narsa topilsa —
    aniq son va turlari bilan 409 Conflict qaytariladi, hech narsa
    o'zgartirilmaydi. Agar admin tarixni saqlab, shifokorni shunchaki
    ro'yxatdan chetlatmoqchi bo'lsa — buning uchun alohida
    PUT /api/doctors/{id} (is_active=false) mavjud."""
    doctor = _get_doctor_or_404(db, doctor_id)
    fullname = doctor.fullname

    appointment_count = (
        db.query(models.Appointment).filter(models.Appointment.doctor_id == doctor_id).count()
    )
    lab_result_count = (
        db.query(models.LabResult).filter(models.LabResult.doctor_id == doctor_id).count()
    )
    treatment_history_count = (
        db.query(models.TreatmentHistory)
        .filter(models.TreatmentHistory.doctor_id == doctor_id)
        .count()
    )
    linked_user_count = (
        db.query(models.User).filter(models.User.doctor_id == doctor_id).count()
    )

    if appointment_count or lab_result_count or treatment_history_count or linked_user_count:
        parts = []
        if appointment_count:
            parts.append(f"{appointment_count} ta qabul yozuvi")
        if lab_result_count:
            parts.append(f"{lab_result_count} ta tahlil natijasi")
        if treatment_history_count:
            parts.append(f"{treatment_history_count} ta davolash tarixi yozuvi")
        if linked_user_count:
            parts.append(f"{linked_user_count} ta bog'langan xodim hisobi")
        raise HTTPException(
            status_code=409,
            detail=(
                "Bu shifokorni o'chirib bo'lmaydi, chunki "
                + ", ".join(parts)
                + " bog'liq. Tibbiy tarixni saqlab qolish uchun uni o'chirish "
                "o'rniga faolsizlantiring: "
                f"POST /api/doctors/{doctor_id}/deactivate"
            ),
        )

    db.delete(doctor)
    db.commit()
    log_action(db, user, "doctor.delete", "Doctor", doctor_id, f"fullname={fullname}")
    return None


# ⬅️ YANGI (Prompt 8): Soft Delete/Deactivation — bog'liq tarixi (qabullar,
# tahlil natijalari, davolash tarixi) bo'lgani uchun yuqoridagi DELETE bilan
# o'chirib bo'lmaydigan shifokorlar uchun taklif etilgan muqobil: hard
# delete o'rniga is_active=False. Bu shifokorni va uning butun tibbiy
# tarixini DB'da saqlab qoladi, faqat uni yangi qabul/tahlil/davolash
# tanlovlarida (is_active bo'yicha filtrlangan ro'yxatlarda) yashiradi.
@router.post(
    "/{doctor_id}/deactivate",
    response_model=schemas.DoctorRead,
    dependencies=[Depends(require_role("admin"))],
)
def deactivate_doctor(
    doctor_id: int = Path(..., ge=1, le=2147483647),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> models.Doctor:
    doctor = _get_doctor_or_404(db, doctor_id)
    doctor.is_active = False
    db.commit()
    db.refresh(doctor)
    log_action(db, user, "doctor.deactivate", "Doctor", doctor.id, f"fullname={doctor.fullname}")
    return doctor


@router.post(
    "/{doctor_id}/activate",
    response_model=schemas.DoctorRead,
    dependencies=[Depends(require_role("admin"))],
)
def activate_doctor(
    doctor_id: int = Path(..., ge=1, le=2147483647),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> models.Doctor:
    """Faolsizlantirilgan shifokorni qaytadan ro'yxatlarga qaytarish —
    deactivate'ning teskarisi (masalan xato bosilgan yoki shifokor ishga
    qaytgan holatlar uchun)."""
    doctor = _get_doctor_or_404(db, doctor_id)
    doctor.is_active = True
    db.commit()
    db.refresh(doctor)
    log_action(db, user, "doctor.activate", "Doctor", doctor.id, f"fullname={doctor.fullname}")
    return doctor


# ── Shifokor rasmi ───────────────────────────────────────────────────
@router.post(
    "/{doctor_id}/photo",
    response_model=schemas.DoctorPhotoUploadResponse,
    dependencies=[Depends(require_role("admin"))],
)
async def upload_doctor_photo(
    doctor_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> schemas.DoctorPhotoUploadResponse:
    """Shifokor rasmini yuklash. Faqat JPG/PNG qabul qilinadi, hajm 2MB dan
    oshmasligi kerak. Pillow bilan 400x400 (proportional, tomonlari
    kesilmaydi) thumbnaylga kichraytirilib,
    static/uploads/doctors/{doctor_id}.jpg sifatida saqlanadi va
    Doctor.photo_path yangilanadi. Faqat admin (modules/patients.py dagi
    upload_patient_photo bilan bir xil pattern, lekin u yerda
    admin+reception ruxsat berilgan edi — shifokor ma'lumotlarini
    tahrirlash butun modulda faqat admin uchun, shu bilan izchil)."""
    doctor = _get_doctor_or_404(db, doctor_id)

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

    # Content-Type sarlavhasi soxtalashtirilishi mumkin, shuning uchun
    # haqiqiy tekshiruv — Pillow orqali faylni ochib ko'rish (patients.py
    # dagi bilan bir xil ikki bosqichli verify()/qayta ochish patterni).
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

    os.makedirs(DOCTOR_PHOTOS_DIR, exist_ok=True)
    file_path = os.path.join(DOCTOR_PHOTOS_DIR, f"{doctor_id}.jpg")
    image.save(file_path, format="JPEG", quality=85)

    # Cache-bust: fayl nomi doim bir xil (doctor_id.jpg), shuning uchun eski
    # rasm brauzer keshidan ko'rinib qolmasligi uchun URL'ga versiya so'rov
    # parametri qo'shiladi.
    photo_path = f"/static/uploads/doctors/{doctor_id}.jpg?v={int(time.time())}"
    doctor.photo_path = photo_path
    db.commit()

    log_action(
        db,
        user,
        "doctor.photo_upload",
        "Doctor",
        doctor.id,
        f"fullname={doctor.fullname}",
    )
    return schemas.DoctorPhotoUploadResponse(photo_path=photo_path)


def register_module() -> Dict[str, object]:
    return {
        "module_name": "Doctors",
        "version": "2.2.0",
        "router": router,
    }