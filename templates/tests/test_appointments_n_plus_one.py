# tests/test_appointments_n_plus_one.py
"""
PROMPT 11 — N+1 query: Appointments va Payments ro'yxatini optimallashtirish.

Ikkita joyda N+1 bor edi:
  1. modules/appointments.py :: list_appointments (`_detail_query`) —
     `_to_detail()` har bir appointment uchun `.patient`/`.doctor`/
     `.payments`ga murojaat qiladi. Eager load bo'lmasa, ro'yxatdagi
     HAR BIR yozuv uchun qo'shimcha so'rovlar ketadi — jami so'rovlar
     soni appointment sonига (N) chiziqli bog'liq bo'lib qoladi.
  2. main.py :: payments_page — `raw_open` ro'yxatidagi har bir
     appointment uchun `.debt` (property) chaqiriladi, u esa
     `.payments`ga murojaat qiladi — xuddi shu muammo.

Bu testlar N ni oshirganda SQL so'rovlar soni O'ZGARMASLIGINI (N+1
yo'qligini) va appointment bir nechta to'lovga ega bo'lganda ham
javobda DUBLIKAT qatorlar hosil bo'lmasligini tekshiradi.
"""
import datetime

from sqlalchemy import event

import models
from database import engine


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


def _make_appointment_with_payments(db, make_patient, make_doctor, *, n_payments: int, status: str = "waiting"):
    patient = make_patient()
    doctor = make_doctor()
    appointment = models.Appointment(
        patient_id=patient.id,
        doctor_id=doctor.id,
        scheduled_time=datetime.datetime.utcnow(),
        status=status,
        price=100_000,
    )
    db.add(appointment)
    db.commit()
    db.refresh(appointment)

    for i in range(n_payments):
        payment = models.Payment(
            patient_id=patient.id,
            appointment_id=appointment.id,
            amount=10_000,
            status="completed",
        )
        db.add(payment)
    db.commit()
    return appointment


class TestListAppointmentsQueryCountDoesNotScaleWithN:
    """Asosiy talab (#3): so'rovlar soni appointment soniga (N)
    chiziqli bog'liq BO'LMASLIGI kerak — N+1 yo'qolgan bo'lsa, 3 ta
    va 8 ta appointment uchun bir xil (yoki juda yaqin) so'rov soni
    ketishi kerak."""

    def _count_queries_for_n_appointments(self, client, login_as, make_user, make_patient, make_doctor, db, n: int) -> int:
        user = make_user("reception")
        c = login_as(user)
        for _ in range(n):
            _make_appointment_with_payments(db, make_patient, make_doctor, n_payments=2)

        with _QueryCounter() as counter:
            resp = c.get("/api/appointments/list")
        assert resp.status_code == 200
        assert len(resp.json()) == n
        return counter.count

    def test_query_count_constant_regardless_of_appointment_count(
        self, client, login_as, make_user, make_patient, make_doctor, db
    ):
        small_n_queries = self._count_queries_for_n_appointments(
            client, login_as, make_user, make_patient, make_doctor, db, n=3
        )

        # Toza holat: bazani qayta tozalab, kattaroq N bilan qaytadan.
        models.Base.metadata.drop_all(bind=engine)
        models.Base.metadata.create_all(bind=engine)

        large_n_queries = self._count_queries_for_n_appointments(
            client, login_as, make_user, make_patient, make_doctor, db, n=8
        )

        # N+1 bo'lganida so'rovlar soni N bilan chiziqli o'sar edi
        # (masalan 3 ta appointment ~1+3*3=10 so'rov, 8 tasi ~1+3*8=25
        # so'rov qilardi). joinedload bilan ikkalasi ham BITTA (yagona
        # JOIN) so'rovga yaqin bo'lib, N o'sishi bilan DEYARLI
        # o'zgarmasligi kerak.
        assert small_n_queries == large_n_queries, (
            f"So'rovlar soni appointment soniga bog'liq bo'lib qoldi "
            f"(N+1 hali ham mavjud): N=3 -> {small_n_queries} so'rov, "
            f"N=8 -> {large_n_queries} so'rov."
        )

    def test_query_count_is_low_single_digit(
        self, client, login_as, make_user, make_patient, make_doctor, db
    ):
        """Qo'shimcha aniqlik: 6 ta appointment (har birida 2 tadan
        to'lov) uchun so'rovlar soni bir nechtagina (N+1 bo'lganda
        kutilgan ~19 so'rov emas, bir hovuch) bo'lishi kerak."""
        user = make_user("admin")
        c = login_as(user)
        for _ in range(6):
            _make_appointment_with_payments(db, make_patient, make_doctor, n_payments=2)

        with _QueryCounter() as counter:
            resp = c.get("/api/appointments/list")
        assert resp.status_code == 200
        assert len(resp.json()) == 6
        # Auth (get_current_user) + bitta asosiy JOIN so'rov uchun
        # keng, xavfsiz chegara — N+1 bo'lganda 19+ so'rov ketardi.
        assert counter.count < 10, (
            f"Kutilganidan ko'p so'rov: {counter.count} (N+1 hali bor bo'lishi mumkin)"
        )


