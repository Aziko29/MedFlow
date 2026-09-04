# tests/test_prompt22_forgot_password.py
"""
PROMPT 22 — POST /api/auth/forgot-password sanity testlari.

Login sahifasidagi "Parolni unutdingizmi?" havolasi bosilib, so'rov
yuborilganda bu endpoint adminga SecurityMessage yaratishini tekshiradi.
"""
import models


class TestForgotPasswordEndpoint:
    def test_creates_security_message_with_username(self, client, db):
        resp = client.post("/api/auth/forgot-password", json={"username": "dr_asadov"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "qabul qilindi" in body["message"]

        msg = (
            db.query(models.SecurityMessage)
            .order_by(models.SecurityMessage.id.desc())
            .first()
        )
        assert msg is not None
        assert msg.from_user_id is None  # tizim tomonidan yaratilgan
        assert "dr_asadov" in msg.subject
        assert "dr_asadov" in msg.message

    def test_works_without_username(self, client, db):
        """Login maydoni bo'sh bo'lsa ham so'rov yuborilaveradi."""
        resp = client.post("/api/auth/forgot-password", json={})
        assert resp.status_code == 200

        msg = (
            db.query(models.SecurityMessage)
            .order_by(models.SecurityMessage.id.desc())
            .first()
        )
        assert msg is not None
        assert "login kiritilmagan" in msg.message
