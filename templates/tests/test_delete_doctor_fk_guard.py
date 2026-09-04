# tests/test_delete_doctor_fk_guard.py
"""
PROMPT 7 — Shifokorni o'chirishda FK blokirovkasi.

Bug: modules/doctors.py::delete_doctor faqat Appointment yozuvlarini
tekshirar edi. Agar shifokorga faqat LabResult (yoki TreatmentHistory,
yoki unga bog'langan xodim hisobi — User.doctor_id) bog'liq bo'lsa-yu,
Appointment bo'lmasa, funksiya to'g'ridan-to'g'ri `db.delete(doctor)`ga
o'tib, DB darajasida (PRAGMA foreign_keys=ON) IntegrityError bilan
tushunarsiz 500 xatolik berardi.

Ushbu testlar: LabResult (va boshqa doctor_id FK'ga ega yozuvlar) bor
shifokorni o'chirishga urinilganda 409 Conflict aniq xabar bilan
qaytishini, hech narsa o'chirilmasligini, va bog'liqligi yo'q
shifokorning hamon muvaffaqiyatli o'chirilishini tekshiradi.
"""
import datetime

import models


class TestDeleteDoctorBlockedByLabResults:
    def test_returns_409_when_doctor_has_lab_result(
        self, client, db, make_doctor, make_patient, make_user, login_as
    ):
        admin = make_user("admin")
        login_as(admin)

        doctor = make_doctor()
        patient = make_patient()
        lab_result = models.LabResult(
            patient_id=patient.id,
            doctor_id=doctor.id,
            test_name="Qon tahlili",
            result_data="Natija: normal",
            status="pending",
        )
        db.add(lab_result)
        db.commit()

        resp = client.delete(f"/api/doctors/{doctor.id}")

        assert resp.status_code == 409
        assert "1 ta tahlil natijasi" in resp.json()["detail"]

    def test_doctor_and_lab_result_still_exist_after_blocked_delete(
        self, client, db, make_doctor, make_patient, make_user, login_as
    ):
        admin = make_user("admin")
        login_as(admin)

        doctor = make_doctor()
        patient = make_patient()
        lab_result = models.LabResult(
            patient_id=patient.id,
            doctor_id=doctor.id,
            test_name="Qon tahlili",
            result_data="Natija: normal",
            status="pending",
        )
        db.add(lab_result)
        db.commit()
        lab_result_id = lab_result.id

        resp = client.delete(f"/api/doctors/{doctor.id}")
        assert resp.status_code == 409

        db.expire_all()
        assert db.query(models.Doctor).filter(models.Doctor.id == doctor.id).first() is not None
        assert db.query(models.LabResult).filter(
            models.LabResult.id == lab_result_id
        ).first() is not None

    def test_returns_409_with_multiple_lab_results_count(
        self, client, db, make_doctor, make_patient, make_user, login_as
    ):
        admin = make_user("admin")
        login_as(admin)

        doctor = make_doctor()
        patient = make_patient()
        for i in range(4):
            db.add(
                models.LabResult(
                    patient_id=patient.id,
                    doctor_id=doctor.id,
                    test_name=f"Tahlil {i}",
                    result_data="Natija: normal",
                    status="pending",
                )
            )
        db.commit()

        resp = client.delete(f"/api/doctors/{doctor.id}")

        assert resp.status_code == 409
        assert "4 ta tahlil natijasi" in resp.json()["detail"]


class TestDeleteDoctorBlockedByAppointments:
    def test_returns_409_when_doctor_has_appointments(
        self, client, db, make_doctor, make_patient, make_user, login_as
    ):
        admin = make_user("admin")
        login_as(admin)

        doctor = make_doctor()
        patient = make_patient()
        appointment = models.Appointment(
            patient_id=patient.id,
            doctor_id=doctor.id,
            scheduled_time=datetime.datetime.utcnow(),
            status="waiting",
            price=50_000,
        )
        db.add(appointment)
        db.commit()

        resp = client.delete(f"/api/doctors/{doctor.id}")

        assert resp.status_code == 409
        assert "1 ta qabul yozuvi" in resp.json()["detail"]


class TestDeleteDoctorBlockedByLinkedUserAccount:
    def test_returns_409_when_doctor_has_linked_user_account(
        self, client, db, make_doctor, make_user, login_as
    ):
        """make_user(\"doctor\") avtomatik ravishda bir Doctor yozuvi bilan
        User.doctor_id orqali bog'lanadi (tests/conftest.py) — shu
        Doctor'ni o'chirishga urinish bloklanishi kerak, aks holda
        xodim hisobi \"osilib qoladi\" (yetim FK)."""
        admin = make_user("admin")
        login_as(admin)

        doctor_user = make_user("doctor")  # o'ziga tegishli Doctor yozuvini ham yaratadi

        resp = client.delete(f"/api/doctors/{doctor_user.doctor_id}")

        assert resp.status_code == 409
        assert "bog'langan xodim hisobi" in resp.json()["detail"]


class TestDeleteDoctorWithoutDependenciesStillWorks:
    def test_doctor_without_related_records_is_deleted(
        self, client, db, make_doctor, make_user, login_as
    ):
        """Regressiyaga qarshi: bog'liqligi yo'q shifokor hamon
        muvaffaqiyatli (204) o'chirilishi kerak."""
        admin = make_user("admin")
        login_as(admin)

        doctor = make_doctor()
        doctor_id = doctor.id

        resp = client.delete(f"/api/doctors/{doctor_id}")

        assert resp.status_code == 204
        db.expire_all()
        assert db.query(models.Doctor).filter(models.Doctor.id == doctor_id).first() is None
