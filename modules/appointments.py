"""
Appointment booking and status-tracking API.

Fixes from the audit:
  - #3 price list: booking copies doctor.consultation_price onto
    Appointment.price at creation time, so the amount owed is always known
    up front instead of the cashier guessing a number.
  - #5 state machine: status changes go through models.APPOINTMENT_TRANSITIONS
    instead of a free <select> that could jump any-state-to-any-state. You
    also can't mark an appointment "completed" while it's still unpaid.
  - #7 double booking: booking the same doctor at the same scheduled_time
    twice (while the first booking is still active) is rejected with 409.
  - #8 missing "bekor qilish" (cancel) action — added as its own endpoint
    with a required-in-spirit reason, rather than overloading /status.
"""
from datetime import datetime as dt
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session, joinedload

import models
import schemas
from audit import log_action
from auth import get_current_user, require_role
from database import get_db

# ⬅️ YANGI (1-band, KRITIK): login majburiy — barcha GET endpointlar (qabullar
# ro'yxati, bemor/shifokor bo'yicha tarix) login'siz ochiq edi.
router = APIRouter(
    prefix="/api/appointments",
    tags=["Appointments"],
    dependencies=[Depends(get_current_user)],
)

ACTIVE_STATUSES = ("waiting", "in_progress", "delayed")


def _ensure_patient_and_doctor_exist(db: Session, patient_id: int, doctor_id: int) -> models.Doctor:
    if not db.query(models.Patient).filter(models.Patient.id == patient_id).first():
        raise HTTPException(status_code=404, detail=f"Bemor #{patient_id} topilmadi")
    doctor = db.query(models.Doctor).filter(models.Doctor.id == doctor_id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail=f"Shifokor #{doctor_id} topilmadi")
    return doctor


def _to_detail(appt: models.Appointment) -> schemas.AppointmentDetail:
    return schemas.AppointmentDetail(
        id=appt.id,
        patient_id=appt.patient_id,
        doctor_id=appt.doctor_id,
        scheduled_time=appt.scheduled_time,
        status=appt.status,
        price=appt.price,
        cancel_reason=appt.cancel_reason,
        patient_name=appt.patient.fullname if appt.patient else "Noma'lum",
        doctor_name=f"{appt.doctor.fullname} ({appt.doctor.specialty})" if appt.doctor else "Noma'lum",
        paid_amount=appt.paid_amount,
        debt=appt.debt,
    )


def _detail_query(db: Session):
    """_to_detail() so'rov yuborilgach patient/doctor/payments'ga murojaat
    qiladi — eager load qilmasak, ro'yxatdagi HAR BIR qabul uchun 3 ta
    qo'shimcha so'rov ketadi (N+1). Bu yordamchi shu uchtasini bitta
    JOIN so'rovga birlashtiradi."""
    return db.query(models.Appointment).options(
        joinedload(models.Appointment.patient),
        joinedload(models.Appointment.doctor),
        joinedload(models.Appointment.payments),
    )


def _get_appointment_or_404(db: Session, appointment_id: int) -> models.Appointment:
    appointment = db.query(models.Appointment).filter(models.Appointment.id == appointment_id).first()
    if appointment is None:
        raise HTTPException(status_code=404, detail="Navbat topilmadi")
    return appointment


