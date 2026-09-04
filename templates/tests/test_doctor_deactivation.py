# tests/test_doctor_deactivation.py
"""
PROMPT 8 — Shifokorni faolsizlantirish (is_active) mantiqi.

Doctor.is_active ustuni allaqachon mavjud edi (models.py), lekin uni
ishlatadigan aniq mexanizm yo'q edi:
  - DELETE /api/doctors/{id} bog'liq tarixi (Appointment/LabResult/
    TreatmentHistory/User.doctor_id) bor shifokorni 409 bilan bloklardi
    (Prompt 7), lekin admin uchun "shu o'rniga nima qilish kerak" degan
    aniq muqobil YO'Q edi.
  - GET /api/doctors/list HAR DOIM barcha (faol + faolsiz) shifokorlarni
    qaytarardi — templates/base.html'dagi "Yangi Qabul Band Qilish"
    select'i buni `is_active` bo'yicha JINJA darajasida filtrlardi, lekin
    xuddi shu ma'lumotdan foydalanadigan boshqa API iste'molchilari
    (masalan mobil ilova) uchun serverdagi filtr yo'q edi.

Ushbu testlar:
  1. Doctor modelida is_active ustuni mavjudligini va standart holatda
     True bo'lishini tekshiradi.
  2. POST /api/doctors/{id}/deactivate shifokorni hard-delete qilmasdan
     is_active=False qilishini, DB qatori (va bog'liq tarixi) hamon
     mavjudligini tekshiradi.
  3. Bog'liq tarixi bor shifokorni DELETE qilishga urinilganda 409 xabari
     endi aniq muqobil (deactivate endpoint) ni ko'rsatishini tekshiradi.
  4. GET /api/doctors/list?active_only=true faolsizlantirilgan shifokorni
     chiqarib tashlashini, standart (parametrsiz) chaqiruv esa hamon
     BARCHASINI (admin boshqaruvi uchun) qaytarishini tekshiradi.
  5. Faolsizlantirilgan shifokor "Yangi Qabul Band Qilish" sahifasidagi
     (GET /appointments) shifokor tanlov ro'yxatida ko'rinmasligini
     tekshiradi — ya'ni yangi qabullar uchun endi tanlab bo'lmaydi.
  6. Faqat admin deactivate/activate qila olishini (RBAC) va
     POST /api/doctors/{id}/activate orqali qaytarib faollashtirish
     mumkinligini tekshiradi.
"""
import datetime

import models


class TestDoctorIsActiveColumn:
    def test_new_doctor_defaults_to_active(self, db, make_doctor):
        doctor = make_doctor()
        assert doctor.is_active is True

    def test_is_active_persists_as_false(self, db, make_doctor):
        doctor = make_doctor()
        doctor.is_active = False
        db.commit()
        db.expire_all()

        row = db.query(models.Doctor).filter(models.Doctor.id == doctor.id).first()
        assert row.is_active is False


class TestDeactivateEndpoint:
    def test_admin_can_deactivate_doctor(self, client, db, make_doctor, make_user, login_as):
        admin = make_user("admin")
        login_as(admin)
        doctor = make_doctor()
        assert doctor.is_active is True

        resp = client.post(f"/api/doctors/{doctor.id}/deactivate")

        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

        db.expire_all()
        row = db.query(models.Doctor).filter(models.Doctor.id == doctor.id).first()
        assert row is not None  # hard delete emas — qator saqlanib qoladi
        assert row.is_active is False

    def test_deactivate_does_not_delete_related_history(
        self, client, db, make_doctor, make_patient, make_user, login_as
    ):
        """Deactivation'ning butun maqsadi — tibbiy tarixni saqlab qolish."""
        admin = make_user("admin")
        login_as(admin)
        doctor = make_doctor()
        patient = make_patient()
        appointment = models.Appointment(
            patient_id=patient.id,
            doctor_id=doctor.id,
            scheduled_time=datetime.datetime.utcnow(),
            status="completed",
            price=50_000,
        )
        db.add(appointment)
        db.commit()
        appointment_id = appointment.id

        resp = client.post(f"/api/doctors/{doctor.id}/deactivate")
        assert resp.status_code == 200

        db.expire_all()
        assert db.query(models.Doctor).filter(models.Doctor.id == doctor.id).first() is not None
        assert (
            db.query(models.Appointment)
            .filter(models.Appointment.id == appointment_id)
            .first()
            is not None
        )

    def test_non_admin_cannot_deactivate_doctor(
        self, client, db, make_doctor, make_user, login_as
    ):
        reception = make_user("reception")
        login_as(reception)
        doctor = make_doctor()

        resp = client.post(f"/api/doctors/{doctor.id}/deactivate")

        assert resp.status_code == 403
        db.expire_all()
        row = db.query(models.Doctor).filter(models.Doctor.id == doctor.id).first()
        assert row.is_active is True

    def test_deactivate_unknown_doctor_returns_404(self, client, make_user, login_as):
        admin = make_user("admin")
        login_as(admin)

        resp = client.post("/api/doctors/999999/deactivate")

        assert resp.status_code == 404


