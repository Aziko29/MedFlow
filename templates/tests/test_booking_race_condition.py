# tests/test_booking_race_condition.py
"""
PROMPT 14 — Double-booking: Race condition va DB lock himoyasi.

Bag (tuzatilgunga qadar): `book_appointment` "tekshir, keyin yoz"
(check-then-write) tartibida ishlagan — avval `_find_conflicting_appointment`
bilan bo'sh joy bor-yo'qligi tekshirilgan, so'ng INSERT qilingan. Ikkita
so'rov AYNAN BIR VAQTDA kelsa, ikkalasi ham hali hech biri commit
qilmagan paytda "bo'sh" javobini olishi va ikkalasi ham yozib
yuborishi mumkin edi — natijada bitta shifokor bir xil (yoki yaqin)
vaqtga ikki marta band bo'lib qolardi.

Tuzatish: `modules/appointments.py :: _lock_doctor_for_booking` endi
konflikt tekshiruvidan OLDIN chaqiriladi va shu `doctor_id` bo'yicha
tekshir+yoz bo'limini serializatsiya qiladi (PostgreSQL'da haqiqiy
`SELECT ... FOR UPDATE`, SQLite'da yozuvchi qulfini majburiy DARHOL
egallovchi no-op UPDATE — funksiya docstring'iga qarang).

Bu testlar HTTP/TestClient qatlamini emas, balki `book_appointment`
funksiyasining o'zini, HAR BIR "parallel so'rov" uchun ALOHIDA,
HAQIQIY DB seansi (SessionLocal()) bilan, bir nechta THREAD'dan
chaqiradi — bu HTTP darajasidagi test client'ning threadlar-orasi
xavfsizligiga bog'liq bo'lmagan holda, aynan DB darajasidagi
poyga holatini (race condition) va uning qulf bilan yopilishini
tekshiradi.
"""
import datetime
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException

import models
import schemas
from database import SessionLocal
from modules.appointments import book_appointment


FUTURE_DAY = datetime.datetime.now() + datetime.timedelta(days=10)
BASE_TIME = FUTURE_DAY.replace(hour=10, minute=0, second=0, microsecond=0)

PARALLEL_ATTEMPTS = 8


def _attempt_book(doctor_id: int, patient_id: int, scheduled_time, user):
    """Bitta "parallel so'rov"ni simulyatsiya qiladi: o'zining ALOHIDA
    DB seansi bilan (haqiqiy ilova so'rovidagi kabi — har bir HTTP
    so'rov `get_db()` orqali o'z seansini oladi) `book_appointment`ni
    to'g'ridan-to'g'ri chaqiradi."""
    session = SessionLocal()
    try:
        data = schemas.AppointmentCreate(
            patient_id=patient_id, doctor_id=doctor_id, scheduled_time=scheduled_time
        )
        try:
            result = book_appointment(data, db=session, user=user)
            return ("ok", result.id)
        except HTTPException as exc:
            return ("rejected", exc.status_code)
    finally:
        session.close()


class TestParallelBookingRaceCondition:
    def test_only_one_of_N_parallel_identical_bookings_succeeds(
        self, make_user, make_patient, make_doctor, db
    ):
        """N ta bir xil so'rov (bir xil shifokor, bir xil vaqt) AYNAN
        BIR VAQTDA yuborilsa — faqat BITTASI 201 bilan o'tishi, qolgan
        HAMMASI 409 (band) bilan rad etilishi kerak."""
        admin = make_user("admin")
        doctor = make_doctor()
        patients = [make_patient() for _ in range(PARALLEL_ATTEMPTS)]

        with ThreadPoolExecutor(max_workers=PARALLEL_ATTEMPTS) as pool:
            futures = [
                pool.submit(_attempt_book, doctor.id, patient.id, BASE_TIME, admin)
                for patient in patients
            ]
            results = [f.result() for f in futures]

        outcomes = [r[0] for r in results]
        assert outcomes.count("ok") == 1, f"Kutilgan: aynan 1 ta 'ok', olindi: {results}"
        assert outcomes.count("rejected") == PARALLEL_ATTEMPTS - 1
        assert all(status == 409 for kind, status in results if kind == "rejected")

        # DB darajasida ham faqat BITTA yozuv borligini tasdiqlaymiz —
        # bu endpoint'ning javobiga emas, haqiqatan ham nechta qator
        # yozilganiga tayangan qat'iy tekshiruv.
        appointments = (
            db.query(models.Appointment).filter(models.Appointment.doctor_id == doctor.id).all()
        )
        assert len(appointments) == 1
        assert appointments[0].scheduled_time == BASE_TIME

    def test_parallel_near_times_within_queue_interval_also_serialized(
        self, make_user, make_patient, make_doctor, db
    ):
        """AYNAN bir xil vaqt emas, balki queue_interval_minutes ichida
        joylashgan bir-biriga yaqin vaqtlarga parallel yuborilgan
        so'rovlar orasidan ham faqat bittasi o'tishi kerak — qulf
        interval-asosidagi tekshiruvni ham to'g'ri himoya qiladi."""
        settings = models.AdminProfileSettings(
            positions=[], departments=[], queue_interval_minutes=15
        )
        db.add(settings)
        db.commit()

        admin = make_user("admin")
        doctor = make_doctor()
        offsets = [0, 2, 4, 6, 8, 10]
        patients = [make_patient() for _ in offsets]
        times = [BASE_TIME + datetime.timedelta(minutes=m) for m in offsets]

        with ThreadPoolExecutor(max_workers=len(offsets)) as pool:
            futures = [
                pool.submit(_attempt_book, doctor.id, patient.id, sched_time, admin)
                for patient, sched_time in zip(patients, times)
            ]
            results = [f.result() for f in futures]

        outcomes = [r[0] for r in results]
        assert outcomes.count("ok") == 1, f"Kutilgan: aynan 1 ta 'ok', olindi: {results}"

        appointments = (
            db.query(models.Appointment).filter(models.Appointment.doctor_id == doctor.id).all()
        )
        assert len(appointments) == 1

    def test_parallel_bookings_for_different_doctors_are_unaffected(
        self, make_user, make_patient, make_doctor, db
    ):
        """Qulf HAR BIR shifokor uchun ALOHIDA (doctor_id bo'yicha) —
        turli shifokorlarga bir vaqtda kelgan so'rovlar bir-birini
        bloklamasligi, HAMMASI muvaffaqiyatli o'tishi kerak."""
        admin = make_user("admin")
        doctors = [make_doctor(fullname=f"Dr. {i}") for i in range(4)]
        patients = [make_patient() for _ in doctors]

        with ThreadPoolExecutor(max_workers=len(doctors)) as pool:
            futures = [
                pool.submit(_attempt_book, doctor.id, patient.id, BASE_TIME, admin)
                for doctor, patient in zip(doctors, patients)
            ]
            results = [f.result() for f in futures]

        assert [r[0] for r in results] == ["ok"] * len(doctors)
