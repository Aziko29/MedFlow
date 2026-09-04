# tests/test_auth_module_update_user.py
"""
PROMPT 3 — PUT /api/auth/users/{id} (update_user) bug tuzatish testi.

Bug: modules/auth_module.py:update_user funksiyasida
`target.fullname = payload.fullname` qatori yo'q edi — admin xodim
ismini o'zgartirsa ham DB'da eski fullname saqlanib qolar edi (xato
chiqmasdan, jim yo'qolish). Ushbu test shu regressiyani ushlab turadi.

Boshqa update_* funksiyalar (update_patient, update_doctor,
update_admin_profile, update_position, update_department,
update_gov_integration_settings, update_clinic_settings,
update_system_settings) ham qo'lda tekshirildi — ularning barchasi
generic `for field, value in ....model_dump().items(): setattr(...)`
patternidan foydalanadi yoki qo'lda yozilgan ro'yxat tegishli schema
bilan to'liq mos keladi, shuning uchun ularda shu turdagi "tushib
qolgan maydon" xatosi topilmadi.
"""
import pytest

import models


class TestUpdateUserFullname:
    def test_fullname_is_persisted(self, client, db, make_user, login_as):
        admin = make_user("admin")
        target = make_user("cashier", username="update_target_1")
        login_as(admin)

        resp = client.put(
            f"/api/auth/users/{target.id}",
            json={
                "fullname": "Yangilangan F.I.O.",
                "role": "cashier",
                "doctor_id": None,
            },
        )

        assert resp.status_code == 200
        assert resp.json()["fullname"] == "Yangilangan F.I.O."

        db.refresh(target)
        assert target.fullname == "Yangilangan F.I.O."

    def test_role_and_doctor_id_still_update_alongside_fullname(
        self, client, db, make_user, make_doctor, login_as
    ):
        """Regressiyaga qarshi: fullname tuzatilganda role/doctor_id
        yangilanishi buzilmagani tekshiriladi."""
        admin = make_user("admin")
        target = make_user("cashier", username="update_target_2")
        doctor = make_doctor(fullname="Dr. Yangi")
        login_as(admin)

        resp = client.put(
            f"/api/auth/users/{target.id}",
            json={
                "fullname": "Ikkinchi Yangilanish",
                "role": "doctor",
                "doctor_id": doctor.id,
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["fullname"] == "Ikkinchi Yangilanish"
        assert body["role"] == "doctor"
        assert body["doctor_id"] == doctor.id

        db.refresh(target)
        assert target.fullname == "Ikkinchi Yangilanish"
        assert target.role == "doctor"
        assert target.doctor_id == doctor.id

    def test_cache_is_cleared_so_next_read_sees_new_fullname(
        self, client, db, make_user, login_as
    ):
        """clear_user_cache chaqirilgani uchun, yangilangan fullname
        keshdan emas, DB'dan darhol ko'rinishi kerak."""
        admin = make_user("admin")
        target = make_user("reception", username="update_target_3")
        login_as(admin)

        client.put(
            f"/api/auth/users/{target.id}",
            json={
                "fullname": "Uchinchi F.I.O.",
                "role": "reception",
                "doctor_id": None,
            },
        )

        resp = client.get("/api/auth/users")
        assert resp.status_code == 200
        updated = next(u for u in resp.json() if u["id"] == target.id)
        assert updated["fullname"] == "Uchinchi F.I.O."


class TestOtherUpdateFunctionsNoMissingFields:
    """Prompt 3, 2-band: update_patient/update_doctor kabi funksiyalarda
    shunga o'xshash 'schema maydoni bor, lekin modelga yozilmagan'
    xatosi yo'qligini tasdiqlaydi (regressiyaga qarshi)."""

    def test_update_patient_persists_all_fields(self, client, db, make_user, login_as):
        admin = make_user("admin")
        login_as(admin)

        create_resp = client.post(
            "/api/patients/add",
            json={"fullname": "Boshlang'ich Ism", "phone": "+998907001122"},
        )
        assert create_resp.status_code == 201
        patient_id = create_resp.json()["id"]

        update_resp = client.put(
            f"/api/patients/{patient_id}",
            json={"fullname": "Yangilangan Bemor", "phone": "+998907001133"},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["fullname"] == "Yangilangan Bemor"
        assert update_resp.json()["phone"] == "+998907001133"

        patient = db.query(models.Patient).get(patient_id)
        assert patient.fullname == "Yangilangan Bemor"
        assert patient.phone == "+998907001133"

    def test_update_doctor_persists_all_fields(self, client, db, make_user, make_doctor, login_as):
        admin = make_user("admin")
        doctor = make_doctor(fullname="Eski Ism", specialty="Terapevt")
        login_as(admin)

        update_resp = client.put(
            f"/api/doctors/{doctor.id}",
            json={
                "fullname": "Yangi Ism",
                "specialty": "Kardiolog",
                "room": "204",
                "consultation_price": 150000,
            },
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["fullname"] == "Yangi Ism"
        assert update_resp.json()["specialty"] == "Kardiolog"

        db.refresh(doctor)
        assert doctor.fullname == "Yangi Ism"
        assert doctor.specialty == "Kardiolog"
        assert doctor.room == "204"
        assert doctor.consultation_price == 150000
