# tests/test_patient_portal_otp.py
"""
PROMPT 1 — Bemor portali OTP: PatientLoginOTP.attempt_count.

Loyihani tekshirganda ma'lum bo'ldiki, models.py dagi PatientLoginOTP
klassida attempt_count ustuni VA uni yaratuvchi Alembic migratsiyasi
(d1e2f3a4b5c6_add_patient_login_otp.py) allaqachon mavjud — ular
modules/patient_portal.py:verify_login_code funksiyasi bilan bir xil
commit'da qo'shilgan edi. Shuning uchun bu faylda faqat talab qilingan
uchinchi qadam — pytest testi — yoziladi, u ikki narsani tasdiqlaydi:

  1. Sxema darajasida: attempt_count ustuni jadvalda mavjud va uning
     standart (default) qiymati 0.
  2. Xatti-harakat darajasida: noto'g'ri kod bilan MAX_VERIFY_ATTEMPTS
     martadan ko'p urinish qilinganda, endpoint har doim bir xil
     (enumeration-himoyalangan) 401 xatosini qaytaradi va bazadagi
     attempt_count qiymati oshib boradi hamda chegaradan oshmaydi.
"""
import datetime
import hashlib

from sqlalchemy import inspect

import models
from modules.patient_portal import MAX_VERIFY_ATTEMPTS, OTP_TTL_SECONDS


# ==============================================
# 1) SXEMA TEKSHIRUVI
# ==============================================
class TestPatientLoginOTPSchema:
    def test_attempt_count_column_exists(self):
        from database import engine

        columns = {
            col["name"]: col
            for col in inspect(engine).get_columns("patient_login_otp")
        }
        assert "attempt_count" in columns, (
            "patient_login_otp jadvalida attempt_count ustuni topilmadi"
        )
        assert columns["attempt_count"]["nullable"] is False

    def test_new_otp_row_defaults_attempt_count_to_zero(self, db, make_patient):
        patient = make_patient()
        otp = models.PatientLoginOTP(
            patient_id=patient.id,
            code_hash=hashlib.sha256(b"123456").hexdigest(),
            expires_at=datetime.datetime.utcnow()
            + datetime.timedelta(seconds=OTP_TTL_SECONDS),
        )
        db.add(otp)
        db.commit()
        db.refresh(otp)

        assert otp.attempt_count == 0


# ==============================================
# 2) XATTI-HARAKAT TEKSHIRUVI — verify_login_code
# ==============================================
class TestVerifyLoginCodeAttemptLockout:
    def _make_otp(self, db, patient, code="123456"):
        otp = models.PatientLoginOTP(
            patient_id=patient.id,
            code_hash=hashlib.sha256(code.encode()).hexdigest(),
            expires_at=datetime.datetime.utcnow()
            + datetime.timedelta(seconds=OTP_TTL_SECONDS),
        )
        db.add(otp)
        db.commit()
        db.refresh(otp)
        return otp

    def test_wrong_code_increments_attempt_count(self, client, db, make_patient):
        patient = make_patient(phone="+998901112233")
        otp = self._make_otp(db, patient, code="654321")

        resp = client.post(
            "/portal/login/verify",
            json={"phone": patient.phone, "code": "000000"},
        )

        assert resp.status_code == 401
        db.refresh(otp)
        assert otp.attempt_count == 1

    def test_lockout_after_max_attempts(self, client, db, make_patient):
        patient = make_patient(phone="+998901112244")
        otp = self._make_otp(db, patient, code="654321")

        # MAX_VERIFY_ATTEMPTS marta noto'g'ri kod yuboramiz
        for _ in range(MAX_VERIFY_ATTEMPTS):
            resp = client.post(
                "/portal/login/verify",
                json={"phone": patient.phone, "code": "000000"},
            )
            assert resp.status_code == 401

        db.refresh(otp)
        assert otp.attempt_count == MAX_VERIFY_ATTEMPTS

        # Endi TO'G'RI kod bilan urinsak ham — chegaradan oshgani uchun
        # rad etilishi kerak. PROMPT 2: bu holatda endi generic 401
        # emas, balki aniq 400 + "Urinishlar soni tugadi" xabari
        # qaytishi kerak (AttributeError EMAS, aniq xatolik).
        resp = client.post(
            "/portal/login/verify",
            json={"phone": patient.phone, "code": "654321"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Urinishlar soni tugadi, yangi kod so'rang."

        # attempt_count MAX dan oshib ketmasligi kerak (chegaradan keyin
        # endi tekshiruv kodni solishtirishgacha yetib bormaydi).
        db.refresh(otp)
        assert otp.attempt_count == MAX_VERIFY_ATTEMPTS

    def test_lockout_message_distinct_from_generic_error(self, client, db, make_patient):
        """PROMPT 2 talabi: limitga yetgan holat generic ("Kod noto'g'ri
        yoki muddati tugagan.") xabaridan FARQLI, aniq xabar va boshqa
        status kod (400, 401 emas) bilan qaytishi kerak."""
        patient = make_patient(phone="+998901112266")
        otp = self._make_otp(db, patient, code="654321")
        otp.attempt_count = MAX_VERIFY_ATTEMPTS
        db.commit()

        resp = client.post(
            "/portal/login/verify",
            json={"phone": patient.phone, "code": "000000"},
        )

        assert resp.status_code == 400
        assert resp.json()["detail"] == "Urinishlar soni tugadi, yangi kod so'rang."

    def test_expired_otp_still_returns_generic_error(self, client, db, make_patient):
        """Eskirgan (muddati tugagan) OTP tekshiruvi attempt_count
        tekshiruvidan OLDIN ishlashda davom etishi kerak — PROMPT 2
        o'zgarishi bu mantiqni buzmasligini tasdiqlaydi."""
        patient = make_patient(phone="+998901112277")
        otp = self._make_otp(db, patient, code="654321")
        otp.expires_at = datetime.datetime.utcnow() - datetime.timedelta(seconds=1)
        db.commit()

        resp = client.post(
            "/portal/login/verify",
            json={"phone": patient.phone, "code": "654321"},
        )

        assert resp.status_code == 401
        assert resp.json()["detail"] == "Kod noto'g'ri yoki muddati tugagan."
        db.refresh(otp)
        # Muddati o'tgan bo'lgani uchun attempt_count hech qachon
        # oshirilmasligi kerak (kod solishtirish bosqichigacha yetib
        # bormaydi).
        assert otp.attempt_count == 0

    def test_correct_code_within_limit_logs_in(self, client, db, make_patient):
        patient = make_patient(phone="+998901112255")
        otp = self._make_otp(db, patient, code="654321")

        resp = client.post(
            "/portal/login/verify",
            json={"phone": patient.phone, "code": "654321"},
        )

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        db.refresh(otp)
        assert otp.used_at is not None
