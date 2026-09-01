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


def get_dashboard_summary_by_role(db: Session, user: models.User) -> schemas.RoleDashboardSummary:
    """📊 Prompt 4: har bir rol faqat o'ziga tegishli ko'rsatkichlarni oladi.
    Boshqa ko'rsatkichlar schemas.RoleDashboardSummary'da None (frontend
    "—" sifatida ko'rsatadi)."""
    start, end = _today_bounds()
    role = user.role
    data: Dict[str, object] = {"role": role}

    def _appt_today():
        return db.query(models.Appointment).filter(
            models.Appointment.scheduled_time >= start,
            models.Appointment.scheduled_time < end,
        )

    if role in ("admin", "assistant_admin"):
        data["total_patients"] = db.query(models.Patient).count()

    if role in ("admin", "cashier", "reception", "assistant_admin"):
        data["today_appointments"] = _appt_today().count()

    if role in ("admin", "reception", "assistant_admin"):
        data["waiting_patients"] = _appt_today().filter(
            models.Appointment.status.in_(("waiting", "delayed"))
        ).count()

    if role in ("admin", "reception"):
        data["completed_appointments"] = _appt_today().filter(
            models.Appointment.status == "completed"
        ).count()

    if role in ("admin", "cashier", "assistant_admin"):
        today_payments = db.query(models.Payment).filter(
            models.Payment.created_at >= start, models.Payment.created_at < end
        ).all()
        revenue = sum(p.amount for p in today_payments if not p.is_refund) - sum(
            p.amount for p in today_payments if p.is_refund
        )
        data["today_revenue"] = f"{revenue:,} UZS"
        if role == "cashier":
            data["today_payments_count"] = sum(1 for p in today_payments if not p.is_refund)
            refunds = sum(p.amount for p in today_payments if p.is_refund)
            data["today_refunds"] = f"{refunds:,} UZS"

    if role in ("admin", "cashier"):
        active_appointments = (
            db.query(models.Appointment)
            .options(joinedload(models.Appointment.payments))
            .filter(models.Appointment.status != "cancelled")
            .all()
        )
        total_debt = sum(a.debt for a in active_appointments)
        data["total_debt"] = f"{total_debt:,} UZS"
        if role == "cashier":
            today_active = [a for a in active_appointments if start <= a.scheduled_time < end]
            data["pending_payments"] = sum(1 for a in today_active if a.debt > 0)

    # Prompt 6: ikki bosqichli qaytarim ko'rsatkichlari.
    if role == "cashier":
        data["pending_refunds"] = db.query(models.Payment).filter(
            models.Payment.status == "cancelled"
        ).count()

    if role == "admin":
        data["cancelled_payments"] = db.query(models.Payment).filter(
            models.Payment.status == "cancelled"
        ).count()

    if role == "reception":
        data["new_patients_today"] = db.query(models.Patient).filter(
            models.Patient.created_at >= start, models.Patient.created_at < end
        ).count()

    # Prompt 7: "Hozirgi palatadagi bemorlar" — faqat admin va reception.
    if role in ("admin", "reception"):
        data["admitted_patients_count"] = db.query(models.Patient).filter(
            models.Patient.is_admitted.is_(True)
        ).count()

    if role == "doctor":
        doc_id = user.doctor_id
        if doc_id is not None:
            doc_today = _appt_today().filter(models.Appointment.doctor_id == doc_id)
            data["my_today_appointments"] = doc_today.count()
            data["my_waiting_patients"] = doc_today.filter(
                models.Appointment.status.in_(("waiting", "delayed"))
            ).count()
            data["my_completed_today"] = doc_today.filter(models.Appointment.status == "completed").count()
            data["my_patients_count"] = (
                db.query(models.Appointment.patient_id)
                .filter(models.Appointment.doctor_id == doc_id)
                .distinct()
                .count()
            )
        else:
            data.update(my_today_appointments=0, my_waiting_patients=0, my_completed_today=0, my_patients_count=0)

    if role == "lab_doctor":
        doc_id = user.doctor_id
        if doc_id is not None:
            data["my_lab_pending"] = db.query(models.LabResult).filter(
                models.LabResult.doctor_id == doc_id, models.LabResult.status == "Kutilmoqda"
            ).count()
            data["my_lab_completed"] = db.query(models.LabResult).filter(
                models.LabResult.doctor_id == doc_id, models.LabResult.status == "Tayyor"
            ).count()
            data["today_lab_requests"] = db.query(models.LabResult).filter(
                models.LabResult.doctor_id == doc_id,
                models.LabResult.created_at >= start,
                models.LabResult.created_at < end,
            ).count()
        else:
            data.update(my_lab_pending=0, my_lab_completed=0, today_lab_requests=0)

    if role == "assistant_admin":
        data["staff_count"] = db.query(models.User).count()

    # Prompt 9: xavfsizlik markazi — badge/hisoblagichlar. admin va
    # assistant_admin ikkalasi ham ko'ra oladi (audit_log bilan bir xil
    # "faqat o'qish" naqsh, qarang: modules/security_center.py).
    if role in ("admin", "assistant_admin"):
        since_24h = datetime.now() - timedelta(hours=24)
        data["unread_security_messages"] = db.query(models.SecurityMessage).filter(
            models.SecurityMessage.is_read.is_(False)
        ).count()
        data["system_errors_24h"] = db.query(models.SystemError).filter(
            models.SystemError.created_at >= since_24h
        ).count()
        data["failed_logins_24h"] = db.query(models.LoginLog).filter(
            models.LoginLog.success.is_(False),
            models.LoginLog.created_at >= since_24h,
        ).count()

    return schemas.RoleDashboardSummary(**data)


def get_live_queue(db: Session, user: models.User) -> List[schemas.AppointmentQueueItem]:
    """📋 FAQAT bugungi, hali yakunlanmagan qabullar (waiting/in_progress/
    delayed), rol bo'yicha filtrlangan:
      - doctor      -> faqat o'ziga biriktirilgan navbatlar
      - lab_doctor  -> navbat ro'yxati yo'q (tahlil ish jarayoni alohida)
      - cashier     -> faqat to'lov kutayotgan (qarzi bor) navbatlar
      - reception/admin/assistant_admin -> barcha navbatlar
    """
    if user.role == "lab_doctor":
        return []

    start, end = _today_bounds()
    q = (
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
    )
    if user.role == "doctor":
        if user.doctor_id is None:
            return []
        q = q.filter(models.Appointment.doctor_id == user.doctor_id)

    appointments = q.order_by(models.Appointment.scheduled_time.asc()).all()
    if user.role == "cashier":
        appointments = [a for a in appointments if a.debt > 0]

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


@router.get("/summary", response_model=schemas.RoleDashboardSummary)
def api_get_dashboard_summary(
    db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
) -> schemas.RoleDashboardSummary:
    return get_dashboard_summary_by_role(db, user)


@router.get("/queue", response_model=List[schemas.AppointmentQueueItem])
def api_get_live_queue(
    db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
) -> List[schemas.AppointmentQueueItem]:
    return get_live_queue(db, user)


def register_module() -> Dict[str, object]:
    return {
        "module_name": "Dashboard",
        "version": "3.0.0",
        "router": router,
    }
