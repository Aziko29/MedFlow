"""
Doctor registration, listing, edit, delete + narxnoma (consultation_price)
and working_hours — audit points #3 (no price list) and #7 (no schedule,
so double-booking went unchecked; the actual collision check lives in
modules/appointments.py, this module just stores the fields it needs).

Same route-ordering rule as patients.py: static/action paths before /{id}.
"""
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, Path
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


def register_module() -> Dict[str, object]:
    return {
        "module_name": "Doctors",
        "version": "2.1.0",
        "router": router,
    }