class TestListAppointmentsNoDuplicateRows:
    """Talab #2: bitta appointment bir nechta to'lovga ega bo'lganda,
    JOIN natijasida shu appointment javobda TAKRORLANMASLIGI kerak."""

    def test_appointment_with_multiple_payments_appears_once(
        self, client, login_as, make_user, make_patient, make_doctor, db
    ):
        user = make_user("reception")
        c = login_as(user)
        appt = _make_appointment_with_payments(db, make_patient, make_doctor, n_payments=4)

        resp = c.get("/api/appointments/list")
        assert resp.status_code == 200
        body = resp.json()

        matching = [row for row in body if row["id"] == appt.id]
        assert len(matching) == 1, (
            f"Appointment #{appt.id} javobda {len(matching)} marta chiqdi — "
            "JOIN dublikat qator hosil qilgan (unique() yo'q)."
        )

    def test_paid_amount_correctly_summed_despite_join(
        self, client, login_as, make_user, make_patient, make_doctor, db
    ):
        """Dublikatsiz ekanini yana bir yo'ldan tasdiqlash: agar
        appointment qatori 4 marta takrorlansa, paid_amount ham 4
        martalik yig'indi (40_000 o'rniga noto'g'ri son) bo'lib
        qolmasligi kerak."""
        user = make_user("reception")
        c = login_as(user)
        appt = _make_appointment_with_payments(db, make_patient, make_doctor, n_payments=4)

        resp = c.get("/api/appointments/list")
        row = next(r for r in resp.json() if r["id"] == appt.id)
        assert row["paid_amount"] == 4 * 10_000


class TestPaymentsPageOpenAppointmentsQueryCount:
    """main.py :: payments_page — /payments sahifasidagi "ochiq
    appointmentlar" ro'yxati (`.debt` property orqali) ham eager
    load'siz N+1 hosil qilardi."""

    def test_payments_page_query_count_does_not_scale_with_open_appointments(
        self, client, login_as, make_user, make_patient, make_doctor, db
    ):
        user = make_user("admin")
        c = login_as(user)

        for _ in range(3):
            _make_appointment_with_payments(
                db, make_patient, make_doctor, n_payments=1, status="waiting"
            )
        with _QueryCounter() as counter:
            resp_small = c.get("/payments")
        assert resp_small.status_code == 200
        small_n_queries = counter.count

        for _ in range(6):
            _make_appointment_with_payments(
                db, make_patient, make_doctor, n_payments=1, status="waiting"
            )
        with _QueryCounter() as counter:
            resp_large = c.get("/payments")
        assert resp_large.status_code == 200
        large_n_queries = counter.count

        # 3 dan 9 taga o'sganda so'rovlar soni deyarli o'zgarmasligi
        # kerak (N+1 bo'lganda sezilarli o'sardi).
        assert large_n_queries - small_n_queries <= 2, (
            f"3 -> 9 ochiq appointmentga o'tganda so'rovlar soni "
            f"{small_n_queries} dan {large_n_queries} gacha o'sdi — "
            "N+1 hali ham mavjud bo'lishi mumkin."
        )
