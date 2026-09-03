# tests/test_delete_patient_soft_delete.py
"""
PROMPT 6 — Bemorni o'chirish: Soft Delete va Relationship sozlamasi.

Bu fayl Prompt 5'dagi `tests/test_delete_patient_fk_guard.py`ni almashtiradi:
o'sha yerdagi "bog'liq yozuvlar bo'lsa 409 qaytarish" xatti-harakati endi
ORTIQCHA — chunki DELETE /api/patients/{id} endi hech qachon Patient
qatorini (yoki uning appointments/payments/lab_results tarixini)
jismonan o'chirmaydi, faqat `is_deleted=True` va `deleted_at`ni
belgilaydi. Shuning uchun bog'liq yozuvlari bor bemorni ham, yo'q
bemorni ham bir xil — muvaffaqiyatli (204, soft-delete) — o'chirish
mumkin, va ikkalasida ham DB'dagi hech qanday qator yo'qolmaydi.

Testlar tekshiradi:
  1. DELETE dan keyin Patient qatori DB'da hamon MAVJUD (jismonan
     o'chirilmagan), lekin `is_deleted=True` va `deleted_at` to'ldirilgan.
  2. Bog'liq Appointment/Payment/LabResult yozuvlari ham butunlay
     saqlanib qoladi (relationship endi hard-delete cascade bermaydi).
  3. Soft-delete qilingan bemor endi GET/PUT orqali "topilmadi" (404)
     deb ko'rinadi (_get_patient_or_404 is_deleted=False filtrlaydi).
  4. Allaqachon o'chirilgan bemorni yana o'chirishga urinish 404 beradi
     (haqiqiy qatorni ikkinchi marta "o'chirib" yubormaydi).
"""
import datetime

import models


class TestDeletePatientIsSoftDelete:
    def test_delete_marks_is_deleted_true_and_row_still_exists(
        self, client, db, make_patient, make_user, login_as
    ):
        admin = make_user("admin")
        login_as(admin)

        patient = make_patient()
        patient_id = patient.id
        assert patient.is_deleted is False
        assert patient.deleted_at is None

        resp = client.delete(f"/api/patients/{patient_id}")
        assert resp.status_code == 204

        db.expire_all()  # TestClient alohida DB-seansida commit qilgan
        row = db.query(models.Patient).filter(models.Patient.id == patient_id).first()

        # ✅ Qator DB'dan O'CHIRILMAGAN — hard delete emas.
        assert row is not None
        # ✅ ...lekin soft-delete bilan belgilangan.
        assert row.is_deleted is True
        assert row.deleted_at is not None

    def test_deleted_at_reflects_time_of_deletion(
        self, client, db, make_patient, make_user, login_as
    ):
        admin = make_user("admin")
        login_as(admin)
        patient = make_patient()

        before = datetime.datetime.utcnow()
        resp = client.delete(f"/api/patients/{patient.id}")
        after = datetime.datetime.utcnow()
        assert resp.status_code == 204

        db.expire_all()
        row = db.query(models.Patient).filter(models.Patient.id == patient.id).first()
        assert before - datetime.timedelta(seconds=5) <= row.deleted_at <= after + datetime.timedelta(seconds=5)


class TestDeletePatientPreservesRelatedRecords:
    """Prompt 5'da tekshirilgan narsa endi 409 emas — lekin bog'liq
    yozuvlar HAMON yo'qolmasligi (endi soft-delete tufayli) tasdiqlanadi."""

    def test_appointments_survive_patient_soft_delete(
        self, client, db, make_patient, make_doctor, make_user, login_as
    ):
        admin = make_user("admin")
        login_as(admin)

        patient = make_patient()
        doctor = make_doctor()
        appointment = models.Appointment(
            patient_id=patient.id,
            doctor_id=doctor.id,
            scheduled_time=datetime.datetime.utcnow(),
            status="waiting",
            price=50_000,
        )
        db.add(appointment)
        db.commit()
        appointment_id = appointment.id

        resp = client.delete(f"/api/patients/{patient.id}")
        assert resp.status_code == 204

        db.expire_all()
        assert db.query(models.Appointment).filter(
            models.Appointment.id == appointment_id
        ).first() is not None

    def test_payments_and_appointments_survive_patient_soft_delete(
        self, client, db, make_appointment_with_payment, make_user, login_as
    ):
        admin = make_user("admin")
        login_as(admin)

        payment = make_appointment_with_payment()
        patient_id = payment.patient_id
        payment_id = payment.id
        appointment_id = payment.appointment_id

        resp = client.delete(f"/api/patients/{patient_id}")
        assert resp.status_code == 204

        db.expire_all()
        assert db.query(models.Payment).filter(models.Payment.id == payment_id).first() is not None
        assert db.query(models.Appointment).filter(
            models.Appointment.id == appointment_id
        ).first() is not None

    def test_lab_results_survive_patient_soft_delete(
        self, client, db, make_patient, make_doctor, make_user, login_as
    ):
        admin = make_user("admin")
        login_as(admin)

        patient = make_patient()
        doctor = make_doctor()
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

        resp = client.delete(f"/api/patients/{patient.id}")
        assert resp.status_code == 204

        db.expire_all()
        assert db.query(models.LabResult).filter(
            models.LabResult.id == lab_result_id
        ).first() is not None


class TestSoftDeletedPatientIsHiddenFromNormalAccess:
    def test_soft_deleted_patient_returns_404_on_get(
        self, client, db, make_patient, make_user, login_as
    ):
        admin = make_user("admin")
        login_as(admin)
        patient = make_patient()

        assert client.delete(f"/api/patients/{patient.id}").status_code == 204

        resp = client.get(f"/api/patients/{patient.id}")
        assert resp.status_code == 404

    def test_deleting_already_deleted_patient_returns_404(
        self, client, db, make_patient, make_user, login_as
    ):
        """Ikkinchi marta o'chirishga urinish — qator allaqachon
        is_deleted=True bo'lgani uchun _get_patient_or_404 uni endi
        "topilmadi" deb hisoblaydi (haqiqiy qatorni yana o'zgartirmaydi)."""
        admin = make_user("admin")
        login_as(admin)
        patient = make_patient()

        assert client.delete(f"/api/patients/{patient.id}").status_code == 204
        second_resp = client.delete(f"/api/patients/{patient.id}")

        assert second_resp.status_code == 404
