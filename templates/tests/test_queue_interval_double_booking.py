# tests/test_queue_interval_double_booking.py
"""
PROMPT 13 — Double-booking: Vaqt oralig'i (queue_interval_minutes)
tekshiruvi.

Bag: `modules/appointments.py :: book_appointment` avval faqat AYNAN
bir xil `scheduled_time`ni tekshirardi (`==`). Amalda shifokorning
navbat oralig'i (masalan 15 daqiqa) ichida joylashgan, lekin AYNAN bir
xil bo'lmagan vaqtga (masalan 10 daqiqa farq bilan) yozilish ham
band bo'lishi kerak edi — avvalgi kod buni o'tkazib yuborardi.

Bu testlar:
  1. `AdminProfileSettings.queue_interval_minutes = 15` bo'lganda, mavjud
     qabuldan 10 daqiqa farqdagi yangi so'rov 409 bilan RAD ETILISHINI,
     va xabarda "Bu vaqt band, keyingi bo'sh vaqt: HH:MM" formati
     borligini,
  2. Xuddi shu sharoitda 20 daqiqa farqdagi so'rov MUVAFFAQIYATLI
     (201) o'tishini,
  3. Interval o'zgartirilsa (masalan 5 daqiqaga tushirilsa), avval rad
     etilgan 10 daqiqalik farq endi o'tishini,
  4. Aynan `interval_minutes` chegarasining o'zi (farq = interval,
     qat'iy kichik emas) band HISOBLANMASLIGINI ("chegara holati")
tekshiradi.
"""
import datetime

import models


FUTURE_DAY = datetime.datetime.now() + datetime.timedelta(days=7)
BASE_TIME = FUTURE_DAY.replace(hour=9, minute=0, second=0, microsecond=0)


def _set_queue_interval(db, minutes: int) -> None:
    settings = db.query(models.AdminProfileSettings).first()
    if settings is None:
        settings = models.AdminProfileSettings(positions=[], departments=[])
        db.add(settings)
    settings.queue_interval_minutes = minutes
    db.commit()


def _book(client, doctor_id, patient_id, scheduled_time):
    return client.post(
        "/api/appointments/book",
        json={
            "patient_id": patient_id,
            "doctor_id": doctor_id,
            "scheduled_time": scheduled_time.isoformat(),
        },
    )


class TestQueueIntervalRejectsCloseBooking:
    def test_10_minutes_apart_rejected_when_interval_is_15(
        self, client, login_as, make_user, make_patient, make_doctor, db
    ):
        _set_queue_interval(db, 15)
        reception = make_user("reception")
        c = login_as(reception)
        doctor = make_doctor()
        patient1 = make_patient()
        patient2 = make_patient()

        first = _book(c, doctor.id, patient1.id, BASE_TIME)
        assert first.status_code == 201

        conflicting_time = BASE_TIME + datetime.timedelta(minutes=10)
        second = _book(c, doctor.id, patient2.id, conflicting_time)

        assert second.status_code == 409
        detail = second.json()["detail"]
        assert "Bu vaqt band, keyingi bo'sh vaqt:" in detail
        # Format HH:MM tekshiruvi.
        suggested = detail.split(":")[-2].strip() + ":" + detail.split(":")[-1].strip()
        assert len(suggested) == 5 and suggested[2] == ":"

    def test_20_minutes_apart_accepted_when_interval_is_15(
        self, client, login_as, make_user, make_patient, make_doctor, db
    ):
        _set_queue_interval(db, 15)
        reception = make_user("reception")
        c = login_as(reception)
        doctor = make_doctor()
        patient1 = make_patient()
        patient2 = make_patient()

        first = _book(c, doctor.id, patient1.id, BASE_TIME)
        assert first.status_code == 201

        free_time = BASE_TIME + datetime.timedelta(minutes=20)
        second = _book(c, doctor.id, patient2.id, free_time)

        assert second.status_code == 201

    def test_10_minutes_apart_also_rejected_when_new_time_is_before_existing(
        self, client, login_as, make_user, make_patient, make_doctor, db
    ):
        """Farq ikkala yo'nalishda ham (oldin/keyin) tekshirilishi kerak."""
        _set_queue_interval(db, 15)
        reception = make_user("reception")
        c = login_as(reception)
        doctor = make_doctor()
        patient1 = make_patient()
        patient2 = make_patient()

        first = _book(c, doctor.id, patient1.id, BASE_TIME)
        assert first.status_code == 201

        earlier_conflicting_time = BASE_TIME - datetime.timedelta(minutes=10)
        second = _book(c, doctor.id, patient2.id, earlier_conflicting_time)
        assert second.status_code == 409


