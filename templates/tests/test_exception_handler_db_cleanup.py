# tests/test_exception_handler_db_cleanup.py
"""
PROMPT 15 — DB connection sizishi.

Bag (tuzatilgunga qadar): `main.py :: http_exception_with_security_logging`
403 xatoliklarni loglash uchun `db_gen = get_db(); db = next(db_gen)`
qilib, keyin faqat `db.close()`ni qo'lda chaqirardi. Bu SESSIYANI
yopadi, lekin `get_db()` GENERATOR'ining o'zi hech qachon `.close()`
olmagani uchun, uning ichidagi `finally` bloki DARHOL ishga
tushmaydi — CPython'da odatda generator refsanog'i nolga tushganda
GC "tasodifan" yopib qo'yadi, lekin bunga tayanish shart emas,
ayniqsa agar shu blokdagi kod (masalan `record_unauthorized_access`)
xatolik bersa — o'sha holatda ham resurs tozalanishi KAFOLATLANGAN
bo'lishi kerak.

Tuzatish: endi `contextlib.closing(db_gen)` ishlatiladi — bu
`db_gen.close()`ni try/finally semantikasi bilan, blok ichida xatolik
bo'ladimi-yo'qmi, HAR DOIM chaqiradi.

Bu testlar buni: `get_db()`ning HAQIQIY generatorini kuzatuvchi
(tracking) o'rovchi bilan almashtirib, chiqarilgan Session'ning
`.close()` metodi chaqirilganini tekshiradi.
"""
import main


def _install_tracking_get_db(monkeypatch):
    """`main.get_db`ni HAQIQIY `database.get_db()` generatorini
    o'raydigan, lekin chiqargan Session'ning `.close()` chaqirilganini
    kuzatib boradigan versiya bilan almashtiradi. Boshqa modullardagi
    (masalan modules/doctors.py) `Depends(get_db)`larga TA'SIR
    qilmaydi — ular o'zining alohida `from database import get_db`
    nusxasidan foydalanadi, faqat main.py ichidagi chaqiruv
    almashtiriladi."""
    from database import get_db as real_get_db

    state = {"closed": False}

    def tracking_get_db():
        gen = real_get_db()
        db = next(gen)
        original_close = db.close

        def tracked_close():
            state["closed"] = True
            original_close()

        db.close = tracked_close
        try:
            yield db
        finally:
            gen.close()

    monkeypatch.setattr(main, "get_db", tracking_get_db)
    return state


def _add_doctor(client):
    """Faqat admin uchun ruxsat etilgan endpoint — boshqa rol bilan
    chaqirilsa har doim 403 qaytaradi, shuning uchun
    http_exception_with_security_logging'ni ishga tushirish uchun
    qulay."""
    return client.post(
        "/api/doctors/add",
        json={"fullname": "Dr. Test", "specialty": "Terapevt", "consultation_price": 100000},
    )


class TestExceptionHandlerClosesDbSession:
    def test_db_session_closed_even_when_logging_function_raises(
        self, client, login_as, make_user, monkeypatch
    ):
        """`record_unauthorized_access` (logging) xatolik bersa ham,
        403 javob normal qaytishi VA DB sessiyasi baribir yopilishi
        kerak — shu Prompt 15ning asosiy talabi."""
        state = _install_tracking_get_db(monkeypatch)

        def _boom(*args, **kwargs):
            raise RuntimeError("logging xizmati vaqtincha ishlamayapti")

        monkeypatch.setattr(main, "record_unauthorized_access", _boom)

        reception = login_as(make_user("reception"))
        resp = _add_doctor(reception)

        assert resp.status_code == 403
        assert state["closed"] is True, "Logging xatolik bergan bo'lsa ham DB sessiyasi yopilishi kerak edi"

    def test_db_session_closed_on_normal_403_logging(self, client, login_as, make_user, monkeypatch):
        """Nazorat testi (regressiyaga qarshi): logging funksiyasi
        normal ishlaganda ham sessiya yopilishi kerak — birinchi test
        FAQAT xatolik holatini emas, ikkalasini ham qamrab olishi
        uchun."""
        state = _install_tracking_get_db(monkeypatch)

        reception = login_as(make_user("reception"))
        resp = _add_doctor(reception)

        assert resp.status_code == 403
        assert state["closed"] is True

    def test_response_is_still_returned_when_logging_fails(self, client, login_as, make_user, monkeypatch):
        """DB/logging muammosi API javobini "yutib qo'ymasligi" kerak —
        foydalanuvchi hamon aniq 403 javobini olishi kerak, 500 emas."""
        monkeypatch.setattr(
            main,
            "record_unauthorized_access",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("logging xato")),
        )

        reception = login_as(make_user("reception"))
        resp = _add_doctor(reception)

        assert resp.status_code == 403
        assert "detail" in resp.json()
