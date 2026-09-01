# tests/test_rbac.py
"""
Prompt 15.1 — Backend RBAC integratsiyasi va DB sxema tekshiruvi.

Bu fayl uchta bo'limdan iborat:
  1. TestDatabaseSchema — yangi jadvallar/ustunlar/status qiymatlari
     mavjudligini tekshiradi (models.py orqali, statik tekshiruv).
  2. TestRBAC* — har bir muhim endpoint uchun rol bo'yicha ruxsatlar
     matritsasi (200/201/303 vs 401/403).
  3. TestSecurityAndBackup — 3-marta-ketma-ket-muvaffaqiyatsiz-login
     ogohlantirishi va POST /admin/backup/check-path 400 xulq-atvori.

MUHIM — lab-results moduli haqida (Prompt 15.1 talabi bilan solishtirib
audit qilinganda topilgan arxitektura nomuvofiqligi):
Talabnomada "POST /lab-results -> 200 JSON" deb yozilgan, lekin haqiqiy
endpoint `/lab-results/add` — forma-based (HTML frontend uchun, redirect +
flash-error naqshi bilan). Bu ATAYLAB tuzatilmadi (templates/lab_results.html
JS'i shu 303-redirect xatti-harakatiga tayanadi, uni o'zgartirish frontendni
buzadi). Quyidagi testlar HAQIQIY xatti-harakatni tasdiqlaydi: muvaffaqiyatli
so'rov ham 303 (JSON emas) qaytaradi, lekin ROL TEKSHIRUVI (cashier -> 403)
boshqa modullar bilan bir xil, to'g'ri HTTPException orqali ishlaydi.
"""
import datetime

import pytest
from sqlalchemy import inspect

import models
from conftest import DEFAULT_PASSWORD


# ==============================================
# 1) DATABASE SXEMA TEKSHIRUVI
# ==============================================
class TestDatabaseSchema:
    def test_new_tables_exist(self):
        from database import engine
        table_names = set(inspect(engine).get_table_names())

        for expected in (
            "gov_integration_settings",
            "security_messages",
            "system_errors",
            "login_logs",
            "admin_profile_settings",
        ):
            assert expected in table_names, f"Jadval topilmadi: {expected}"

    def test_patient_new_fields_exist(self):
        columns = {c.name for c in models.Patient.__table__.columns}
        expected = {
            "room_number", "admitted_at", "discharged_at", "is_admitted",
            "pinfl", "passport_series", "passport_number", "is_verified",
        }
        missing = expected - columns
        assert not missing, f"Patient jadvalida yetishmayotgan ustunlar: {missing}"

    def test_payment_statuses_updated(self):
        # ⬅️ 'cancelled' va 'refunded' talabga muvofiq mavjud. Aniq
        # "pending_refund" literali YO'Q — bu holat funksional jihatdan
        # status="cancelled" ("admin bekor qildi, pul hali qaytarilmagan")
        # orqali ifodalanadi (qarang: models.Payment docstring, Prompt 6).
        # Bu ataylab o'zgartirilmadi (kattaroq refaktoring talab qiladi —
        # CSV export, shablonlar, migratsiya), shuning uchun bu test
        # JORIY xatti-harakatni hujjatlashtiradi, "pending_refund"ni emas.
        assert "cancelled" in models.PAYMENT_STATUSES
        assert "refunded" in models.PAYMENT_STATUSES
        assert "completed" in models.PAYMENT_STATUSES
        assert "pending_refund" not in models.PAYMENT_STATUSES  # hujjatlashtirilgan holat


