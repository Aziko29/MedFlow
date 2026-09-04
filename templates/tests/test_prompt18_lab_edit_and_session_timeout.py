# tests/test_prompt18_lab_edit_and_session_timeout.py
"""
PROMPT 18 — ikki mustaqil bug uchun testlar:

  A) modules/lab_results.py — edit_lab_result: admin endi Form orqali
     kelgan doctor_id yordamida boshqa (FAOL) shifokor nomidan
     tahrirlay oladi; oddiy doctor/lab_doctor hamon faqat o'ziga
     biriktirilganini, o'z doctor_id'i bilan tahrirlaydi (Prompt 5
     himoyasi buzilmagani tekshiriladi).

  B) auth.py — sessiya muddati endi SystemSettings.session_timeout_minutes
     bilan HAQIQATAN bog'langan: qiymat o'zgartirilganda, server qayta
     ishga tushirilmasdan/qayta login qilinmasdan, KEYINGI so'rovdanoq
     yangi muddat amal qiladi.
"""
import time

import auth
import models


# ==============================================
# A) LAB RESULT — admin boshqa shifokor nomidan tahrirlaydi
# ==============================================
class TestEditLabResultAdminReassign:
    def _make_result(self, db, patient, doctor, template_key="cbc"):
        import lab_templates

        payload = lab_templates.build_result_payload(template_key, {}, "")
        result = models.LabResult(
            patient_id=patient.id,
            doctor_id=doctor.id,
            test_name=payload["template_name"],
            result_data=__import__("json").dumps(payload, ensure_ascii=False),
            status="Tayyor",
        )
        db.add(result)
        db.commit()
        db.refresh(result)
        return result

    def test_admin_can_reassign_to_another_active_doctor(
        self, client, db, make_user, make_doctor, make_patient, login_as
    ):
        admin = make_user("admin")
        owner_doctor = make_doctor(fullname="Birinchi Shifokor")
        new_doctor = make_doctor(fullname="Ikkinchi Shifokor")
        patient = make_patient()
        result = self._make_result(db, patient, owner_doctor)
        login_as(admin)

        resp = client.post(
            f"/lab-results/edit/{result.id}",
            data={
                "doctor_id": str(new_doctor.id),
                "indicators_json": "{}",
                "note": "",
                "status_val": "Tayyor",
            },
        )

        assert resp.status_code in (200, 303)
        db.refresh(result)
        assert result.doctor_id == new_doctor.id

    def test_admin_reassign_to_inactive_doctor_is_rejected(
        self, client, db, make_user, make_doctor, make_patient, login_as
    ):
        admin = make_user("admin")
        owner_doctor = make_doctor(fullname="Faol Shifokor")
        inactive_doctor = make_doctor(fullname="Nofaol Shifokor")
        inactive_doctor.is_active = False
        db.commit()
        patient = make_patient()
        result = self._make_result(db, patient, owner_doctor)
        login_as(admin)

        resp = client.post(
            f"/lab-results/edit/{result.id}",
            data={
                "doctor_id": str(inactive_doctor.id),
                "indicators_json": "{}",
                "note": "",
                "status_val": "Tayyor",
            },
            follow_redirects=False,
        )

        # Xato bilan bemor ro'yxatiga (form_error bilan) qaytariladi,
        # shifokor esa O'ZGARMAGAN bo'lishi kerak.
        assert resp.status_code == 303
        assert "form_error" in resp.headers.get("location", "")
        db.refresh(result)
        assert result.doctor_id == owner_doctor.id

    def test_doctor_cannot_reassign_via_form_field(
        self, client, db, make_user, make_doctor, make_patient, login_as
    ):
        """Prompt 5 himoyasi buzilmagani: oddiy doctor o'z natijasini
        tahrirlaganda, Form'dagi doctor_id qiymati e'tiborga olinmaydi —
        natija hamon o'ziga (user.doctor_id) biriktirilgan holda qoladi."""
        owner = make_user("doctor")
        other_doctor = make_doctor(fullname="Boshqa Shifokor")
        patient = make_patient()
        result = self._make_result(db, patient, owner.doctor)
        login_as(owner)

        resp = client.post(
            f"/lab-results/edit/{result.id}",
            data={
                "doctor_id": str(other_doctor.id),  # buni o'zgartirishga urinadi
                "indicators_json": "{}",
                "note": "",
                "status_val": "Tayyor",
            },
        )

        assert resp.status_code in (200, 303)
        db.refresh(result)
        assert result.doctor_id == owner.doctor_id  # o'zgarmadi

    def test_doctor_cannot_edit_others_result(
        self, client, db, make_user, make_doctor, make_patient, login_as
    ):
        """Regressiyaga qarshi: Prompt 5'dagi 403 himoyasi saqlanib
        qolganini tasdiqlaydi."""
        owner = make_user("doctor")
        other_doctor_user = make_user("doctor")
        patient = make_patient()
        result = self._make_result(db, patient, owner.doctor)
        login_as(other_doctor_user)

        resp = client.post(
            f"/lab-results/edit/{result.id}",
            data={
                "doctor_id": "0",
                "indicators_json": "{}",
                "note": "",
                "status_val": "Tayyor",
            },
        )

        assert resp.status_code == 403

    def test_cashier_still_forbidden(self, client, db, make_user, make_doctor, make_patient, login_as):
        cashier = make_user("cashier")
        doctor = make_doctor()
        patient = make_patient()
        result = self._make_result(db, patient, doctor)
        login_as(cashier)

        resp = client.post(
            f"/lab-results/edit/{result.id}",
            data={
                "doctor_id": "0",
                "indicators_json": "{}",
                "note": "",
                "status_val": "Tayyor",
            },
        )

        assert resp.status_code == 403


