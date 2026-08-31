# modules/reports.py
"""
Reports module: admin uchun kengaytirilgan hisobot va analitika.

1-bosqich (bu fayl): sana oralig'i filtri infratuzilmasi + umumiy hajm
ko'rsatkichlari (jami bemorlar, faol/nofaol shifokorlar, tanlangan
davrdagi qabullar soni). Keyingi bosqichlar (status taqsimoti, shifokor
samaradorligi, bekor qilish sabablari, band soat/kun tahlili, yangi/
qaytgan bemorlar, davr taqqoslash, CSV eksport) shu faylning ustiga
qo'shiladi — barchasi require_role("admin") bilan himoyalangan bo'ladi,
chunki hisobot butun klinikaning moliyaviy/operatsion ma'lumotini
ko'rsatadi va faqat admin uchun mo'ljallangan (main.py dagi reports_page
ham shu bilan izchil ravishda faqat adminga ochiq).
"""
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional, Tuple
import csv
import io

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

import models
import schemas
from auth import require_role
from database import get_db

router = APIRouter(
    prefix="/api/reports",
    tags=["Reports"],
    dependencies=[Depends(require_role("admin"))],
)


def parse_date_range(
    date_from: Optional[str], date_to: Optional[str]
) -> Tuple[datetime, datetime]:
    """Sana oralig'ini so'rov parametrlaridan (YYYY-MM-DD matn) datetime
    juftligiga o'giradi.

    Ikkalasi ham berilmasa: default — joriy oyning 1-sanasi soat 00:00
    dan bugungi kun oxirigacha (23:59:59.999999). Berilgan bo'lsa, shu
    qiymatlar ishlatiladi — date_to HAR DOIM kun oxirigacha kengaytiriladi
    (foydalanuvchi "2026-08-30" desa, shu kunning oxirigacha qabullar ham
    hisobga olinishi kerak, aks holda soat 00:00 dan keyingi barcha
    yozuvlar chetda qolib ketadi).

    Noto'g'ri formatdagi sana kelsa (masalan bo'sh satr yoki buzilgan
    matn), ValueError ko'tarmaydi — jim ravishda default qiymatga
    qaytadi, chunki bu funksiya sahifa render qilishda ham ishlatiladi
    va foydalanuvchiga 500 xato ko'rsatish o'rniga oqilona defaultga
    tushish afzalroq.
    """
    today = datetime.now().date()

    start_date = None
    end_date = None

    if date_from:
        try:
            start_date = datetime.strptime(date_from, "%Y-%m-%d").date()
        except ValueError:
            start_date = None

    if date_to:
        try:
            end_date = datetime.strptime(date_to, "%Y-%m-%d").date()
        except ValueError:
            end_date = None

    if start_date is None and end_date is None:
        start_date = today.replace(day=1)
        end_date = today
    elif start_date is None:
        start_date = end_date.replace(day=1)
    elif end_date is None:
        end_date = today

    start = datetime.combine(start_date, time.min)
    # Kun oxiri: keyingi kunning 00:00 dan 1 mikrosoniya oldin — bu
    # scheduled_time < end (qat'iy kichik) filtriga ham,
    # scheduled_time <= end filtriga ham to'g'ri ishlaydi.
    end = datetime.combine(end_date, time.max)

    return start, end


def get_report_overview(db: Session, start: datetime, end: datetime) -> schemas.ReportOverview:
    """Umumiy hajm ko'rsatkichlari:
    - total_patients / total_doctors_active / total_doctors_inactive —
      BUTUN tizim bo'yicha, tanlangan davrga bog'liq emas (bemor yoki
      shifokor "davr ichida qo'shildimi" emas, hozirgi holat so'raladi).
    - period_appointments — FAQAT tanlangan davr ichida rejalashtirilgan
      (scheduled_time shu oraliqda) qabullar soni.
    """
    total_patients = db.query(models.Patient).count()
    total_doctors_active = (
        db.query(models.Doctor).filter(models.Doctor.is_active.is_(True)).count()
    )
    total_doctors_inactive = (
        db.query(models.Doctor).filter(models.Doctor.is_active.is_(False)).count()
    )
    period_appointments = (
        db.query(models.Appointment)
        .filter(
            models.Appointment.scheduled_time >= start,
            models.Appointment.scheduled_time <= end,
        )
        .count()
    )

    return schemas.ReportOverview(
        total_patients=total_patients,
        total_doctors_active=total_doctors_active,
        total_doctors_inactive=total_doctors_inactive,
        period_appointments=period_appointments,
    )