class TestActivateEndpoint:
    def test_admin_can_reactivate_doctor(self, client, db, make_doctor, make_user, login_as):
        admin = make_user("admin")
        login_as(admin)
        doctor = make_doctor()
        client.post(f"/api/doctors/{doctor.id}/deactivate")

        resp = client.post(f"/api/doctors/{doctor.id}/activate")

        assert resp.status_code == 200
        assert resp.json()["is_active"] is True

        db.expire_all()
        row = db.query(models.Doctor).filter(models.Doctor.id == doctor.id).first()
        assert row.is_active is True


class TestDeleteDoctorSuggestsDeactivation:
    def test_409_message_points_to_deactivate_endpoint(
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
        detail = resp.json()["detail"]
        # Eski xabar (turlar/soni) hamon mavjud bo'lishi kerak (regressiya
        # emas — Prompt 7 testlari shu substring'larni tekshiradi).
        assert "1 ta tahlil natijasi" in detail
        # Endi muqobil sifatida deactivate endpoint aniq ko'rsatilgan.
        assert f"/api/doctors/{doctor.id}/deactivate" in detail

        # Va bu taklif qilingan muqobil haqiqatan ham ishlaydi:
        deactivate_resp = client.post(f"/api/doctors/{doctor.id}/deactivate")
        assert deactivate_resp.status_code == 200
        db.expire_all()
        assert db.query(models.Doctor).filter(models.Doctor.id == doctor.id).first() is not None


class TestListDoctorsActiveOnlyFilter:
    def test_default_list_includes_inactive_doctors(
        self, client, db, make_doctor, make_user, login_as
    ):
        """Admin boshqaruv ro'yxati (masalan /doctors sahifasi) hamon
        faolsizlantirilgan shifokorlarni ko'rishi/qayta faollashtirishi
        kerak — shuning uchun standart (filtrsiz) chaqiruv o'zgarmaydi."""
        user = make_user("reception")
        login_as(user)
        active_doctor = make_doctor(fullname="Faol Shifokor")
        inactive_doctor = make_doctor(fullname="Faolsiz Shifokor")
        inactive_doctor.is_active = False
        db.commit()

        resp = client.get("/api/doctors/list")

        assert resp.status_code == 200
        ids = {d["id"] for d in resp.json()}
        assert active_doctor.id in ids
        assert inactive_doctor.id in ids

    def test_active_only_excludes_deactivated_doctor(
        self, client, db, make_doctor, make_user, login_as
    ):
        user = make_user("reception")
        login_as(user)
        active_doctor = make_doctor(fullname="Faol Shifokor")
        inactive_doctor = make_doctor(fullname="Faolsiz Shifokor")
        inactive_doctor.is_active = False
        db.commit()

        resp = client.get("/api/doctors/list?active_only=true")

        assert resp.status_code == 200
        ids = {d["id"] for d in resp.json()}
        assert active_doctor.id in ids
        assert inactive_doctor.id not in ids


class TestDeactivatedDoctorHiddenFromNewAppointmentSelection:
    """PROMPT 8, band 4 — asosiy talab: faolsizlantirilgan shifokor yangi
    qabullar uchun ro'yxatda ko'rinmasligi kerak."""

    def test_deactivated_doctor_missing_from_active_only_list(
        self, client, db, make_doctor, make_user, login_as
    ):
        reception = make_user("reception")
        login_as(reception)
        doctor = make_doctor(fullname="Dr. Aliyev")

        # Boshida — faol, ro'yxatda ko'rinadi.
        resp_before = client.get("/api/doctors/list?active_only=true")
        assert doctor.id in {d["id"] for d in resp_before.json()}

        admin = make_user("admin")
        login_as(admin)
        deactivate_resp = client.post(f"/api/doctors/{doctor.id}/deactivate")
        assert deactivate_resp.status_code == 200

        resp_after = client.get("/api/doctors/list?active_only=true")
        assert resp_after.status_code == 200
        assert doctor.id not in {d["id"] for d in resp_after.json()}

    def test_deactivated_doctor_missing_from_new_appointment_page(
        self, client, db, make_doctor, make_user, login_as
    ):
        """Server tomonidan render qilingan /appointments sahifasidagi
        "Yangi Qabul Band Qilish" select'i — bu haqiqiy foydalanuvchi
        yangi qabul band qilishda ko'radigan ro'yxat."""
        admin = make_user("admin")
        login_as(admin)

        active_doctor = make_doctor(fullname="Dr. Faol Ismoilov")
        inactive_doctor = make_doctor(fullname="Dr. Faolsiz Karimov")
        client.post(f"/api/doctors/{inactive_doctor.id}/deactivate")

        resp = client.get("/appointments")

        assert resp.status_code == 200
        html = resp.text
        assert f'value="{active_doctor.id}"' in html
        assert f'value="{inactive_doctor.id}"' not in html
        assert "Dr. Faol Ismoilov" in html
        assert "Dr. Faolsiz Karimov" not in html

    def test_reactivated_doctor_reappears_in_active_only_list(
        self, client, db, make_doctor, make_user, login_as
    ):
        admin = make_user("admin")
        login_as(admin)
        doctor = make_doctor()
        client.post(f"/api/doctors/{doctor.id}/deactivate")
        assert doctor.id not in {
            d["id"] for d in client.get("/api/doctors/list?active_only=true").json()
        }

        client.post(f"/api/doctors/{doctor.id}/activate")

        resp = client.get("/api/doctors/list?active_only=true")
        assert doctor.id in {d["id"] for d in resp.json()}
