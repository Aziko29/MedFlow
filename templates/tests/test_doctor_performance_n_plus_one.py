# tests/test_doctor_performance_n_plus_one.py
"""
PROMPT 12 — N+1 query: Shifokorlar samaradorligi hisobotini optimallashtirish.

`modules/reports.py :: get_doctor_performance` avval har bir FAOL
shifokor uchun alohida `db.query(models.Appointment).filter(doctor_id=...)`
so'rovi yuborardi — N ta faol shifokor bo'lsa, 1 (shifokorlar ro'yxati)
+ N (har biri uchun appointment so'rovi) = N+1 so'rov.

Bu testlar:
  1. Natija to'g'ri hisob-kitob qilinishini (avvalgidek): total/
     completed/cancelled/delayed/no_show/revenue har bir shifokor uchun
     to'g'ri, davrda qabuli yo'q faol shifokor ham 0 sonlar bilan
     ro'yxatda ekanini, nofaol shifokor umuman chiqmasligini,
  2. So'rovlar soni shifokorlar soniga (N) CHIZIQLI BOG'LIQ EMASLIGINI
     (N+1 yo'qligini) — 2 ta va 6 ta shifokor uchun deyarli bir xil
     so'rov soni ketishini,
  3. Bitta chaqiruv uchun so'rovlar soni juda kichik (bitta asosiy
     GROUP BY so'rovga yaqin) ekanini
tekshiradi.
"""
import datetime

from sqlalchemy import event

import models
from database import engine
from modules.reports import get_doctor_performance


class _QueryCounter:
    """`before_cursor_execute` hodisasi orqali bajarilgan SQL
    so'rovlar sonini sanaydi (engine darajasida, global)."""

    def __init__(self):
        self.count = 0

    def __enter__(self):
        self.count = 0
        event.listen(engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc):
        event.remove(engine, "before_cursor_execute", self._on_execute)

    def _on_execute(self, conn, cursor, statement, parameters, context, executemany):
        self.count += 1


PERIOD_START = datetime.datetime(2026, 1, 1, 0, 0, 0)
PERIOD_END = datetime.datetime(2026, 1, 31, 23, 59, 59)


def _make_doctor_with_appointments(db, make_doctor, make_patient, *, statuses, is_active=True):
    """`statuses` — shu shifokor uchun yaratiladigan appointmentlarning
    status ro'yxati (masalan ["completed", "completed", "cancelled"])."""
    doctor = make_doctor()
    doctor.is_active = is_active
    db.commit()

    for status in statuses:
        patient = make_patient()
        db.add(
            models.Appointment(
                patient_id=patient.id,
                doctor_id=doctor.id,
                scheduled_time=PERIOD_START + datetime.timedelta(days=1),
                status=status,
                price=50_000,
            )
        )
    db.commit()
    return doctor