@router.get("/overview", response_model=schemas.ReportOverview)
def api_get_report_overview(
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
) -> schemas.ReportOverview:
    start, end = parse_date_range(date_from, date_to)
    return get_report_overview(db, start, end)


# ── 2-bosqich: status bo'yicha taqsimot ────────────────────────────────
STATUS_LABELS: Dict[str, str] = {
    "waiting": "Kutmoqda",
    "in_progress": "Jarayonda",
    "delayed": "Kechikdi",
    "completed": "Tugadi",
    "cancelled": "Bekor qilindi",
    "no_show": "Kelmadi",
}


def get_status_breakdown(
    db: Session, start: datetime, end: datetime
) -> schemas.ReportStatusBreakdown:
    """Tanlangan davrdagi qabullarni status bo'yicha taqsimlaydi.
    models.APPOINTMENT_STATUSES dagi HAR BIR status uchun qator qaytadi
    (davrda umuman uchramagan status ham count=0, percentage=0.0 bilan
    ro'yxatda bo'ladi — front-end'da "yo'q" holatni alohida ishlash
    shart bo'lmasin deb)."""
    period_appointments = (
        db.query(models.Appointment)
        .filter(
            models.Appointment.scheduled_time >= start,
            models.Appointment.scheduled_time <= end,
        )
        .all()
    )
    total = len(period_appointments)

    counts: Dict[str, int] = {status: 0 for status in models.APPOINTMENT_STATUSES}
    for appt in period_appointments:
        if appt.status in counts:
            counts[appt.status] += 1

    items = [
        schemas.StatusCount(
            status=status,
            label=STATUS_LABELS.get(status, status),
            count=counts[status],
            percentage=round((counts[status] / total * 100), 1) if total > 0 else 0.0,
        )
        for status in models.APPOINTMENT_STATUSES
    ]

    return schemas.ReportStatusBreakdown(items=items, total=total)


@router.get("/status-breakdown", response_model=schemas.ReportStatusBreakdown)
def api_get_status_breakdown(
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
) -> schemas.ReportStatusBreakdown:
    start, end = parse_date_range(date_from, date_to)
    return get_status_breakdown(db, start, end)


# ── 3-bosqich: shifokor samaradorligi ──────────────────────────────────
def get_doctor_performance(
    db: Session, start: datetime, end: datetime
) -> List[schemas.DoctorPerformanceRow]:
    """Har bir faol shifokor (Doctor.is_active) uchun tanlangan davrdagi
    qabullarini status bo'yicha sanab, natijani jami qabullar soni
    (total) bo'yicha kamayish tartibida qaytaradi.

    revenue — shu shifokorning shu davrdagi FAQAT "completed" statusidagi
    qabullarining price ustunlari yig'indisi (bron paytidagi narx,
    doctor.consultation_price emas — qarang: Appointment.price izohi).

    Davrda umuman qabul qilmagan faol shifokor ham ro'yxatda bo'ladi,
    barcha sonlar 0 bilan — front-end alohida "yo'q" holatni ishlamasin
    deb (get_status_breakdown bilan bir xil naqsh).
    """
    active_doctors = (
        db.query(models.Doctor).filter(models.Doctor.is_active.is_(True)).all()
    )

    rows: List[schemas.DoctorPerformanceRow] = []
    for doctor in active_doctors:
        appointments = (
            db.query(models.Appointment)
            .filter(
                models.Appointment.doctor_id == doctor.id,
                models.Appointment.scheduled_time >= start,
                models.Appointment.scheduled_time <= end,
            )
            .all()
        )

        total = len(appointments)
        completed = 0
        cancelled = 0
        delayed = 0
        no_show = 0
        revenue = 0
        for appt in appointments:
            if appt.status == "completed":
                completed += 1
                revenue += appt.price
            elif appt.status == "cancelled":
                cancelled += 1
            elif appt.status == "delayed":
                delayed += 1
            elif appt.status == "no_show":
                no_show += 1

        rows.append(
            schemas.DoctorPerformanceRow(
                doctor_id=doctor.id,
                doctor_name=doctor.fullname,
                specialty=doctor.specialty,
                total=total,
                completed=completed,
                cancelled=cancelled,
                delayed=delayed,
                no_show=no_show,
                revenue=revenue,
            )
        )

    rows.sort(key=lambda row: row.total, reverse=True)
    return rows