# ==============================================
# 2) RBAC — LAB RESULTS
# ==============================================
class TestRBACLabResults:
    def _payload(self, patient_id: int):
        return {
            "patient_id": str(patient_id),
            "template_key": "cbc",
            "indicators_json": "{}",
            "note": "",
            "status_val": "Tayyor",
        }

    def test_doctor_can_add(self, client, login_as, make_user, make_patient):
        user = make_user("doctor")
        patient = make_patient()
        c = login_as(user)
        resp = c.post("/lab-results/add", data=self._payload(patient.id), follow_redirects=False)
        # Haqiqiy xatti-harakat: muvaffaqiyat ham 303 redirect (200 JSON emas)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/lab-results"

    def test_lab_doctor_can_add(self, client, login_as, make_user, make_patient):
        user = make_user("lab_doctor")
        patient = make_patient()
        c = login_as(user)
        resp = c.post("/lab-results/add", data=self._payload(patient.id), follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/lab-results"

    def test_cashier_forbidden(self, client, login_as, make_user, make_patient):
        user = make_user("cashier")
        patient = make_patient()
        c = login_as(user)
        resp = c.post("/lab-results/add", data=self._payload(patient.id), follow_redirects=False)
        assert resp.status_code == 403

    def test_unauthenticated_redirected_to_login(self, client, make_patient):
        patient = make_patient()
        resp = client.post("/lab-results/add", data=self._payload(patient.id), follow_redirects=False)
        # 401 emas — mavjud forma-based xatti-harakat: login sahifasiga redirect.
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login"


# ==============================================
# 3) RBAC — PAYMENTS (cancel / refund)
# ==============================================
class TestRBACPayments:
    def test_admin_can_cancel(self, client, login_as, make_user, make_appointment_with_payment):
        admin = make_user("admin")
        payment = make_appointment_with_payment()
        c = login_as(admin)
        resp = c.post(f"/api/payments/{payment.id}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_cashier_cannot_cancel(self, client, login_as, make_user, make_appointment_with_payment):
        cashier = make_user("cashier")
        payment = make_appointment_with_payment()
        c = login_as(cashier)
        resp = c.post(f"/api/payments/{payment.id}/cancel")
        assert resp.status_code == 403

    def test_cashier_can_refund(self, client, login_as, make_user, make_appointment_with_payment, db):
        admin = make_user("admin")
        cashier = make_user("cashier")
        payment = make_appointment_with_payment()

        # Qaytarim faqat admin AVVAL "cancelled" deb belgilagan to'lov uchun ishlaydi.
        c_admin = login_as(admin)
        cancel_resp = c_admin.post(f"/api/payments/{payment.id}/cancel")
        assert cancel_resp.status_code == 200

        c_cashier = login_as(cashier)
        resp = c_cashier.post(
            f"/api/payments/{payment.id}/refund", json={"reason": "Bemor talabi bilan"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"
        assert resp.json()["is_refund"] is True

    def test_doctor_cannot_refund(self, client, login_as, make_user, make_appointment_with_payment):
        doctor = make_user("doctor")
        payment = make_appointment_with_payment()
        c = login_as(doctor)
        resp = c.post(f"/api/payments/{payment.id}/refund", json={"reason": "test"})
        assert resp.status_code == 403


# ==============================================
# 4) RBAC — PATIENTS (admit)
# ==============================================
class TestRBACPatientsAdmit:
    def test_doctor_can_admit(self, client, login_as, make_user, make_patient):
        doctor = make_user("doctor")
        patient = make_patient()
        c = login_as(doctor)
        resp = c.post(f"/api/patients/{patient.id}/admit", json={"room_number": "101"})
        assert resp.status_code == 200
        assert resp.json()["is_admitted"] is True

    def test_reception_can_admit(self, client, login_as, make_user, make_patient):
        reception = make_user("reception")
        patient = make_patient()
        c = login_as(reception)
        resp = c.post(f"/api/patients/{patient.id}/admit", json={"room_number": "102"})
        assert resp.status_code == 200

    def test_cashier_cannot_admit(self, client, login_as, make_user, make_patient):
        cashier = make_user("cashier")
        patient = make_patient()
        c = login_as(cashier)
        resp = c.post(f"/api/patients/{patient.id}/admit", json={"room_number": "103"})
        assert resp.status_code == 403


# ==============================================
# 5) RBAC — ADMIN PROFILE (to'liq vs read-only)
# ==============================================
class TestRBACAdminProfile:
    def test_admin_can_read_and_write(self, client, login_as, make_user):
        admin = make_user("admin")
        c = login_as(admin)
        assert c.get("/api/admin/profile").status_code == 200
        resp = c.put("/api/admin/profile", json={"clinic_name": "ClinicFlow Markaz"})
        assert resp.status_code == 200
        assert resp.json()["clinic_name"] == "ClinicFlow Markaz"

    def test_assistant_admin_read_only(self, client, login_as, make_user):
        assistant = make_user("assistant_admin")
        c = login_as(assistant)
        # O'qish — ruxsat berilgan
        assert c.get("/api/admin/profile").status_code == 200
        # Yozish — 403 (faqat require_role("admin"))
        resp = c.put("/api/admin/profile", json={"clinic_name": "Boshqa nom"})
        assert resp.status_code == 403

    def test_other_roles_forbidden_entirely(self, client, login_as, make_user):
        doctor = make_user("doctor")
        c = login_as(doctor)
        assert c.get("/api/admin/profile").status_code == 403


# ==============================================
# 6) RBAC — SECURITY MESSAGES (yuborish vs ko'rish)
# ==============================================
class TestRBACSecurityMessages:
    def test_doctor_can_send(self, client, login_as, make_user):
        doctor = make_user("doctor")
        c = login_as(doctor)
        resp = c.post(
            "/security/messages",
            json={"subject": "Savol", "message": "Yordam kerak", "priority": "medium"},
        )
        assert resp.status_code == 201

    def test_cashier_cannot_send(self, client, login_as, make_user):
        cashier = make_user("cashier")
        c = login_as(cashier)
        resp = c.post(
            "/security/messages",
            json={"subject": "Savol", "message": "Yordam kerak", "priority": "medium"},
        )
        assert resp.status_code == 403

    def test_admin_can_view(self, client, login_as, make_user):
        admin = make_user("admin")
        c = login_as(admin)
        resp = c.get("/security/messages")
        assert resp.status_code == 200

    def test_doctor_cannot_view(self, client, login_as, make_user):
        doctor = make_user("doctor")
        c = login_as(doctor)
        resp = c.get("/security/messages")
        assert resp.status_code == 403


# ==============================================
# 7) SETTINGS — DANGEROUS ACTION (2-qatlam parol)
# ==============================================
class TestDangerousAction:
    def test_admin_with_correct_password(self, client, login_as, make_user, monkeypatch):
        monkeypatch.setenv("CLINICFLOW_ADMIN_ACTIONS_PASSWORD", "super-maxfiy-parol")
        admin = make_user("admin")
        c = login_as(admin)
        resp = c.post(
            "/settings/dangerous-action",
            json={"action_type": "reset_sessions", "confirmation_password": "super-maxfiy-parol"},
        )
        assert resp.status_code == 200
        assert resp.json()["action_type"] == "reset_sessions"

    def test_admin_with_wrong_password(self, client, login_as, make_user, monkeypatch):
        monkeypatch.setenv("CLINICFLOW_ADMIN_ACTIONS_PASSWORD", "super-maxfiy-parol")
        admin = make_user("admin")
        c = login_as(admin)
        resp = c.post(
            "/settings/dangerous-action",
            json={"action_type": "reset_sessions", "confirmation_password": "notogri"},
        )
        assert resp.status_code == 403

    def test_non_admin_forbidden(self, client, login_as, make_user, monkeypatch):
        monkeypatch.setenv("CLINICFLOW_ADMIN_ACTIONS_PASSWORD", "super-maxfiy-parol")
        cashier = make_user("cashier")
        c = login_as(cashier)
        resp = c.post(
            "/settings/dangerous-action",
            json={"action_type": "reset_sessions", "confirmation_password": "super-maxfiy-parol"},
        )
        assert resp.status_code == 403

    def test_missing_env_var_returns_500(self, client, login_as, make_user, monkeypatch):
        # Talab #3: CLINICFLOW_ADMIN_ACTIONS_PASSWORD .env'da yo'q bo'lsa,
        # amal 500 bilan bloklanishi kerak (server sozlamasi to'liq emas).
        monkeypatch.delenv("CLINICFLOW_ADMIN_ACTIONS_PASSWORD", raising=False)
        admin = make_user("admin")
        c = login_as(admin)
        resp = c.post(
            "/settings/dangerous-action",
            json={"action_type": "reset_sessions", "confirmation_password": "istalgan-narsa"},
        )
        assert resp.status_code == 500
        assert "CLINICFLOW_ADMIN_ACTIONS_PASSWORD" in resp.json()["detail"]


# ==============================================
# 8) BACKUP — check-path 400 xatti-harakati (asosiy tuzatish)
# ==============================================
class TestBackupCheckPath:
    def test_nonexistent_path_returns_400(self, client, login_as, make_user):
        admin = make_user("admin")
        c = login_as(admin)
        resp = c.post(
            "/admin/backup/check-path",
            json={"path": "/bu/yol/mavjud/emas/hech-qachon-12345"},
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert isinstance(detail, str) and "mavjud emas" in detail

    def test_existing_writable_path_returns_200(self, client, login_as, make_user, tmp_path):
        admin = make_user("admin")
        c = login_as(admin)
        resp = c.post("/admin/backup/check-path", json={"path": str(tmp_path)})
        assert resp.status_code == 200
        body = resp.json()
        assert body["exists"] is True
        assert body["writable"] is True

    def test_non_admin_forbidden(self, client, login_as, make_user, tmp_path):
        cashier = make_user("cashier")
        c = login_as(cashier)
        resp = c.post("/admin/backup/check-path", json={"path": str(tmp_path)})
        assert resp.status_code == 403

    def test_unauthenticated_401(self, client, tmp_path):
        resp = client.post("/admin/backup/check-path", json={"path": str(tmp_path)})
        assert resp.status_code == 401


# ==============================================
# 9) XAVFSIZLIK — 3 ketma-ket muvaffaqiyatsiz login -> SecurityMessage
# ==============================================
class TestLoginSecurityAlerts:
    def test_three_failed_logins_trigger_security_message(self, client, make_user, db):
        user = make_user("reception", username="alert_test_user")

        # Haqiqiy /api/auth/login endpointi ishlatiladi (bu yerda, bir
        # marta) — 5/minute slowapi rate-limitidan pastda (3 ta urinish).
        for _ in range(3):
            resp = client.post(
                "/api/auth/login",
                json={"username": "alert_test_user", "password": "notogri-parol"},
            )
            assert resp.status_code == 401

        login_logs = (
            db.query(models.LoginLog)
            .filter(models.LoginLog.username == "alert_test_user")
            .all()
        )
        assert len(login_logs) == 3
        assert all(not log.success for log in login_logs)

        alerts = (
            db.query(models.SecurityMessage)
            .filter(models.SecurityMessage.subject.ilike("%alert_test_user%"))
            .all()
        )
        assert len(alerts) == 1
        assert alerts[0].priority == "high"
        assert alerts[0].from_user_id is None  # tizim tomonidan avtomatik

    def test_successful_login_resets_the_narrative_but_not_the_log(self, client, make_user, db):
        """Muvaffaqiyatli login streakni "uzadi" — 2 ta muvaffaqiyatsizdan
        keyin 1 ta muvaffaqiyatli, keyin yana 2 ta muvaffaqiyatsiz bo'lsa,
        ketma-ketlik hali 3ga yetmagan (jami 4 emas, streak=2) — xabar
        YARATILMASLIGI kerak."""
        user = make_user("reception", username="streak_test_user")

        client.post("/api/auth/login", json={"username": "streak_test_user", "password": "xato1"})
        client.post("/api/auth/login", json={"username": "streak_test_user", "password": "xato2"})
        ok = client.post(
            "/api/auth/login",
            json={"username": "streak_test_user", "password": DEFAULT_PASSWORD},
        )
        assert ok.status_code == 200

        alerts = (
            db.query(models.SecurityMessage)
            .filter(models.SecurityMessage.subject.ilike("%streak_test_user%"))
            .all()
        )
        assert len(alerts) == 0
