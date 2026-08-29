# modules/dashboard.py
"""
Dashboard module: KPI summary + live queue aggregation.

Audit fix #4: "Bugungi navbat" now actually means today. The live queue
filters scheduled_time to [today 00:00, tomorrow 00:00) AND restricts to
active statuses (waiting/in_progress/delayed) — an appointment scheduled
for tomorrow at 15:00 no longer shows up in "today's queue" just because
nobody changed its status yet. The KPI summary also now separates
"today" numbers from "all-time" numbers instead of mixing them (fix #8).
"""
from datetime import datetime, time, timedelta
from typing import Dict, List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload

import models
import schemas
from auth import get_current_user
from database import get_db

# ⬅️ YANGI (1-band, KRITIK): login majburiy — /summary va /queue KPI va
# jonli navbat (bemor ismlari, qarzlar) login'siz ochiq edi.
router = APIRouter(
    prefix="/api/dashboard",
    tags=["Dashboard"],
    dependencies=[Depends(get_current_user)],
)


def _today_bounds() -> tuple[datetime, datetime]:
    today = datetime.now().date()
    start = datetime.combine(today, time.min)
    end = start + timedelta(days=1)
    return start, end


def get_dashboard_summary(db: Session) -> schemas.DashboardSummary:
    """📊 Real bazadan KPI hisob-kitoblarini olish — 'bugungi' va 'jami'
    ko'rsatkichlar aniq ajratilgan."""
    start, end = _today_bounds()

    total_patients = db.query(models.Patient).count()

    today_appointments_q = db.query(models.Appointment).filter(
        models.Appointment.scheduled_time >= start,
        models.Appointment.scheduled_time < end,
    )
    today_appointments = today_appointments_q.count()
    today_waiting = today_appointments_q.filter(
        models.Appointment.status.in_(("waiting", "delayed"))
    ).count()
    today_completed = today_appointments_q.filter(models.Appointment.status == "completed").count()

    # joinedload — a.debt -> a.paid_amount har bir qabul uchun payments'ni
    # o'qiydi; eager load bo'lmasa dashboard sahifasi bazadagi qabullar
    # soniga proporsional so'rov yuboradi (1000 qabul = 1000+ so'rov).
    all_appointments = (
        db.query(models.Appointment)
        .options(joinedload(models.Appointment.payments))
        .filter(models.Appointment.status != "cancelled")
        .all()
    )
    total_debt = sum(a.debt for a in all_appointments)

    all_payments = db.query(models.Payment).all()
    total_revenue = sum(p.amount for p in all_payments if not p.is_refund) - sum(
        p.amount for p in all_payments if p.is_refund
    )

    today_payments = db.query(models.Payment).filter(
        models.Payment.created_at >= start, models.Payment.created_at < end
    ).all()
    today_revenue = sum(p.amount for p in today_payments if not p.is_refund) - sum(
        p.amount for p in today_payments if p.is_refund
    )

    return schemas.DashboardSummary(
        total_patients=total_patients,
        today_appointments=today_appointments,
        today_waiting=today_waiting,
        today_completed=today_completed,
        today_revenue=f"{today_revenue:,} UZS",
        total_revenue=f"{total_revenue:,} UZS",
        total_debt=f"{total_debt:,} UZS",
    )


def get_live_queue(db: Session) -> List[schemas.AppointmentQueueItem]:
    """📋 FAQAT bugungi, hali yakunlanmagan qabullar (waiting/in_progress/
    delayed) — kecha yoki ertaga rejalashtirilgan qabullar bu yerda
    ko'rinmaydi, statusidan qat'i nazar."""
    start, end = _today_bounds()
    appointments = (
        db.query(models.Appointment)
        .options(
            joinedload(models.Appointment.patient),
            joinedload(models.Appointment.doctor),
            joinedload(models.Appointment.payments),
        )
        .filter(
            models.Appointment.scheduled_time >= start,
            models.Appointment.scheduled_time < end,
            models.Appointment.status.in_(("waiting", "in_progress", "delayed")),
        )
        .order_by(models.Appointment.scheduled_time.asc())
        .all()
    )

    queue: List[schemas.AppointmentQueueItem] = []
    for appt in appointments:
        queue.append(
            schemas.AppointmentQueueItem(
                id=f"#{appt.id}",
                patient_id=appt.patient_id,
                doctor_id=appt.doctor_id,
                patient_name=appt.patient.fullname if appt.patient else "Noma'lum",
                doctor_name=(
                    f"{appt.doctor.fullname} ({appt.doctor.specialty})" if appt.doctor else "Noma'lum"
                ),
                time=appt.scheduled_time.strftime("%H:%M"),
                status=appt.status,
                debt=appt.debt,
            )
        )
    return queue


@router.get("/summary", response_model=schemas.DashboardSummary)
def api_get_dashboard_summary(db: Session = Depends(get_db)) -> schemas.DashboardSummary:
    return get_dashboard_summary(db)


@router.get("/queue", response_model=List[schemas.AppointmentQueueItem])
def api_get_live_queue(db: Session = Depends(get_db)) -> List[schemas.AppointmentQueueItem]:
    return get_live_queue(db)


def register_module() -> Dict[str, object]:
    return {
        "module_name": "Dashboard",
        "version": "3.0.0",
        "router": router,
    }