@router.get("/doctor-performance", response_model=List[schemas.DoctorPerformanceRow])
def api_get_doctor_performance(
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
) -> List[schemas.DoctorPerformanceRow]:
    start, end = parse_date_range(date_from, date_to)
    return get_doctor_performance(db, start, end)


# ── 4-bosqich: bekor qilish sabablari ──────────────────────────────────
UNSPECIFIED_CANCEL_REASON = "Sabab ko'rsatilmagan"


def get_cancel_reasons(
    db: Session, start: datetime, end: datetime
) -> List[schemas.CancelReasonRow]:
    """Tanlangan davrdagi "cancelled" statusdagi qabullarni cancel_reason
    bo'yicha guruhlaydi. cancel_reason NULL yoki bo'sh satr bo'lsa,
    UNSPECIFIED_CANCEL_REASON qatoriga qo'shiladi. Natija soni bo'yicha
    kamayish tartibida saralanadi, foizi jami bekor qilinganlarga
    nisbatan hisoblanadi.
    """
    cancelled_appointments = (
        db.query(models.Appointment)
        .filter(
            models.Appointment.status == "cancelled",
            models.Appointment.scheduled_time >= start,
            models.Appointment.scheduled_time <= end,
        )
        .all()
    )

    total = len(cancelled_appointments)

    counts: Dict[str, int] = {}
    for appt in cancelled_appointments:
        reason = (appt.cancel_reason or "").strip() or UNSPECIFIED_CANCEL_REASON
        counts[reason] = counts.get(reason, 0) + 1

    rows = [
        schemas.CancelReasonRow(
            reason=reason,
            count=count,
            percentage=round((count / total * 100), 1) if total > 0 else 0.0,
        )
        for reason, count in counts.items()
    ]
    rows.sort(key=lambda row: row.count, reverse=True)
    return rows


@router.get("/cancel-reasons", response_model=List[schemas.CancelReasonRow])
def api_get_cancel_reasons(
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
) -> List[schemas.CancelReasonRow]:
    start, end = parse_date_range(date_from, date_to)
    return get_cancel_reasons(db, start, end)


# ── 5-bosqich: band vaqt tahlili ────────────────────────────────────────
WEEKDAY_LABELS: Dict[int, str] = {
    0: "Dushanba",
    1: "Seshanba",
    2: "Chorshanba",
    3: "Payshanba",
    4: "Juma",
    5: "Shanba",
    6: "Yakshanba",
}


def get_hourly_load(db: Session, start: datetime, end: datetime) -> List[schemas.HourlyLoadRow]:
    """Tanlangan davrdagi barcha qabullarni scheduled_time.hour (0-23)
    bo'yicha guruhlaydi. Har bir soat uchun qator qaytadi — davrda
    umuman qabul bo'lmagan soat ham count=0 bilan ro'yxatda bo'ladi,
    front-end grafik uchun to'liq X o'qi kerak (0-23) bo'lgani sababli.
    """
    appointments = (
        db.query(models.Appointment)
        .filter(
            models.Appointment.scheduled_time >= start,
            models.Appointment.scheduled_time <= end,
        )
        .all()
    )

    counts: Dict[int, int] = {hour: 0 for hour in range(24)}
    for appt in appointments:
        counts[appt.scheduled_time.hour] += 1

    return [schemas.HourlyLoadRow(hour=hour, count=counts[hour]) for hour in range(24)]


def get_weekday_load(db: Session, start: datetime, end: datetime) -> List[schemas.WeekdayLoadRow]:
    """Tanlangan davrdagi barcha qabullarni haftaning kuni bo'yicha
    guruhlaydi (scheduled_time.weekday(): 0=Dushanba ... 6=Yakshanba).
    7 kunning barchasi qaytadi, davrda qabul bo'lmagan kun ham count=0
    bilan ro'yxatda bo'ladi.
    """
    appointments = (
        db.query(models.Appointment)
        .filter(
            models.Appointment.scheduled_time >= start,
            models.Appointment.scheduled_time <= end,
        )
        .all()
    )

    counts: Dict[int, int] = {weekday: 0 for weekday in range(7)}
    for appt in appointments:
        counts[appt.scheduled_time.weekday()] += 1

    return [
        schemas.WeekdayLoadRow(
            weekday=weekday, weekday_label=WEEKDAY_LABELS[weekday], count=counts[weekday]
        )
        for weekday in range(7)
    ]


