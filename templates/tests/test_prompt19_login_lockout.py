# tests/test_prompt19_login_lockout.py
"""
Prompt 19 — SystemSettings.max_login_attempts endi haqiqiy ta'sirga ega:
ketma-ket noto'g'ri parol bilan urinishlar shu limitga yetganda, hisob
vaqtincha (LOGIN_LOCKOUT_MINUTES, standart 15 daqiqa) bloklanadi va login
— parol to'g'ri bo'lsa ham — 423 (Locked) bilan rad etiladi.

Haqiqiy /api/auth/login endpointi ishlatiladi (TestClient), chunki aynan
shu endpointdagi xulq-atvor tekshirilyapti. Urinishlar soni (4 ta)
5/minute slowapi rate-limitidan pastda — conftest.py'dagi
`_reset_rate_limiter` autouse fixture bilan birga har bir test uchun
mustaqil.
"""
import models
from conftest import DEFAULT_PASSWORD


def _set_max_login_attempts(db, value: int) -> models.SystemSettings:
    settings = models.SystemSettings(max_login_attempts=value)
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


class TestMaxLoginAttemptsLockout:
    def test_fourth_wrong_attempt_locks_account_when_limit_is_three(self, client, make_user, db):
        """max_login_attempts=3 bo'lganda: 1-3 urinish oddiy 401 (yoki
        3-chisi hisobni bloklaydi -> 423), 4-urinish esa (hatto TO'G'RI
        parol bilan bo'lsa ham) hisob bloklangani uchun 423 qaytarishi
        kerak."""
        user = make_user("reception", username="lockout_test_user")
        _set_max_login_attempts(db, 3)

        # 1- va 2- urinishlar: oddiy noto'g'ri parol xabari
        for _ in range(2):
            resp = client.post(
                "/api/auth/login",
                json={"username": "lockout_test_user", "password": "notogri-parol"},
            )
            assert resp.status_code == 401

        # 3- urinish: limitga yetadi -> hisob ENDIGINA bloklanadi (423)
        resp3 = client.post(
            "/api/auth/login",
            json={"username": "lockout_test_user", "password": "notogri-parol"},
        )
        assert resp3.status_code == 423
        assert "bloklandi" in resp3.json()["detail"]

        # DB darajasida ham tasdiqlaymiz: locked_until kelajakda,
        # hisoblagich 0'ga qaytgan.
        db.refresh(user)
        assert user.locked_until is not None
        assert user.failed_login_attempts == 0

        # 4- urinish: bu safar TO'G'RI parol bilan — lekin hisob hali
        # bloklangani uchun baribir rad etilishi kerak (423), parol
        # to'g'riligi buni o'zgartirmaydi.
        resp4 = client.post(
            "/api/auth/login",
            json={"username": "lockout_test_user", "password": DEFAULT_PASSWORD},
        )
        assert resp4.status_code == 423
        assert "bloklandi" in resp4.json()["detail"] or "bloklan" in resp4.json()["detail"]

    def test_default_limit_of_five_is_used_when_no_settings_row(self, client, make_user, db):
        """SystemSettings qatori hali yaratilmagan bo'lsa ham (get-or-create
        hali ishga tushmagan), standart limit (5) qo'llanishi kerak —
        4 ta noto'g'ri urinish hali bloklamaydi (401 bo'lib qoladi)."""
        make_user("reception", username="lockout_default_user")
        assert db.query(models.SystemSettings).first() is None

        for _ in range(4):
            resp = client.post(
                "/api/auth/login",
                json={"username": "lockout_default_user", "password": "notogri-parol"},
            )
            assert resp.status_code == 401

    def test_successful_login_clears_previous_failed_attempts(self, client, make_user, db):
        """Muvaffaqiyatli login hisoblagichni 0'ga qaytaradi — limitga
        yetmagan oldingi muvaffaqiyatsizliklar "unutiladi"."""
        user = make_user("reception", username="lockout_reset_user")
        _set_max_login_attempts(db, 3)

        for _ in range(2):
            client.post(
                "/api/auth/login",
                json={"username": "lockout_reset_user", "password": "notogri-parol"},
            )

        ok = client.post(
            "/api/auth/login",
            json={"username": "lockout_reset_user", "password": DEFAULT_PASSWORD},
        )
        assert ok.status_code == 200

        db.refresh(user)
        assert user.failed_login_attempts == 0
        assert user.locked_until is None
