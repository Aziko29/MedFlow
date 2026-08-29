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
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

import models
import schemas
from audit import log_action
from auth import get_current_user, require_role
from database import get_db

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
    patient = db.query(models.Patient).filter(models.Patient.id == patient_id).first()
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


@router.get("/list", response_model=List[schemas.PatientRead])
def list_patients(db: Session = Depends(get_db)) -> List[models.Patient]:
    """Barcha bemorlar ro'yxati."""
    return db.query(models.Patient).order_by(models.Patient.id.desc()).all()


# ── Dynamic path parameter routes ───────────────────────────────────
@router.get("/{patient_id}", response_model=schemas.PatientDetail)
def get_patient(patient_id: int, db: Session = Depends(get_db)) -> schemas.PatientDetail:
    patient = _get_patient_or_404(db, patient_id)
    financials = compute_financials(db, patient_id)
    return schemas.PatientDetail(
        **schemas.PatientRead.model_validate(patient).model_dump(),
        financials=financials,
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
    for field, value in patient_data.model_dump().items():
        setattr(patient, field, value)
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
    patient = _get_patient_or_404(db, patient_id)
    fullname = patient.fullname
    db.delete(patient)  # cascade removes their appointments + payments too
    db.commit()
    log_action(db, user, "patient.delete", "Patient", patient_id, f"fullname={fullname}")
    return None


def register_module() -> Dict[str, object]:
    return {
        "module_name": "Patients",
        "version": "3.0.0",
        "router": router,
    }