@router.get("/hourly-load", response_model=List[schemas.HourlyLoadRow])
def api_get_hourly_load(
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
) -> List[schemas.HourlyLoadRow]:
    start, end = parse_date_range(date_from, date_to)
    return get_hourly_load(db, start, end)


@router.get("/weekday-load", response_model=List[schemas.WeekdayLoadRow])
def api_get_weekday_load(
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
) -> List[schemas.WeekdayLoadRow]:
    start, end = parse_date_range(date_from, date_to)
    return get_weekday_load(db, start, end)


# ── 6-bosqich: yangi/qaytgan bemorlar nisbati ──────────────────────────
def get_patient_retention(db: Session, start: datetime, end: datetime) -> schemas.PatientRetention:
    """Tanlangan davrda kamida bitta appointmenti bor bemorlarni "yangi"
    (davrdagi appointment — shu bemorning eng birinchi appointmenti) va
    "qaytgan" (birinchi appointmenti bu davrdan oldin bo'lgan) deb
    ikkiga bo'ladi.

    N+1 querydan qochish uchun: avval shu davrda appointmenti bor
    patient_id'lar ro'yxati bitta so'rovda olinadi, so'ng ular uchun
    HAR BIR bemorning (davrga bog'liq bo'lmagan, umumiy) eng birinchi
    appointment sanasi bitta GROUP BY so'rovda olinadi — patient boshiga
    alohida so'rov yo'q.
    """
    period_patient_ids = [
        row[0]
        for row in db.query(models.Appointment.patient_id)
        .filter(
            models.Appointment.scheduled_time >= start,
            models.Appointment.scheduled_time <= end,
        )
        .distinct()
        .all()
    ]

    if not period_patient_ids:
        return schemas.PatientRetention(
            new_patients=0,
            returning_patients=0,
            new_percentage=0.0,
            returning_percentage=0.0,
        )

    first_appointment_rows = (
        db.query(
            models.Appointment.patient_id,
            func.min(models.Appointment.scheduled_time),
        )
        .filter(models.Appointment.patient_id.in_(period_patient_ids))
        .group_by(models.Appointment.patient_id)
        .all()
    )
    first_appointment_by_patient: Dict[int, datetime] = {
        patient_id: first_scheduled_time for patient_id, first_scheduled_time in first_appointment_rows
    }

    new_patients = 0
    returning_patients = 0
    for patient_id in period_patient_ids:
        first_time = first_appointment_by_patient.get(patient_id)
        if first_time is not None and start <= first_time <= end:
            new_patients += 1
        else:
            returning_patients += 1

    total = new_patients + returning_patients
    return schemas.PatientRetention(
        new_patients=new_patients,
        returning_patients=returning_patients,
        new_percentage=round((new_patients / total * 100), 1) if total > 0 else 0.0,
        returning_percentage=round((returning_patients / total * 100), 1) if total > 0 else 0.0,
    )


@router.get("/patient-retention", response_model=schemas.PatientRetention)
def api_get_patient_retention(
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
) -> schemas.PatientRetention:
    start, end = parse_date_range(date_from, date_to)
    return get_patient_retention(db, start, end)


# ── 7-bosqich: oylik davr taqqoslash ────────────────────────────────────
def _month_bounds(year: int, month: int) -> Tuple[datetime, datetime]:
    """Berilgan yil/oy uchun (oy boshi 00:00, oy oxiri 23:59:59.999999)
    juftligini qaytaradi."""
    month_start = datetime(year, month, 1)
    if month == 12:
        next_month_start = datetime(year + 1, 1, 1)
    else:
        next_month_start = datetime(year, month + 1, 1)
    month_end = next_month_start - timedelta(microseconds=1)
    return month_start, month_end


