"""
Appointment booking and status-tracking API.

Fixes from the audit:
  - #3 price list: booking copies doctor.consultation_price onto
    Appointment.price at creation time, so the amount owed is always known
    up front instead of the cashier guessing a number.
  - #5 state machine: status changes go through models.APPOINTMENT_TRANSITIONS
    instead of a free <select> that could jump any-state-to-any-state. You
    also can't mark an appointment "completed" while it's still unpaid.
  - #7 double booking: booking the same doctor within queue_interval_minutes
    of an existing active appointment is rejected with 409 (Prompt 13 —
    previously only an EXACT scheduled_time match was checked).
  - #8 missing "bekor qilish" (cancel) action — added as its own endpoint
    with a required-in-spirit reason, rather than overloading /status.
  - #9 race condition: two simultaneous book/reschedule requests for the
    same doctor could both pass the "is it free?" check before either
    committed, double-booking the doctor. `_lock_doctor_for_booking`
    (Prompt 14) now serializes that check+write per doctor_id — a real
    `SELECT ... FOR UPDATE` on PostgreSQL, an application-level forced
    write lock on SQLite (see its docstring for why).
"""
from datetime import datetime as dt
from datetime import timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import or_, update
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
    JOIN so'rovga birlashtiradi (Prompt 11).

    `payments` — bir-ko'pga (1:N) bog'lanish, shuning uchun bitta
    appointment bir nechta to'lovga ega bo'lsa, JOIN natijasida o'sha
    appointment qatori bir necha marta qaytishi mumkin edi. Bu yerda
    ATAYLAB legacy `db.query()` (Query) API ishlatiladi (loyihaning
    qolgan barcha joyi bilan bir xil) — u 2.0-uslubdagi
    `session.execute(select(...))`dan farqli o'laroq, joined
    eager-load'dagi dublikat ota-qatorlarni natija Python obyektlariga
    aylantirilganda IDENTITY MAP orqali AVTOMATIK unique() qiladi,
    shuning uchun `.all()` natijasi allaqachon dublikatsiz keladi
    (tests/test_appointments_n_plus_one.py — buni aniq tekshiradi)."""
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


# ── Navbat oralig'i (queue_interval_minutes) tekshiruvi (Prompt 13) ──
def _get_queue_interval_minutes(db: Session) -> int:
    """AdminProfileSettings (bitta-qator, Sozlamalar moduli) jadvalidan
    navbat oralig'ini o'qiydi. Bu yerda get-or-create SHART emas —
    faqat o'qish kifoya: agar sozlamalar qatori hali umuman
    yaratilmagan bo'lsa (masalan yangi o'rnatilgan tizim, admin hali
    /settings/clinic sahifasiga kirmagan), ustunning DB darajasidagi
    default qiymati (15) qo'llaniladi — booking oqimi buning uchun
    settings qatorini majburan yaratib qo'ymasligi kerak."""
    settings = db.query(models.AdminProfileSettings).first()
    if settings is None or settings.queue_interval_minutes is None:
        return models.AdminProfileSettings.queue_interval_minutes.default.arg
    return settings.queue_interval_minutes


def _find_conflicting_appointment(
    db: Session,
    doctor_id: int,
    candidate_time: dt,
    interval_minutes: int,
    exclude_appointment_id: int = None,
):
    """Shu shifokorning FAOL (bekor qilinmagan/kelmadi belgilanmagan)
    qabullari orasidan `candidate_time`ga `interval_minutes`dan KAM
    farqda turgan birinchisini qaytaradi (yo'q bo'lsa None).

    Farq QAT'IY interval'dan kichik bo'lganda ziddiyat hisoblanadi —
    aynan `interval_minutes` yoki undan ko'p farq bo'lsa, bu allaqachon
    bo'sh oraliq (masalan interval=15 bo'lsa, 15 daqiqalik farq band
    emas, 14 daqiqalik farq band)."""
    interval = timedelta(minutes=interval_minutes)
    query = db.query(models.Appointment).filter(
        models.Appointment.doctor_id == doctor_id,
        models.Appointment.status.in_(ACTIVE_STATUSES + ("completed",)),
        models.Appointment.scheduled_time > candidate_time - interval,
        models.Appointment.scheduled_time < candidate_time + interval,
    )
    if exclude_appointment_id is not None:
        query = query.filter(models.Appointment.id != exclude_appointment_id)
    return query.order_by(models.Appointment.scheduled_time.asc()).first()