class TestDoctorPerformanceCorrectness:
    def test_counts_and_revenue_correct_per_doctor(self, db, make_doctor, make_patient):
        doctor_a = _make_doctor_with_appointments(
            db, make_doctor, make_patient,
            statuses=["completed", "completed", "cancelled", "delayed", "no_show"],
        )
        doctor_b = _make_doctor_with_appointments(
            db, make_doctor, make_patient,
            statuses=["completed"],
        )

        rows = get_doctor_performance(db, PERIOD_START, PERIOD_END)
        by_id = {row.doctor_id: row for row in rows}

        row_a = by_id[doctor_a.id]
        assert row_a.total == 5
        assert row_a.completed == 2
        assert row_a.cancelled == 1
        assert row_a.delayed == 1
        assert row_a.no_show == 1
        assert row_a.revenue == 2 * 50_000

        row_b = by_id[doctor_b.id]
        assert row_b.total == 1
        assert row_b.completed == 1
        assert row_b.revenue == 50_000

    def test_active_doctor_with_no_appointments_in_period_shows_zeros(
        self, db, make_doctor
    ):
        doctor = make_doctor()
        doctor.is_active = True
        db.commit()

        rows = get_doctor_performance(db, PERIOD_START, PERIOD_END)
        by_id = {row.doctor_id: row for row in rows}

        assert doctor.id in by_id
        row = by_id[doctor.id]
        assert row.total == 0
        assert row.completed == 0
        assert row.cancelled == 0
        assert row.delayed == 0
        assert row.no_show == 0
        assert row.revenue == 0

    def test_inactive_doctor_excluded(self, db, make_doctor, make_patient):
        doctor = _make_doctor_with_appointments(
            db, make_doctor, make_patient, statuses=["completed"], is_active=False
        )
        rows = get_doctor_performance(db, PERIOD_START, PERIOD_END)
        assert doctor.id not in {row.doctor_id for row in rows}

    def test_appointments_outside_period_not_counted(self, db, make_doctor, make_patient):
        doctor = make_doctor()
        doctor.is_active = True
        db.commit()
        patient = make_patient()
        db.add(
            models.Appointment(
                patient_id=patient.id,
                doctor_id=doctor.id,
                scheduled_time=PERIOD_START - datetime.timedelta(days=10),
                status="completed",
                price=50_000,
            )
        )
        db.commit()

        rows = get_doctor_performance(db, PERIOD_START, PERIOD_END)
        by_id = {row.doctor_id: row for row in rows}
        assert by_id[doctor.id].total == 0

    def test_sorted_by_total_descending(self, db, make_doctor, make_patient):
        low = _make_doctor_with_appointments(
            db, make_doctor, make_patient, statuses=["completed"]
        )
        high = _make_doctor_with_appointments(
            db, make_doctor, make_patient,
            statuses=["completed", "completed", "cancelled"],
        )

        rows = get_doctor_performance(db, PERIOD_START, PERIOD_END)
        totals = [row.total for row in rows]
        assert totals == sorted(totals, reverse=True)
        assert rows[0].doctor_id == high.id
        assert rows[-1].doctor_id in (low.id, rows[-1].doctor_id)  # sanity


class TestDoctorPerformanceQueryCount:
    """Asosiy talab: so'rovlar soni FAOL SHIFOKORLAR soniga (N) chiziqli
    bog'liq bo'lmasligi kerak (N+1 yo'qolgan)."""

    def test_query_count_constant_regardless_of_doctor_count(
        self, db, make_doctor, make_patient
    ):
        for _ in range(2):
            _make_doctor_with_appointments(
                db, make_doctor, make_patient, statuses=["completed", "cancelled"]
            )
        with _QueryCounter() as counter:
            get_doctor_performance(db, PERIOD_START, PERIOD_END)
        small_n_queries = counter.count

        # Bazani tozalab, ko'proq shifokor bilan qaytadan.
        models.Base.metadata.drop_all(bind=engine)
        models.Base.metadata.create_all(bind=engine)

        for _ in range(6):
            _make_doctor_with_appointments(
                db, make_doctor, make_patient, statuses=["completed", "cancelled"]
            )
        with _QueryCounter() as counter:
            get_doctor_performance(db, PERIOD_START, PERIOD_END)
        large_n_queries = counter.count

        assert small_n_queries == large_n_queries, (
            f"So'rovlar soni shifokorlar soniga bog'liq bo'lib qoldi "
            f"(N+1 hali ham mavjud): 2 ta shifokor -> {small_n_queries} "
            f"so'rov, 6 ta shifokor -> {large_n_queries} so'rov."
        )

    def test_single_query_used(self, db, make_doctor, make_patient):
        """get_doctor_performance bitta chaqiruvda AYNAN 1 ta SQL
        so'rov yuborishi kerak (aggregat GROUP BY so'rov)."""
        for _ in range(4):
            _make_doctor_with_appointments(
                db, make_doctor, make_patient,
                statuses=["completed", "completed", "no_show"],
            )

        with _QueryCounter() as counter:
            get_doctor_performance(db, PERIOD_START, PERIOD_END)

        assert counter.count == 1, (
            f"get_doctor_performance {counter.count} ta SQL so'rov yubordi, "
            "kutilgan: 1 (N+1 hali bor bo'lishi mumkin)."
        )
