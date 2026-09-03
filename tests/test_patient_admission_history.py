# tests/test_patient_admission_history.py
"""
PROMPT 10 — Bemor yotqizilishi: admit/discharge mantiqini yangilash.

Prompt 9 faqat `models.PatientAdmission` jadvalini qo'shgan edi
(tests/test_patient_admission_model.py), lekin modules/patients.py'dagi
admit_patient/discharge_patient HALI HAM eski Patient ustunlariga
to'g'ridan-to'g'ri yozar edi — jadvalning o'ziga hech qanday qator
ochilmasdi/yopilmasdi, shuning uchun API orqali chindan ham tarix
yig'ilishi tekshirilmagan edi.

Ushbu testlar:
  1. POST /admit haqiqatan ham yangi PatientAdmission qatorini ochishini,
  2. POST /discharge joriy OCHIQ qatorga discharged_at/discharged_reason
     yozishini (Patient ustunlarini emas, tarix qatorining o'zini),
  3. Bemor IKKI MARTA yotqizilib chiqarilganda — ikkala epizod ham
     (discharged_at/room_number/discharged_reason bilan birga) alohida
     saqlanib qolishini,
  4. GET /api/patients/{id} javobida shu to'liq tarix
     (`PatientDetail.admissions`) qaytishini
tasdiqlaydi.
"""
import models


class TestAdmitCreatesAdmissionRow:
    def test_admit_opens_new_admission_record(self, client, login_as, make_user, make_patient, db):
        reception = make_user("reception")
        patient = make_patient()
        c = login_as(reception)

        resp = c.post(f"/api/patients/{patient.id}/admit", json={"room_number": "101"})
        assert resp.status_code == 200

        db.expire_all()
        rows = (
            db.query(models.PatientAdmission)
            .filter(models.PatientAdmission.patient_id == patient.id)
            .all()
        )
        assert len(rows) == 1
        assert rows[0].room_number == "101"
        assert rows[0].admitted_at is not None
        assert rows[0].discharged_at is None


class TestDischargeClosesOpenAdmissionRow:
    def test_discharge_sets_discharged_at_and_reason_on_open_row(
        self, client, login_as, make_user, make_patient, db
    ):
        doctor = make_user("doctor")
        patient = make_patient()
        c = login_as(doctor)

        c.post(f"/api/patients/{patient.id}/admit", json={"room_number": "204"})
        resp = c.post(
            f"/api/patients/{patient.id}/discharge",
            json={"discharged_reason": "Sog'aydi"},
        )
        assert resp.status_code == 200

        db.expire_all()
        row = (
            db.query(models.PatientAdmission)
            .filter(models.PatientAdmission.patient_id == patient.id)
            .one()
        )
        assert row.discharged_at is not None
        assert row.discharged_reason == "Sog'aydi"


class TestTwoAdmitDischargeCyclesPreserveBothEpisodes:
    """Asosiy talab (#4): bemor 2 marta yotqizilib chiqarilganda ikkala
    epizod ham tarixda saqlanishi kerak — ikkinchi admit birinchi
    epizodning discharged_at/room_number'ini o'chirib yubormasligi
    kerak (aynan Prompt 9'dagi bag, endi API darajasida tuzatilgan)."""

    def test_both_episodes_survive_via_api(
        self, client, login_as, make_user, make_patient, db
    ):
        reception = make_user("reception")
        patient = make_patient()
        c = login_as(reception)

        # 1-epizod: 101-palata, yotqizilib chiqariladi.
        r1 = c.post(f"/api/patients/{patient.id}/admit", json={"room_number": "101"})
        assert r1.status_code == 200
        d1 = c.post(
            f"/api/patients/{patient.id}/discharge",
            json={"discharged_reason": "Sog'aydi"},
        )
        assert d1.status_code == 200

        # 2-epizod: 204-palata, yana yotqizilib chiqariladi.
        r2 = c.post(f"/api/patients/{patient.id}/admit", json={"room_number": "204"})
        assert r2.status_code == 200
        d2 = c.post(
            f"/api/patients/{patient.id}/discharge",
            json={"discharged_reason": "Boshqa klinikaga o'tkazildi"},
        )
        assert d2.status_code == 200

        # DB darajasida: ikkita ALOHIDA, ikkalasi ham yopiq qator.
        db.expire_all()
        rows = (
            db.query(models.PatientAdmission)
            .filter(models.PatientAdmission.patient_id == patient.id)
            .order_by(models.PatientAdmission.admitted_at.asc())
            .all()
        )
        assert len(rows) == 2

        first, second = rows
        assert first.room_number == "101"
        assert first.discharged_at is not None
        assert first.discharged_reason == "Sog'aydi"

        assert second.room_number == "204"
        assert second.discharged_at is not None
        assert second.discharged_reason == "Boshqa klinikaga o'tkazildi"

        # 1-epizod hali ham to'liq (2-admit uni bosib yozmagan).
        assert first.admitted_at is not None
        assert first.admitted_at != second.admitted_at

    def test_both_episodes_returned_in_patient_detail_endpoint(
        self, client, login_as, make_user, make_patient
    ):
        """Talab #3: bemor detali endpointi (GET /{patient_id})
        yotqizilishlar tarixini qaytarishi kerak."""
        reception = make_user("reception")
        patient = make_patient()
        c = login_as(reception)

        c.post(f"/api/patients/{patient.id}/admit", json={"room_number": "101"})
        c.post(
            f"/api/patients/{patient.id}/discharge",
            json={"discharged_reason": "Sog'aydi"},
        )
        c.post(f"/api/patients/{patient.id}/admit", json={"room_number": "204"})
        c.post(
            f"/api/patients/{patient.id}/discharge",
            json={"discharged_reason": "Boshqa klinikaga o'tkazildi"},
        )

        detail = c.get(f"/api/patients/{patient.id}")
        assert detail.status_code == 200
        admissions = detail.json()["admissions"]

        assert len(admissions) == 2
        rooms = {a["room_number"] for a in admissions}
        assert rooms == {"101", "204"}
        for a in admissions:
            assert a["discharged_at"] is not None
            assert a["discharged_reason"] in ("Sog'aydi", "Boshqa klinikaga o'tkazildi")

        # Eng yangi epizod birinchi (order_by admitted_at.desc()).
        assert admissions[0]["room_number"] == "204"
        assert admissions[1]["room_number"] == "101"

    def test_currently_admitted_episode_has_no_discharged_at(
        self, client, login_as, make_user, make_patient
    ):
        """1-epizod chiqarilgan, 2-epizod hali OCHIQ bo'lsa — tarixda
        ikkalasi ham bo'lishi, lekin faqat ikkinchisi discharged_at=None
        bo'lishi kerak."""
        doctor = make_user("doctor")
        patient = make_patient()
        c = login_as(doctor)

        c.post(f"/api/patients/{patient.id}/admit", json={"room_number": "101"})
        c.post(
            f"/api/patients/{patient.id}/discharge",
            json={"discharged_reason": "Sog'aydi"},
        )
        c.post(f"/api/patients/{patient.id}/admit", json={"room_number": "204"})

        detail = c.get(f"/api/patients/{patient.id}")
        admissions = detail.json()["admissions"]
        assert len(admissions) == 2

        current = next(a for a in admissions if a["room_number"] == "204")
        past = next(a for a in admissions if a["room_number"] == "101")
        assert current["discharged_at"] is None
        assert past["discharged_at"] is not None