# ==============================================
# B) SESSIYA MUDDATI — SystemSettings.session_timeout_minutes bilan bog'liq
# ==============================================
class TestSessionTimeoutFromSystemSettings:
    PROTECTED_ENDPOINT = "/api/auth/users"  # require_admin_or_assistant -> get_current_user

    def _issue_token_at(self, user_id: int, issued_at: float) -> str:
        """create_session_token bilan bir xil formatda, lekin
        o'tmishdagi (yoki kelajakdagi) issued_at bilan token yasaydi —
        muddat tugash chegarasini test qilish uchun."""
        payload = f"{user_id}:{int(issued_at)}"
        return f"{payload}:{auth._sign(payload)}"

    def test_no_settings_row_falls_back_to_default(self, client, db, make_user):
        """DB'da hali SystemSettings qatori yo'q bo'lsa (get-or-create
        ISHLATILMAYDI — apply_update singari yon ta'sirsiz o'qish),
        auth.SESSION_MAX_AGE_SECONDS (standart/env) qo'llanilishi
        kerak."""
        assert db.query(models.SystemSettings).first() is None

        admin = make_user("admin")
        token = self._issue_token_at(admin.id, time.time() - 5)
        client.cookies.set("cf_session", token)

        resp = client.get(self.PROTECTED_ENDPOINT)
        assert resp.status_code == 200

    def test_short_db_timeout_expires_session(self, client, db, make_user):
        """SystemSettings.session_timeout_minutes juda qisqa (1 daqiqa)
        qilib qo'yilsa, 90 soniya oldin chiqarilgan token endi
        YAROQSIZ bo'lishi kerak — garchi env/standart 8 soat bo'lsa
        ham (DB qiymati ustunlik qilishi kerak)."""
        settings = models.SystemSettings(session_timeout_minutes=1)
        db.add(settings)
        db.commit()

        admin = make_user("admin")
        token = self._issue_token_at(admin.id, time.time() - 90)
        client.cookies.set("cf_session", token)

        resp = client.get(self.PROTECTED_ENDPOINT)
        assert resp.status_code == 401

    def test_long_db_timeout_keeps_session_valid(self, client, db, make_user):
        """SystemSettings.session_timeout_minutes uzun (120 daqiqa)
        qilib qo'yilganda, 90 soniya oldin chiqarilgan token hamon
        YAROQLI bo'lishi kerak."""
        settings = models.SystemSettings(session_timeout_minutes=120)
        db.add(settings)
        db.commit()

        admin = make_user("admin")
        token = self._issue_token_at(admin.id, time.time() - 90)
        client.cookies.set("cf_session", token)

        resp = client.get(self.PROTECTED_ENDPOINT)
        assert resp.status_code == 200

    def test_changing_timeout_takes_effect_without_relogin(self, client, db, make_user):
        """Eng muhim stsenariy: bitta token, ikki xil DB sozlamasi —
        birinchisida yaroqli, admin muddatni qisqartirgandan KEYIN
        (token/sessiya QAYTA yaratilmasdan) darhol yaroqsiz bo'lishi
        kerak."""
        settings = models.SystemSettings(session_timeout_minutes=120)
        db.add(settings)
        db.commit()

        admin = make_user("admin")
        token = self._issue_token_at(admin.id, time.time() - 90)
        client.cookies.set("cf_session", token)

        # 1) Uzun muddat bilan — hali yaroqli.
        resp1 = client.get(self.PROTECTED_ENDPOINT)
        assert resp1.status_code == 200

        # 2) Admin muddatni 1 daqiqaga qisqartiradi.
        settings.session_timeout_minutes = 1
        db.commit()

        # 3) XUDDI SHU token, qayta login qilinmasdan — endi rad etiladi.
        resp2 = client.get(self.PROTECTED_ENDPOINT)
        assert resp2.status_code == 401

    def test_update_system_settings_endpoint_changes_effective_timeout(
        self, client, db, make_user, login_as
    ):
        """Integratsiya: haqiqiy PUT /settings/system endpointi orqali
        o'zgartirilgan qiymat ham amalda ishlashini tasdiqlaydi (faqat
        DB qatorini qo'lda o'zgartirish emas)."""
        admin = make_user("admin")
        login_as(admin)

        resp = client.put(
            "/settings/system",
            json={
                "timezone": "Asia/Tashkent",
                "date_format": "dd.MM.yyyy",
                "session_timeout_minutes": 5,
                "max_login_attempts": 5,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["session_timeout_minutes"] == 5

        other_admin = make_user("admin")
        old_token = self._issue_token_at(other_admin.id, time.time() - 400)  # > 5 daqiqa
        client.cookies.set("cf_session", old_token)

        resp2 = client.get(self.PROTECTED_ENDPOINT)
        assert resp2.status_code == 401