# ── Static/action routes first ──────────────────────────────────────
@router.post(
    "/book",
    response_model=schemas.AppointmentDetail,
    status_code=201,
    dependencies=[Depends(require_role("admin", "reception"))],
)
def book_appointment(
    appointment_data: schemas.AppointmentCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> schemas.AppointmentDetail:
    doctor = _ensure_patient_and_doctor_exist(db, appointment_data.patient_id, appointment_data.doctor_id)

    if appointment_data.scheduled_time < dt.now().replace(second=0, microsecond=0):
        raise HTTPException(status_code=400, detail="O'tmish sanaga qabul yozib bo'lmaydi")

    # 🚫 Double booking: shu shifokor, shu aniq vaqt, faol (bekor qilinmagan
    # va "kelmadi"ga belgilanmagan) qabul allaqachon bormi?
    collision = (
        db.query(models.Appointment)
        .filter(
            models.Appointment.doctor_id == appointment_data.doctor_id,
            models.Appointment.scheduled_time == appointment_data.scheduled_time,
            models.Appointment.status.in_(ACTIVE_STATUSES + ("completed",)),
        )
        .first()
    )
    if collision:
        raise HTTPException(
            status_code=409,
            detail="Bu shifokor shu vaqtga band — boshqa vaqt tanlang (double booking taqiqlangan)",
        )

    new_appointment = models.Appointment(
        patient_id=appointment_data.patient_id,
        doctor_id=appointment_data.doctor_id,
        scheduled_time=appointment_data.scheduled_time,
        status="waiting",
        price=doctor.consultation_price,
    )
    db.add(new_appointment)
    db.commit()
    db.refresh(new_appointment)
    log_action(
        db, user, "appointment.book", "Appointment", new_appointment.id,
        f"patient_id={new_appointment.patient_id}, doctor_id={new_appointment.doctor_id}, "
        f"scheduled_time={new_appointment.scheduled_time}",
    )
    return _to_detail(new_appointment)


@router.get("/list", response_model=List[schemas.AppointmentDetail])
def list_appointments(db: Session = Depends(get_db)) -> List[schemas.AppointmentDetail]:
    appointments = _detail_query(db).order_by(models.Appointment.scheduled_time.desc()).all()
    return [_to_detail(a) for a in appointments]


@router.get("/patient/{patient_id}", response_model=List[schemas.AppointmentDetail])
def list_appointments_for_patient(
    patient_id: int = Path(..., ge=1, le=2147483647),
    db: Session = Depends(get_db)
) -> List[schemas.AppointmentDetail]:
    appointments = (
        _detail_query(db)
        .filter(models.Appointment.patient_id == patient_id)
        .order_by(models.Appointment.scheduled_time.desc())
        .all()
    )
    return [_to_detail(a) for a in appointments]


@router.get("/doctor/{doctor_id}", response_model=List[schemas.AppointmentDetail])
def list_appointments_for_doctor(
    doctor_id: int = Path(..., ge=1, le=2147483647),
    db: Session = Depends(get_db)
) -> List[schemas.AppointmentDetail]:
    appointments = (
        _detail_query(db)
        .filter(models.Appointment.doctor_id == doctor_id)
        .order_by(models.Appointment.scheduled_time.desc())
        .all()
    )
    return [_to_detail(a) for a in appointments]


# ── Dynamic path parameter routes ───────────────────────────────────
@router.patch("/{appointment_id}/status", response_model=schemas.AppointmentDetail)
def update_appointment_status(
    appointment_id: int = Path(..., ge=1, le=2147483647),
    status_update: schemas.AppointmentStatusUpdate = ...,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> schemas.AppointmentDetail:
    appointment = _get_appointment_or_404(db, appointment_id)

    # Shifokor faqat O'ZINING navbatlarini o'zgartira oladi.
    if user.role == "doctor" and appointment.doctor_id != user.doctor_id:
        raise HTTPException(status_code=403, detail="Faqat o'zingizning qabullaringiz holatini o'zgartira olasiz")
    if user.role not in ("admin", "reception", "doctor"):
        raise HTTPException(status_code=403, detail="Bu amal uchun ruxsat yo'q")

    current = appointment.status
    target = status_update.status

    if current == target:
        return _to_detail(appointment)

    allowed_next = models.APPOINTMENT_TRANSITIONS.get(current, set())
    if target not in allowed_next:
        raise HTTPException(
            status_code=409,
            detail=f"'{current}' holatidan '{target}' holatiga o'tish mumkin emas "
            f"(ruxsat etilgan: {sorted(allowed_next) or 'hech biri — bu yakuniy holat'})",
        )

    if target == "completed" and appointment.debt > 0:
        raise HTTPException(
            status_code=409,
            detail=f"To'liq to'lanmagan qabulni 'tugadi' deb belgilab bo'lmaydi "
            f"(qarz: {appointment.debt:,} UZS)",
        )

    appointment.status = target
    db.commit()
    db.refresh(appointment)
    log_action(
        db, user, "appointment.status_change", "Appointment", appointment.id,
        f"{current} -> {target}",
    )
    return _to_detail(appointment)


@router.put(
    "/{appointment_id}/reschedule",
    response_model=schemas.AppointmentDetail,
    dependencies=[Depends(require_role("admin", "reception"))],
)
def reschedule_appointment(
    appointment_id: int = Path(..., ge=1, le=2147483647),
    data: schemas.AppointmentReschedule = ...,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> schemas.AppointmentDetail:
    """README audit point #8 ("tahrirlash" — editing) promised an edit
    action for appointments, but only cancel existed. This is the
    missing piece: reschedule an active appointment's time, subject to
    the same rules booking already enforces (no past dates, no double
    booking)."""
    appointment = _get_appointment_or_404(db, appointment_id)

    if appointment.status not in ACTIVE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"'{appointment.status}' holatidagi qabulni ko'chirib bo'lmaydi "
            f"(faqat faol qabullar: {', '.join(ACTIVE_STATUSES)})",
        )

    if data.scheduled_time < dt.now().replace(second=0, microsecond=0):
        raise HTTPException(status_code=400, detail="O'tmish sanaga qabul ko'chirib bo'lmaydi")

    collision = (
        db.query(models.Appointment)
        .filter(
            models.Appointment.id != appointment_id,
            models.Appointment.doctor_id == appointment.doctor_id,
            models.Appointment.scheduled_time == data.scheduled_time,
            models.Appointment.status.in_(ACTIVE_STATUSES + ("completed",)),
        )
        .first()
    )
    if collision:
        raise HTTPException(
            status_code=409,
            detail="Bu shifokor shu vaqtga band — boshqa vaqt tanlang (double booking taqiqlangan)",
        )

    appointment.scheduled_time = data.scheduled_time
    db.commit()
    db.refresh(appointment)
    log_action(db, user, "appointment.reschedule", "Appointment", appointment.id, f"new_time={data.scheduled_time}")
    return _to_detail(appointment)


@router.patch(
    "/{appointment_id}/cancel",
    response_model=schemas.AppointmentDetail,
    dependencies=[Depends(require_role("admin", "reception"))],
)
def cancel_appointment(
    appointment_id: int = Path(..., ge=1, le=2147483647),
    cancel_data: schemas.AppointmentCancel = ...,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> schemas.AppointmentDetail:
    appointment = _get_appointment_or_404(db, appointment_id)
    if appointment.status not in models.APPOINTMENT_TRANSITIONS:
        raise HTTPException(status_code=409, detail=f"'{appointment.status}' holatidagi qabulni bekor qilib bo'lmaydi")
    appointment.status = "cancelled"
    appointment.cancel_reason = cancel_data.reason
    db.commit()
    db.refresh(appointment)
    log_action(db, user, "appointment.cancel", "Appointment", appointment.id, f"reason={cancel_data.reason}")
    return _to_detail(appointment)


@router.get("/{appointment_id}", response_model=schemas.AppointmentDetail)
def get_appointment(
    appointment_id: int = Path(..., ge=1, le=2147483647),
    db: Session = Depends(get_db)
) -> schemas.AppointmentDetail:
    return _to_detail(_get_appointment_or_404(db, appointment_id))


def register_module() -> Dict[str, object]:
    return {
        "module_name": "Appointments",
        "version": "3.1.0",
        "router": router,
    }