def _find_next_free_slot(
    db: Session,
    doctor_id: int,
    desired_time: dt,
    interval_minutes: int,
) -> dt:
    """`desired_time`dan boshlab, shu shifokor uchun `interval_minutes`
    talabiga to'g'ri keladigan eng yaqin BO'SH vaqtni topadi.

    Naqsh: ziddiyatga uchraganda, kandidat vaqt ziddiyat tug'dirgan
    qabuldan roppa-rosa `interval_minutes` keyiniga suriladi, so'ng
    qayta tekshiriladi — chunki yangi kandidat o'z navbatida KEYINGI
    qabul bilan ham ziddiyatli bo'lishi mumkin (masalan qabullar zich
    joylashgan kun). Xavfsizlik uchun sikl cheklangan (amalda bir necha
    o'nlab qabul zanjiridan uzoqroq davom etmaydi)."""
    candidate = desired_time
    for _ in range(1000):
        conflict = _find_conflicting_appointment(db, doctor_id, candidate, interval_minutes)
        if conflict is None:
            return candidate
        candidate = conflict.scheduled_time + timedelta(minutes=interval_minutes)
    return candidate


# ── Race condition himoyasi (Prompt 14) ─────────────────────────────
def _lock_doctor_for_booking(db: Session, doctor_id: int) -> None:
    """`book_appointment`/`reschedule_appointment`dagi "tekshir, keyin
    yoz" (check-then-write) mantig'i, agar ikkita so'rov BIR VAQTDA
    kelsa, klassik race condition'ga ochiq: ikkalasi ham
    `_find_conflicting_appointment`ni bir-biridan oldin (hali hech biri
    commit qilmagan paytda) chaqiradi, ikkalasi ham "bo'sh" javobini
    oladi, va ikkalasi ham INSERT qiladi — natijada bitta shifokor bir
    xil vaqtga IKKI marta band bo'lib qoladi.

    Bu funksiya shu tekshiruvdan OLDIN chaqirilib, `doctor_id` bo'yicha
    kritik bo'limni serializatsiya qiladi — bir vaqtning o'zida faqat
    BITTA tranzaksiya shu shifokor uchun tekshirish+yozishni bajara
    oladi, qolganlari (bir xil doctor_id) tranzaksiya
    commit/rollback bo'lgunicha (yoki busy_timeout tugagunicha) kutadi.

    PostgreSQL: haqiqiy `SELECT ... FOR UPDATE` — Doctor qatorini
    tranzaksiya oxirigacha qulflaydi (application-level lock DB
    submexanizmi orqali).

    SQLite: `FOR UPDATE` tushunchasi yo'q (bitta fayl, MVCC yo'q).
    Shuning uchun bu yerda ATAYLAB no-op UPDATE chiqariladi (qiymat
    o'zgarmaydi, faqat yozish niyati bildiriladi) — bu database.py'dagi
    WAL rejimidagi YAGONA yozuvchi qulfini DARHOL, hali konflikt
    SELECT'idan OLDIN egallaydi. Ikkinchi parallel so'rov xuddi shu
    UPDATE'ga urilib, birinchisi commit qilgunicha bloklanadi
    (PRAGMA busy_timeout=5000 — database.py); faqat SHUNDAN KEYIN o'z
    konflikt tekshiruvini bajaradi va ALLAQACHON committed bo'lgan
    yozuvni to'g'ri ko'radi.

    Ikkala holatda ham qulf sessiya bilan bog'liq — `get_db()`dagi
    `db.close()` (muvaffaqiyat ham, HTTPException ham) commit
    qilinmagan tranzaksiyani rollback qiladi, shu bilan qulf har doim
    bo'shatiladi."""
    if db.bind.dialect.name == "sqlite":
        db.execute(
            update(models.Doctor)
            .where(models.Doctor.id == doctor_id)
            .values(id=models.Doctor.id)
        )
    else:
        db.query(models.Doctor.id).filter(models.Doctor.id == doctor_id).with_for_update().first()


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

    # 🔒 Race condition himoyasi (Prompt 14) — konflikt tekshiruvidan
    # OLDIN, shu shifokor uchun bron qilish qulfini olamiz (pastga qarang:
    # _lock_doctor_for_booking). Shundan keyingina "bo'shmi?" tekshiruvi
    # ishonchli bo'ladi — parallel ikkinchi so'rov shu qatorda kutadi.
    _lock_doctor_for_booking(db, appointment_data.doctor_id)

    # 🚫 Double booking (Prompt 13): faqat AYNAN bir xil vaqtni emas,
    # shifokorning navbat oralig'i (queue_interval_minutes, Sozlamalar
    # modulidan) ichida yotgan HAR QANDAY faol qabulni ham band deb
    # hisoblaydi — masalan interval=15 daqiqa bo'lsa, 09:00'dagi
    # qabulga 09:10'da yozilish endi ham rad etiladi (avval faqat
    # AYNAN 09:00 tekshirilardi, 09:10 "erkin" hisoblanib, amalda
    # shifokorni ustma-ust band qilib qo'yardi).
    queue_interval_minutes = _get_queue_interval_minutes(db)
    collision = _find_conflicting_appointment(
        db, appointment_data.doctor_id, appointment_data.scheduled_time, queue_interval_minutes
    )
    if collision:
        next_free = _find_next_free_slot(
            db, appointment_data.doctor_id, appointment_data.scheduled_time, queue_interval_minutes
        )
        raise HTTPException(
            status_code=409,
            detail=f"Bu vaqt band, keyingi bo'sh vaqt: {next_free.strftime('%H:%M')}",
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


# Prompt 24: sort_by allowlist — sort_by matni to'g'ridan-to'g'ri
# ustunga aylantirilmaydi, faqat shu ro'yxatdagi kalitlar qabul qilinadi.
_APPOINTMENT_SORT_COLUMNS = {
    "id": models.Appointment.id,
    "scheduled_time": models.Appointment.scheduled_time,
    "price": models.Appointment.price,
    "status": models.Appointment.status,
}


@router.get(
    "/list",
    response_model=schemas.AppointmentPage,
    dependencies=[Depends(require_role("admin", "reception", "cashier", "doctor", "assistant_admin"))],
)
def list_appointments(
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, description="Bemor yoki shifokor F.I.O bo'yicha qidirish"),
    sort_by: str = Query("scheduled_time", description="id | scheduled_time | price | status"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> schemas.AppointmentPage:
    """Qabullar ro'yxati — sahifalangan, saralangan va qidiruvli
    (Prompt 24). ``search`` bemor YOKI shifokor F.I.O bo'yicha ILIKE
    orqali izlaydi.

    🐛 BUG FIX (verify bosqichida topildi): ``_detail_query()``
    ``joinedload(models.Appointment.payments)`` ishlatadi — bu bir-KO'Pga
    (1:N) bog'lanish, ya'ni asosiy SQL'da har bir to'lov uchun QO'SHIMCHA
    qator paydo bo'ladi (LEFT JOIN natijasida "yassilangan" natija).
    ``.all()`` chaqirilganda SQLAlchemy'ning legacy Query API'si bu
    dublikatlarni identity-map orqali AVTOMATIK yig'ib beradi (fayl
    boshidagi ``_detail_query`` docstring'ida aytilganidek) — LEKIN bu
    faqat ``.all()`` uchun to'g'ri. ``.count()`` va ``.offset()/.limit()``
    esa xom SQL darajasida, o'sha "yassilangan" (join qilingan) qatorlar
    ustida ishlaydi:
      - ``.count()`` — 2 ta to'lovi bor bitta qabulni 2 marta sanaydi,
        shuning uchun ``total`` (demakki ``total_pages``) haqiqatdan
        KATTA chiqadi.
      - ``.limit(page_size)`` — LIMIT xom qatorlarga qo'llanadi, qabullarga
        emas: agar sahifadagi qabullarning ba'zilari bir nechta to'lovga
        ega bo'lsa, bitta sahifada kutilganidan KAM (yoki hatto boshqa
        sahifada takrorlangan) qabul qaytishi mumkin — aynan shu narsani
        tekshirish so'ralgan edi.

    Yechim: ikki bosqichli so'rov. 1-bosqich — FAQAT ID'larni (joinedload
    payments'siz, demak dublikatsiz) sahifalab olamiz, shu yerda
    ``count()``/``offset()``/``limit()`` mutlaqo to'g'ri ishlaydi.
    2-bosqich — o'sha sahifadagi ID'lar uchun to'liq (joinedload'langan)
    yozuvlarni olamiz — bu yerda endi LIMIT yo'q, shuning uchun payments
    ko'payishi hech qanday muammo tug'dirmaydi."""
    id_query = db.query(models.Appointment.id)
    if search:
        like = f"%{search}%"
        id_query = (
            id_query.join(models.Patient, models.Appointment.patient_id == models.Patient.id)
            .join(models.Doctor, models.Appointment.doctor_id == models.Doctor.id)
            .filter(or_(models.Patient.fullname.ilike(like), models.Doctor.fullname.ilike(like)))
        )

    sort_column = _APPOINTMENT_SORT_COLUMNS.get(sort_by, models.Appointment.scheduled_time)
    sort_column = sort_column.asc() if sort_order == "asc" else sort_column.desc()

    total = id_query.count()

    # ``models.Appointment.id.desc()`` — barqaror ikkinchi tartib mezoni:
    # ``sort_column`` bo'yicha bir xil qiymatli qatorlar (masalan bir xil
    # ``status``) sahifalar orasida tasodifiy tartibda chiqib/tushib
    # qolmasligi uchun (aks holda ayni bitta qabul ikkita ketma-ket
    # sahifada ham chiqishi yoki umuman chiqmasligi mumkin edi).
    page_ids = [
        row[0]
        for row in (
            id_query.order_by(sort_column, models.Appointment.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
    ]

    appointments_by_id = {
        a.id: a
        for a in _detail_query(db).filter(models.Appointment.id.in_(page_ids)).all()
    }
    # ``page_ids`` tartibini saqlab qolamiz — ``IN (...)`` natijasi SQL
    # darajasida tartiblanmagan bo'lishi mumkin.
    appointments = [appointments_by_id[i] for i in page_ids if i in appointments_by_id]

    return schemas.AppointmentPage(
        items=[_to_detail(a) for a in appointments],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max((total + page_size - 1) // page_size, 1),
    )


def list_appointments_all(db: Session) -> List[schemas.AppointmentDetail]:
    """To'liq (sahifalanmagan) qabullar ro'yxati — server-render HTML
    sahifalari (main.py :: appointments_page) uchun. API endpoint EMAS,
    oddiy Python funksiyasi sifatida chaqiriladi."""
    appointments = _detail_query(db).order_by(models.Appointment.scheduled_time.desc()).all()
    return [_to_detail(a) for a in appointments]


@router.get(
    "/patient/{patient_id}",
    response_model=List[schemas.AppointmentDetail],
    dependencies=[Depends(require_role("admin", "reception", "cashier", "doctor", "assistant_admin"))],
)
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


@router.get(
    "/doctor/{doctor_id}",
    response_model=List[schemas.AppointmentDetail],
    dependencies=[Depends(require_role("admin", "reception", "cashier", "doctor", "assistant_admin"))],
)
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

    # 🔒 Race condition himoyasi (Prompt 14) — bir xil sabab bilan
    # book_appointment'dagi kabi shu yerda ham kerak (ikki parallel
    # reschedule so'rovi bir xil shifokorni bir xil yangi vaqtga
    # ko'chirishga urinishi mumkin).
    _lock_doctor_for_booking(db, appointment.doctor_id)

    # 🐛 FIX: aniq vaqt (==) emas, book_appointment'dagi kabi
    # queue_interval_minutes oralig'idagi HAR QANDAY ziddiyat
    # tekshiriladi — aks holda bir shifokorni interval ichida (masalan
    # 09:00 va 09:10, interval=15) ikkiga ko'chirish mumkin bo'lardi.
    # `exclude_appointment_id` — ko'chirilayotgan qabulning o'zi
    # o'ziga qarshi ziddiyat sifatida hisoblanmasligi uchun.
    queue_interval_minutes = _get_queue_interval_minutes(db)
    collision = _find_conflicting_appointment(
        db,
        appointment.doctor_id,
        data.scheduled_time,
        queue_interval_minutes,
        exclude_appointment_id=appointment_id,
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
    # ⬅️ TUZATISH (#13-band): ilgari bu yerda `appointment.status not in
    # models.APPOINTMENT_TRANSITIONS` tekshirilardi — bugun bu TO'G'RI
    # natija beradi, chunki APPOINTMENT_TRANSITIONS'ning kalitlari
    # ("waiting", "delayed", "in_progress") aynan ACTIVE_STATUSES bilan
    # bir xil to'plam. Lekin bu — nozik/tasodifiy bog'liqlik: fayl
    # boshida aynan shu maqsad ("bekor qilib bo'ladigan holatlarmi?")
    # uchun mo'ljallangan ACTIVE_STATUSES tuple bor edi, undan foydalanish
    # kerak edi. APPOINTMENT_TRANSITIONS state-machine'ning "qaysi
    # holatdan qaysi holatga o'tish mumkin" ma'lumotini bildiradi — uning
    # kalitlari tasodifan "faol" holatlar bilan mos tushib qolgan, xolos.
    # Agar kelajakda shu dict o'zgartirilsa (masalan yangi status
    # qo'shilsa yoki "completed"dan biror o'tish qo'shilsa), bu tekshiruv
    # sezilmasdan buzilishi mumkin edi. Endi to'g'ridan-to'g'ri
    # ACTIVE_STATUSES'ga tayanadi — bir xil niyat bir xil manbadan
    # o'qiladi (491-qatordagi reschedule tekshiruvi bilan bir xil naqsh).
    if appointment.status not in ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail=f"'{appointment.status}' holatidagi qabulni bekor qilib bo'lmaydi")
    appointment.status = "cancelled"
    appointment.cancel_reason = cancel_data.reason
    db.commit()
    db.refresh(appointment)
    log_action(db, user, "appointment.cancel", "Appointment", appointment.id, f"reason={cancel_data.reason}")
    return _to_detail(appointment)


@router.get(
    "/{appointment_id}",
    response_model=schemas.AppointmentDetail,
    dependencies=[Depends(require_role("admin", "reception", "cashier", "doctor", "assistant_admin"))],
)
def get_appointment(
    appointment_id: int = Path(..., ge=1, le=2147483647),
    db: Session = Depends(get_db)
) -> schemas.AppointmentDetail:
    return _to_detail(_get_appointment_or_404(db, appointment_id))


def register_module() -> Dict[str, object]:
    return {
        "module_name": "Appointments",
        "version": "3.2.0",
        "router": router,
    }