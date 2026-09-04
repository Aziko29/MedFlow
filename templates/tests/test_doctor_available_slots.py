# tests/test_doctor_available_slots.py
"""
PROMPT 14 — GET /api/doctors/{doctor_id}/available-slots.

Bu endpoint shifokorning berilgan kundagi bo'sh vaqt oynalarini
qaytaradi: ish vaqti (`Doctor.working_hours`) ichida, hozirgi paytdan
keyin, va `queue_interval_minutes` qadam bilan — har bir qadam
`_find_conflicting_appointment` (book_appointment ishlatadigan AYNAN
o'sha funksiya) bo'yicha tekshiriladi, shuning uchun bu ro'yxat
booking mantig'i bilan har doim mos keladi.
"""
import datetime

import models


FUTURE_DAY = (datetime.datetime.now() + datetime.timedelta(days=7)).date()


def _set_queue_interval(db, minutes: int) -> None:
    settings = db.query(models.AdminProfileSettings).first()
    if settings is None:
        settings = models.AdminProfileSettings(positions=[], departments=[])
        db.add(settings)
    settings.queue_interval_minutes = minutes
    db.commit()


class TestAvailableSlotsBasics:
    def test_returns_slots_within_working_hours_at_interval_step(
        self, client, login_as, make_user, make_doctor, db
    ):
        _set_queue_interval(db, 30)
        doctor = make_doctor()
        doctor.working_hours = "09:00 - 11:00"
        db.commit()

        reception = login_as(make_user("reception"))
        resp = reception.get(
            f"/api/doctors/{doctor.id}/available-slots",
            params={"date": FUTURE_DAY.isoformat()},
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["doctor_id"] == doctor.id
        assert payload["interval_minutes"] == 30

        returned_times = [t[11:16] for t in payload["slots"]]
        assert returned_times == ["09:00", "09:30", "10:00", "10:30"]

    def test_booked_slot_is_excluded(
        self, client, login_as, make_user, make_patient, make_doctor, db
    ):
        _set_queue_interval(db, 30)
        doctor = make_doctor()
        doctor.working_hours = "09:00 - 11:00"
        db.commit()
        patient = make_patient()

        reception = login_as(make_user("reception"))
        booked_time = datetime.datetime.combine(FUTURE_DAY, datetime.time(9, 30))
        book_resp = reception.post(
            "/api/appointments/book",
            json={
                "patient_id": patient.id,
                "doctor_id": doctor.id,
                "scheduled_time": booked_time.isoformat(),
            },
        )
        assert book_resp.status_code == 201

        resp = reception.get(
            f"/api/doctors/{doctor.id}/available-slots",
            params={"date": FUTURE_DAY.isoformat()},
        )
        returned_times = [t[11:16] for t in resp.json()["slots"]]
        assert "09:30" not in returned_times
        assert returned_times == ["09:00", "10:00", "10:30"]

    def test_unknown_doctor_returns_404(self, client, login_as, make_user):
        reception = login_as(make_user("reception"))
        resp = reception.get(
            "/api/doctors/999999/available-slots", params={"date": FUTURE_DAY.isoformat()}
        )
        assert resp.status_code == 404

    def test_requires_login(self, client):
        resp = client.get(
            "/api/doctors/1/available-slots", params={"date": FUTURE_DAY.isoformat()}
        )
        assert resp.status_code == 401