class TestQueueIntervalIsConfigurable:
    def test_10_minute_gap_accepted_when_interval_reduced_to_5(
        self, client, login_as, make_user, make_patient, make_doctor, db
    ):
        _set_queue_interval(db, 5)
        reception = make_user("reception")
        c = login_as(reception)
        doctor = make_doctor()
        patient1 = make_patient()
        patient2 = make_patient()

        first = _book(c, doctor.id, patient1.id, BASE_TIME)
        assert first.status_code == 201

        gap_time = BASE_TIME + datetime.timedelta(minutes=10)
        second = _book(c, doctor.id, patient2.id, gap_time)
        assert second.status_code == 201

    def test_default_interval_is_15_when_settings_row_absent(
        self, client, login_as, make_user, make_patient, make_doctor, db
    ):
        """AdminProfileSettings qatori hali yaratilmagan bo'lsa ham,
        standart 15 daqiqalik interval qo'llanishi kerak (Column
        default bilan mos)."""
        assert db.query(models.AdminProfileSettings).first() is None

        reception = make_user("reception")
        c = login_as(reception)
        doctor = make_doctor()
        patient1 = make_patient()
        patient2 = make_patient()

        first = _book(c, doctor.id, patient1.id, BASE_TIME)
        assert first.status_code == 201

        conflicting_time = BASE_TIME + datetime.timedelta(minutes=10)
        second = _book(c, doctor.id, patient2.id, conflicting_time)
        assert second.status_code == 409


class TestQueueIntervalBoundary:
    def test_exact_interval_gap_is_not_considered_a_conflict(
        self, client, login_as, make_user, make_patient, make_doctor, db
    ):
        """Farq AYNAN interval'ga teng bo'lsa (qat'iy kichik emas) —
        bu allaqachon bo'sh oraliq, band emas."""
        _set_queue_interval(db, 15)
        reception = make_user("reception")
        c = login_as(reception)
        doctor = make_doctor()
        patient1 = make_patient()
        patient2 = make_patient()

        first = _book(c, doctor.id, patient1.id, BASE_TIME)
        assert first.status_code == 201

        exactly_interval_later = BASE_TIME + datetime.timedelta(minutes=15)
        second = _book(c, doctor.id, patient2.id, exactly_interval_later)
        assert second.status_code == 201


class TestQueueIntervalDifferentDoctorsUnaffected:
    def test_close_time_for_different_doctor_is_allowed(
        self, client, login_as, make_user, make_patient, make_doctor, db
    ):
        _set_queue_interval(db, 15)
        reception = make_user("reception")
        c = login_as(reception)
        doctor_a = make_doctor(fullname="Dr. A")
        doctor_b = make_doctor(fullname="Dr. B")
        patient1 = make_patient()
        patient2 = make_patient()

        first = _book(c, doctor_a.id, patient1.id, BASE_TIME)
        assert first.status_code == 201

        conflicting_time_but_other_doctor = BASE_TIME + datetime.timedelta(minutes=5)
        second = _book(c, doctor_b.id, patient2.id, conflicting_time_but_other_doctor)
        assert second.status_code == 201


class TestQueueIntervalCancelledAppointmentsIgnored:
    def test_cancelled_appointment_does_not_block_nearby_slot(
        self, client, login_as, make_user, make_patient, make_doctor, db
    ):
        _set_queue_interval(db, 15)
        reception = make_user("reception")
        c = login_as(reception)
        doctor = make_doctor()
        patient1 = make_patient()
        patient2 = make_patient()

        first = _book(c, doctor.id, patient1.id, BASE_TIME)
        assert first.status_code == 201
        appt_id = first.json()["id"]

        cancel_resp = c.patch(
            f"/api/appointments/{appt_id}/cancel", json={"reason": "test"}
        )
        assert cancel_resp.status_code == 200

        nearby_time = BASE_TIME + datetime.timedelta(minutes=5)
        second = _book(c, doctor.id, patient2.id, nearby_time)
        assert second.status_code == 201
