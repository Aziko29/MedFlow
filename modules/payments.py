"""
Payment recording API — audit fix #1: every Payment MUST belong to an
Appointment (appointment_id is required, not an optional afterthought).
A cashier can no longer add money to a patient's account without saying
which service it was for.

Also adds "Bekor qilish / qaytarish" (refund) — audit point #8 — as an
is_refund=True audit row rather than deleting the original payment, so
the money trail stays intact.

v3.1 fixes (post-launch review):
  - refund_of_payment_id is now a real FK (models.Payment) instead of
    matching on the free-text `note` string. The old approach could
    misfire if a cashier's own note happened to match the pattern, and
    it silently trusted client-controlled text for an integrity check.
  - Refunding a payment that belongs to a COMPLETED appointment now
    re-opens the appointment (completed -> in_progress) if the refund
    creates new debt. Previously an appointment could stay "completed"
    while owing money — silently violating the very invariant the
    state machine (modules/appointments.py) is supposed to guarantee.
"""
from typing import Dict, List, Set
import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload

import models
import schemas
from audit import log_action
from auth import get_current_user, require_role
from database import get_db

# ⬅️ YANGI (1-band, KRITIK): login majburiy — bu modul eng og'ir audit
# topilmasi edi: GET /api/payments/list login'siz BARCHA to'lovlarni (kim,
# qancha, qaysi bemor) ochib berardi.
router = APIRouter(
    prefix="/api/payments",
    tags=["Payments"],
    dependencies=[Depends(get_current_user)],
)


def _refunded_payment_ids(db: Session, payment_ids: List[int]) -> Set[int]:
    """Bitta so'rovda: shu ro'yxatdagi qaysi to'lovlar allaqachon
    qaytarilgan (ular uchun refund_of_payment_id bilan bog'langan yozuv
    bormi). N+1'ning oldini olish uchun bitta IN so'rov."""
    if not payment_ids:
        return set()
    rows = (
        db.query(models.Payment.refund_of_payment_id)
        .filter(models.Payment.refund_of_payment_id.in_(payment_ids))
        .all()
    )
    return {row[0] for row in rows}


def _to_list_item(payment: models.Payment, refunded_ids: Set[int]) -> schemas.PaymentListItem:
    return schemas.PaymentListItem(
        id=payment.id,
        patient_id=payment.patient_id,
        patient_name=payment.patient.fullname if payment.patient else "Noma'lum",
        appointment_id=payment.appointment_id,
        amount=payment.amount,
        note=payment.note,
        is_refund=payment.is_refund,
        refund_of_payment_id=payment.refund_of_payment_id,
        is_refunded=payment.id in refunded_ids,
        created_at=payment.created_at,
    )


# ── Static/action routes first ──────────────────────────────────────
@router.post(
    "/add",
    response_model=schemas.PaymentRead,
    status_code=201,
    dependencies=[Depends(require_role("admin", "cashier", "reception"))],
)
def add_payment(
    payment_data: schemas.PaymentCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> models.Payment:
    appointment = (
        db.query(models.Appointment)
        .filter(models.Appointment.id == payment_data.appointment_id)
        .first()
    )
    if appointment is None:
        raise HTTPException(status_code=404, detail=f"Qabul #{payment_data.appointment_id} topilmadi")
    if appointment.status == "cancelled":
        raise HTTPException(status_code=409, detail="Bekor qilingan qabul uchun to'lov qabul qilib bo'lmaydi")

    if payment_data.amount > appointment.debt:
        raise HTTPException(
            status_code=409,
            detail=f"To'lov summasi qarzdan katta (qarz: {appointment.debt:,} UZS)",
        )

    new_payment = models.Payment(
        patient_id=appointment.patient_id,
        appointment_id=appointment.id,
        amount=payment_data.amount,
        note=payment_data.note,
    )
    db.add(new_payment)
    db.commit()
    db.refresh(new_payment)
    log_action(
        db, user, "payment.add", "Payment", new_payment.id,
        f"amount={new_payment.amount}, appointment_id={appointment.id}, patient_id={appointment.patient_id}",
    )
    return new_payment


@router.get("/list", response_model=List[schemas.PaymentListItem])
def list_payments(db: Session = Depends(get_db)) -> List[schemas.PaymentListItem]:
    # joinedload(patient): _to_list_item har bir to'lov uchun
    # payment.patient.fullname'ga murojaat qiladi — eager load yo'q bo'lsa
    # bu klassik N+1 (har bir to'lov = qo'shimcha so'rov).
    payments = (
        db.query(models.Payment)
        .options(joinedload(models.Payment.patient))
        .order_by(models.Payment.id.desc())
        .all()
    )
    refunded_ids = _refunded_payment_ids(db, [p.id for p in payments])
    return [_to_list_item(p, refunded_ids) for p in payments]


@router.get("/patient/{patient_id}", response_model=List[schemas.PaymentListItem])
def list_payments_for_patient(
    patient_id: int = Path(..., ge=1, le=2147483647),
    db: Session = Depends(get_db)
) -> List[schemas.PaymentListItem]:
    payments = (
        db.query(models.Payment)
        .filter(models.Payment.patient_id == patient_id)
        .order_by(models.Payment.id.desc())
        .all()
    )
    refunded_ids = _refunded_payment_ids(db, [p.id for p in payments])
    return [_to_list_item(p, refunded_ids) for p in payments]


@router.get("/appointment/{appointment_id}", response_model=List[schemas.PaymentListItem])
def list_payments_for_appointment(
    appointment_id: int = Path(..., ge=1, le=2147483647),
    db: Session = Depends(get_db)
) -> List[schemas.PaymentListItem]:
    payments = (
        db.query(models.Payment)
        .filter(models.Payment.appointment_id == appointment_id)
        .order_by(models.Payment.id.desc())
        .all()
    )
    refunded_ids = _refunded_payment_ids(db, [p.id for p in payments])
    return [_to_list_item(p, refunded_ids) for p in payments]


@router.get(
    "/export/csv",
    dependencies=[Depends(require_role("admin", "cashier", "reception"))],
)
def export_payments_csv(db: Session = Depends(get_db)) -> StreamingResponse:
    """6-band (UX): to'lovlar ro'yxatini Excel/CSV formatida yuklab olish.
    Og'ir kutubxona (openpyxl) o'rniga Python standart `csv` moduli
    ishlatilgan — Excel CSV faylni to'g'ridan-to'g'ri ocha oladi."""
    payments = (
        db.query(models.Payment)
        .options(joinedload(models.Payment.patient))
        .order_by(models.Payment.id.desc())
        .all()
    )
    refunded_ids = _refunded_payment_ids(db, [p.id for p in payments])

    buffer = io.StringIO()
    # \ufeff — UTF-8 BOM, Excel'da kirill/lotin harflar to'g'ri chiqishi uchun.
    buffer.write("\ufeff")
    writer = csv.writer(buffer)
    writer.writerow(["ID", "Sana", "Bemor", "Qabul ID", "Summa (UZS)", "Turi", "Izoh"])
    for p in payments:
        writer.writerow([
            p.id,
            p.created_at.strftime("%Y-%m-%d %H:%M"),
            p.patient.fullname if p.patient else "Noma'lum",
            p.appointment_id,
            p.amount,
            "Qaytarim" if p.is_refund else ("Qaytarilgan" if p.id in refunded_ids else "To'lov"),
            p.note or "",
        ])
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=tolovlar.csv"},
    )