def get_period_trend(db: Session, months: int = 6) -> List[schemas.PeriodTrendRow]:
    """Joriy oydan orqaga qarab oxirgi `months` ta oyning har biri uchun
    (eng eskisidan eng yangisigacha tartiblangan) jami/tugallangan/bekor
    qilingan appointmentlar soni va tushumni hisoblaydi.

    Sana filtridan MUSTAQIL ishlaydi — har doim joriy sanaga nisbatan
    oxirgi `months` ta oy, foydalanuvchi tanlagan date_from/date_to'ga
    bog'liq emas.
    """
    today = datetime.now().date()
    year, month = today.year, today.month

    month_pairs: List[Tuple[int, int]] = []
    for _ in range(months):
        month_pairs.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    month_pairs.reverse()  # eng eskisidan eng yangisigacha

    rows: List[schemas.PeriodTrendRow] = []
    for y, m in month_pairs:
        month_start, month_end = _month_bounds(y, m)
        appointments = (
            db.query(models.Appointment)
            .filter(
                models.Appointment.scheduled_time >= month_start,
                models.Appointment.scheduled_time <= month_end,
            )
            .all()
        )

        total = len(appointments)
        completed = 0
        cancelled = 0
        revenue = 0
        for appt in appointments:
            if appt.status == "completed":
                completed += 1
                revenue += appt.price
            elif appt.status == "cancelled":
                cancelled += 1

        rows.append(
            schemas.PeriodTrendRow(
                period_label=f"{y:04d}-{m:02d}",
                appointments=total,
                completed=completed,
                cancelled=cancelled,
                revenue=revenue,
            )
        )

    return rows


@router.get("/period-trend", response_model=List[schemas.PeriodTrendRow])
def api_get_period_trend(
    months: int = Query(default=6),
    db: Session = Depends(get_db),
) -> List[schemas.PeriodTrendRow]:
    return get_period_trend(db, months)


# ── 8-bosqich: CSV eksport ───────────────────────────────────────────────
@router.get("/export/csv")
def export_report_csv(
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Tanlangan davr uchun umumiy hajm, status taqsimoti va shifokor
    samaradorligi bo'limlarini bitta CSV faylda ketma-ket, bo'limlar
    orasida bo'sh qator va sarlavha bilan birlashtirib yozadi.
    modules/payments.py dagi export_payments_csv naqshiga mos: standart
    `csv` moduli + UTF-8 BOM (Excel'da kirill/lotin harflar to'g'ri
    chiqishi uchun) + StreamingResponse.
    """
    start, end = parse_date_range(date_from, date_to)
    overview = get_report_overview(db, start, end)
    status_breakdown = get_status_breakdown(db, start, end)
    doctor_performance = get_doctor_performance(db, start, end)

    date_from_str = start.strftime("%Y-%m-%d")
    date_to_str = end.strftime("%Y-%m-%d")

    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer)

    writer.writerow([f"Hisobot ({date_from_str} — {date_to_str})"])
    writer.writerow([])

    writer.writerow(["Umumiy hajm ko'rsatkichlari"])
    writer.writerow(["Ko'rsatkich", "Qiymat"])
    writer.writerow(["Jami bemorlar", overview.total_patients])
    writer.writerow(["Faol shifokorlar", overview.total_doctors_active])
    writer.writerow(["Nofaol shifokorlar", overview.total_doctors_inactive])
    writer.writerow(["Tanlangan davrdagi qabullar", overview.period_appointments])
    writer.writerow([])

    writer.writerow(["Status bo'yicha taqsimot"])
    writer.writerow(["Status", "Soni", "Foizi"])
    for item in status_breakdown.items:
        writer.writerow([item.label, item.count, f"{item.percentage}%"])
    writer.writerow([])

    writer.writerow(["Shifokor bo'yicha samaradorlik"])
    writer.writerow(
        ["Shifokor", "Mutaxassislik", "Jami", "Tugagan", "Bekor qilingan", "Kechikkan", "Kelmagan", "Tushum"]
    )
    for row in doctor_performance:
        writer.writerow(
            [
                row.doctor_name,
                row.specialty,
                row.total,
                row.completed,
                row.cancelled,
                row.delayed,
                row.no_show,
                row.revenue,
            ]
        )

    buffer.seek(0)
    filename = f"hisobot_{date_from_str}_{date_to_str}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def register_module() -> Dict[str, object]:
    return {
        "module_name": "Reports",
        "version": "1.0.0",
        "router": router,
    }
