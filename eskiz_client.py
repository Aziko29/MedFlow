# eskiz_client.py
"""
Eskiz.uz SMS integratsiyasi (FAZA 1 — ixtiyoriy kanal).

Telegram ishlatmaydigan bemorlar uchun SMS yuborish, va FAZA 2'dagi
bemor portali login kodi (OTP) uchun infratuzilma.

Environment o'zgaruvchilari (.env):
    ESKIZ_EMAIL     - Eskiz.uz akkaunt email (majburiy, agar SMS
                      yoqilishi kerak bo'lsa)
    ESKIZ_PASSWORD  - Eskiz.uz akkaunt paroli (majburiy, yuqoridagi
                      bilan bir juftlikda)
    ESKIZ_SENDER_NICK - jo'natuvchi nomi (ixtiyoriy, standart "4546")

ESKIZ_EMAIL/ESKIZ_PASSWORD o'rnatilmasa, servis butunlay o'chirilgan
holda ishlaydi (log yozib, jim o'tib ketadi) — TELEGRAM_BOT_TOKEN
o'rnatilmagan holatdagi reminder_service.py pattern'i bilan bir xil:
hech qachon import vaqtida yoki chaqirilganda dastur yiqilmaydi.
"""
import logging
import os
import threading
import time
from typing import Optional

import httpx

logger = logging.getLogger("medflow.sms")

ESKIZ_EMAIL = os.environ.get("ESKIZ_EMAIL", "").strip()
ESKIZ_PASSWORD = os.environ.get("ESKIZ_PASSWORD", "").strip()
ESKIZ_SENDER_NICK = os.environ.get("ESKIZ_SENDER_NICK", "4546").strip()

ESKIZ_API_BASE = "https://notify.eskiz.uz/api"

# Eskiz JWT token odatda 30 kun amal qiladi — biz ehtiyot uchun 20 soatda
# bir marta yangilaymiz (SESSION_MAX_AGE kabi loyihadagi boshqa muddatlarga
# o'xshab, "erta yangilash — keyin qulab tushishdan yaxshi").
_TOKEN_TTL_SECONDS = 20 * 60 * 60

_token_lock = threading.Lock()
_cached_token: Optional[str] = None
_token_fetched_at: float = 0.0


def sms_enabled() -> bool:
    """SMS servisi sozlanganmi (credential borligini bildiradi, tarmoq
    holatini emas). `/health` endpoint'i va boshqa modullar shu orqali
    "SMS umuman ishlashi mumkinmi" tekshiradi."""
    return bool(ESKIZ_EMAIL and ESKIZ_PASSWORD)


def _fetch_token() -> Optional[str]:
    """Eskiz.uz'dan yangi auth token oladi. Xato bo'lsa None qaytaradi,
    hech qachon exception otmaydi — chaqiruvchi (send_sms) har doim
    xavfsiz davom eta olishi kerak."""
    if not sms_enabled():
        return None
    try:
        resp = httpx.post(
            f"{ESKIZ_API_BASE}/auth/login",
            data={"email": ESKIZ_EMAIL, "password": ESKIZ_PASSWORD},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        token = (data.get("data") or {}).get("token")
        if not token:
            logger.warning("Eskiz auth javobida token topilmadi: %s", data)
            return None
        return token
    except Exception as exc:
        logger.warning("Eskiz auth xato: %s", exc)
        return None


def _get_token() -> Optional[str]:
    """Keshlangan tokenni qaytaradi, muddati tugagan/mavjud bo'lmasa
    qayta oladi. Bir nechta thread bir vaqtda token so'ramasligi uchun
    lock bilan himoyalangan (reminder_service.py'dagi singleton-lock
    mantig'iga o'xshab, lekin bu yerda process-ichi thread lock kifoya —
    token olish ko'p worker orasida umumiy bo'lishi shart emas, har biri
    o'zining tokenini olishi mumkin)."""
    global _cached_token, _token_fetched_at
    if not sms_enabled():
        return None
    with _token_lock:
        now = time.monotonic()
        if _cached_token and (now - _token_fetched_at) < _TOKEN_TTL_SECONDS:
            return _cached_token
        token = _fetch_token()
        if token:
            _cached_token = token
            _token_fetched_at = now
        return token


def _send_once(token: str, phone: str, text: str) -> bool:
    try:
        resp = httpx.post(
            f"{ESKIZ_API_BASE}/message/sms/send",
            headers={"Authorization": f"Bearer {token}"},
            data={
                "mobile_phone": phone.lstrip("+"),
                "message": text,
                "from": ESKIZ_SENDER_NICK,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        # Eskiz muvaffaqiyatli qabul qilganda status "waiting"/"success"
        # kabi qiymat qaytaradi; aniq xato bo'lsa bu yerga tushamiz emas —
        # raise_for_status() allaqachon HTTP xatoларni tutadi, shuning
        # uchun shu yerga yetib kelgan javob amaliy jihatdan qabul
        # qilingan deb hisoblanadi.
        logger.info("Eskiz SMS yuborildi: %s -> %s", phone, data.get("id", "?"))
        return True
    except Exception as exc:
        logger.warning("Eskiz SMS yuborishda xato (%s): %s", phone, exc)
        return False


def send_sms(phone: str, text: str) -> bool:
    """Asosiy public funksiya. Muvaffaqiyatli bo'lsa True, har qanday
    sabab bilan (sozlanmagan, tarmoq xatosi, auth xatosi) muvaffaqiyatsiz
    bo'lsa False qaytaradi — HECH QACHON exception tashlamaydi, shuning
    uchun chaqiruvchi kod (masalan bemor portali OTP oqimi) SMS
    yuborilmagan taqdirda ham normal davom eta oladi.
    """
    if not sms_enabled():
        logger.info("Eskiz sozlanmagan (ESKIZ_EMAIL/ESKIZ_PASSWORD bo'sh), SMS yuborilmadi: %s", phone)
        return False

    token = _get_token()
    if not token:
        return False

    ok = _send_once(token, phone, text)
    if ok:
        return True

    # 🔁 Retry: bitta marta qayta urinish (masalan token eskirgan/bekor
    # qilingan bo'lishi mumkin — shu sabab tokenni majburan yangilab,
    # yana bir bor sinaymiz). reminder_service.py'dagi Telegram retry
    # mantig'iga o'xshab, lekin SMS uchun ortiqcha murakkab exponential
    # backoff shart emas — bitta qayta urinish yetarli.
    global _cached_token, _token_fetched_at
    with _token_lock:
        _cached_token = None
        _token_fetched_at = 0.0
    token = _get_token()
    if not token:
        return False
    return _send_once(token, phone, text)
