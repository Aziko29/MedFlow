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

from fastapi import APIRouter, Depends, File, HTTPException, Path, UploadFile
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

import models
import schemas
from audit import log_action
from auth import get_current_user, require_role
from database import get_db

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
def list_doctors(db: Session = Depends(get_db)) -> List[models.Doctor]:
    return db.query(models.Doctor).order_by(models.Doctor.id.desc()).all()


# ── Dynamic path parameter routes ───────────────────────────────────
@router.get("/{doctor_id}", response_model=schemas.DoctorRead)
def get_doctor(
    doctor_id: int = Path(..., ge=1, le=2147483647),
    db: Session = Depends(get_db)
) -> models.Doctor:
    return _get_doctor_or_404(db, doctor_id)


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
    doctor = _get_doctor_or_404(db, doctor_id)
    fullname = doctor.fullname
    has_appointments = (
        db.query(models.Appointment).filter(models.Appointment.doctor_id == doctor_id).first()
    )
    if has_appointments:
        # Tarixni buzmaslik uchun — qabullari bo'lgan shifokorni o'chirish
        # o'rniga faolsizlantiramiz (is_active=False), tarix saqlanib qoladi.
        doctor.is_active = False
        db.commit()
        log_action(db, user, "doctor.deactivate", "Doctor", doctor_id, f"fullname={fullname}")
        return None
    db.delete(doctor)
    db.commit()
    log_action(db, user, "doctor.delete", "Doctor", doctor_id, f"fullname={fullname}")
    return None


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
        "version": "2.1.0",
        "router": router,
    }