# ── Dynamic path parameter routes ───────────────────────────────────
@router.patch(
    "/{payment_id}/refund",
    response_model=schemas.PaymentRead,
    dependencies=[Depends(require_role("admin", "cashier"))],
)
def refund_payment(
    payment_id: int = Path(..., ge=1, le=2147483647),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> models.Payment:
    payment = db.query(models.Payment).filter(models.Payment.id == payment_id).first()
    if payment is None:
        raise HTTPException(status_code=404, detail="To'lov topilmadi")
    if payment.is_refund:
        raise HTTPException(status_code=409, detail="Bu yozuv allaqachon qaytarim")

    # FK bo'yicha tekshirish — eski versiyada bu `note` matni bo'yicha
    # solishtirilardi, ya'ni kassir yozgan erkin matn tasodifan mos kelib
    # qolsa, tekshiruv noto'g'ri natija berishi mumkin edi.
    already_refunded = (
        db.query(models.Payment)
        .filter(models.Payment.refund_of_payment_id == payment.id)
        .first()
    )
    if already_refunded:
        raise HTTPException(status_code=409, detail="Bu to'lov allaqachon qaytarilgan")

    refund = models.Payment(
        patient_id=payment.patient_id,
        appointment_id=payment.appointment_id,
        amount=payment.amount,
        note=f"Qaytarim: to'lov #{payment.id}",
        is_refund=True,
        refund_of_payment_id=payment.id,
    )
    db.add(refund)
    db.flush()  # appointment.debt pastda to'g'ri hisoblanishi uchun

    # 🩹 Bug fix: agar shu to'lov "tugadi" deb belgilangan qabulga tegishli
    # bo'lsa va qaytarim natijasida qarz paydo bo'lsa — qabul "completed"
    # holatida qoladi, lekin bu app bo'ylab kafolatlangan invariantni
    # buzadi ("to'lanmagan qabul 'tugadi' deb belgilanmaydi", ko'ring:
    # modules/appointments.py). Shu sabab avtomatik ravishda uni
    # "in_progress"ga qaytaramiz — kassir/qabulxona buni ko'rib, qarzni
    # yopgach, qayta "tugadi" deb belgilay oladi.
    appointment = payment.appointment
    if appointment is not None:
        db.refresh(appointment)
        if appointment.status == "completed" and appointment.debt > 0:
            appointment.status = "in_progress"

    db.commit()
    db.refresh(refund)
    log_action(
        db, user, "payment.refund", "Payment", refund.id,
        f"original_payment_id={payment.id}, amount={refund.amount}",
    )
    return refund


@router.get("/{payment_id}", response_model=schemas.PaymentRead)
def get_payment(
    payment_id: int = Path(..., ge=1, le=2147483647),
    db: Session = Depends(get_db)
) -> models.Payment:
    payment = db.query(models.Payment).filter(models.Payment.id == payment_id).first()
    if payment is None:
        raise HTTPException(status_code=404, detail="To'lov topilmadi")
    return payment


def register_module() -> Dict[str, object]:
    return {
        "module_name": "Payments",
        "version": "2.1.0",
        "router": router,
    }