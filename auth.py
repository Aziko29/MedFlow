# auth.py
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Callable, Optional, Dict, Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError
from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.orm import Session

import models
from database import get_db

logger = logging.getLogger(__name__)

# ==============================================
# KONFIGURATSIYA
# ==============================================

# ⬅️ TUZATILDI (2-band, 2.4): endi ishonchsiz default qiymat YO'Q. Agar
# CLINICFLOW_SECRET_KEY o'rnatilmagan bo'lsa, dastur ATAYLAB ishga
# tushmaydi — bu "jimgina" hamma joyda bir xil, oshkora
# ("dev-only-change-me-in-production") kalit bilan productionga chiqib
# ketishning oldini oladi. Kalitni generatsiya qilish:
#   python -c "import secrets; print(secrets.token_hex(32))"
# so'ng uni CLINICFLOW_SECRET_KEY environment o'zgaruvchisiga (yoki .env
# fayliga) yozing.
SECRET_KEY = os.environ.get("CLINICFLOW_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "CLINICFLOW_SECRET_KEY environment o'zgaruvchisi o'rnatilmagan! "
        "python -c \"import secrets; print(secrets.token_hex(32))\" bilan "
        "kalit generatsiya qiling va uni CLINICFLOW_SECRET_KEY sifatida "
        "environment'ga (yoki .env fayliga) yozing."
    )

SESSION_COOKIE_NAME = "cf_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 12  # 12 soat

# ==============================================
# PAROL XESHLASH — ARGON2ID
#
# ⬅️ TUZATILDI (2-band, 2.1): PBKDF2 (10,000 iteratsiya) → Argon2id.
# Argon2id — OWASP tomonidan tavsiya etilgan zamonaviy standart, PBKDF2'ga
# qaraganda GPU/ASIC bilan ommaviy hujumlarga sezilarli darajada
# chidamliroq (xotira-talab qiluvchi algoritm).
#
# ESKI FORMATNI QO'LLAB-QUVVATLASH (migratsiya):
# Bazada hali ham eski PBKDF2 formatidagi ("<salt>$<digest>") xeshlar
# bo'lishi mumkin (masalan hujjat yozilgan paytda ro'yxatdan o'tgan
# foydalanuvchilar). Xeshni orqaga qaytarib bo'lmaydi (one-way), shuning
# uchun ommaviy/oflayn migratsiya imkonsiz — yagona xavfsiz yo'l: parolni
# faqat foydalanuvchi UNI TERGANDA (ya'ni muvaffaqiyatli login paytida)
# bilamiz. Shu sabab:
#   1. verify_password() ikkala formatni ham (eski PBKDF2 va yangi
#      Argon2) aniqlab, to'g'ri tekshiradi — login uzilib qolmaydi.
#   2. needs_rehash() shu foydalanuvchi hali eski formatda ekanini
#      (yoki Argon2 parametrlari eskirganini) bildiradi.
#   3. modules/auth_module.py'dagi login() muvaffaqiyatli tekshiruvdan
#      so'ng needs_rehash() true bo'lsa, xeshni darhol Argon2'ga qayta
#      yozadi (bu yerda ham, "migrate_passwords.py" faylida ham
#      tushuntirilgan — qarang: migrate_passwords.py).
# ==============================================

_ph = PasswordHasher()  # standart parametrlar OWASP tavsiyasiga mos

_LEGACY_PBKDF2_ITERATIONS = 10_000


def _is_legacy_pbkdf2_hash(password_hash: str) -> bool:
    """Argon2 xeshlari har doim "$argon2id$..." bilan boshlanadi — eski
    PBKDF2 formati esa "<salt_hex>$<digest_hex>" ko'rinishida, prefiksiz."""
    return not password_hash.startswith("$argon2")


def _hash_password_pbkdf2_legacy(raw_password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256", raw_password.encode(), salt.encode(), _LEGACY_PBKDF2_ITERATIONS
    )
    return f"{salt}${digest.hex()}"


def _verify_password_pbkdf2_legacy(raw_password: str, password_hash: str) -> bool:
    try:
        salt, _ = password_hash.split("$", 1)
    except ValueError:
        return False
    test_hash = _hash_password_pbkdf2_legacy(raw_password, salt)
    return hmac.compare_digest(test_hash, password_hash)


def hash_password(raw_password: str) -> str:
    """Yangi parolni Argon2id bilan xeshlash. Har doim shu funksiya
    ishlatiladi — yangi ro'yxatdan o'tish, parol o'zgartirish va login
    paytidagi avtomatik qayta-xeshlashda ham."""
    return _ph.hash(raw_password)


def verify_password(raw_password: str, password_hash: str) -> bool:
    """Parolni saqlangan xesh bilan solishtirish. Ikkala formatni ham
    (eski PBKDF2 va yangi Argon2) qo'llab-quvvatlaydi — migratsiya davrida
    login uzilmasligi uchun."""
    if _is_legacy_pbkdf2_hash(password_hash):
        return _verify_password_pbkdf2_legacy(raw_password, password_hash)
    try:
        return _ph.verify(password_hash, raw_password)
    except (VerifyMismatchError, InvalidHash):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True qaytarsa — bu foydalanuvchi hali eski PBKDF2 formatida
    (yoki Argon2 parametrlari o'zgargan) va keyingi muvaffaqiyatli
    login'da xeshi Argon2'ga (yoki yangi parametrlarga) qayta yozilishi
    kerak."""
    if _is_legacy_pbkdf2_hash(password_hash):
        return True
    try:
        return _ph.check_needs_rehash(password_hash)
    except InvalidHash:
        return True

# ==============================================
# CACHE - TEZKOR FOYDALANUVCHI QIDIRISH
#
# ✅ Bu — BUTUN ilova bo'ylab ishlatiladigan YAGONA user-cache. Avval
# modules/auth_module.py'da mustaqil @lru_cache asosidagi ikkinchi kesh
# ham bor edi (login aynan o'shani ishlatardi), main.py esa faqat SHU
# yerdagi (auth.py) keshni tozalar edi — natijada admin panel "Cache
# tozalash" tugmasi login uchun ishlatiladigan keshga umuman ta'sir
# qilmasdi. Endi login (modules/auth_module.py) ham to'g'ridan-to'g'ri
# shu get_user_cached/clear_user_cache funksiyalaridan foydalanadi.
#
# ⬅️ TUZATILDI (4-band, 4.1): avval bu kesh HAR BIR gunicorn worker
# prosessida MUSTAQIL ravishda xotirada (`_user_cache` dict) yashardi.
# Admin biror foydalanuvchining parolini/rolini o'zgartirganda,
# `clear_user_cache()` faqat o'sha so'rovni QABUL QILGAN workerda
# ishlardi — qolgan workerlarda eski parol xesh/rol CACHE_TTL_SECONDS
# (5 daqiqa)gacha kuchda qolib ketardi (masalan buzilgan parol reset
# qilingandan keyin ham 5 daqiqagacha eski parol bilan kirish mumkin
# bo'lib qolar edi).
#
# YECHIM: agar CLINICFLOW_REDIS_URL sozlangan bo'lsa, kesh Redis'da —
# BARCHA workerlar bo'ylab BITTA, markazlashgan joyda — saqlanadi. Bunda
# alohida pub/sub kerak emas: chunki endi worker-boshiga alohida nusxa
# umuman yo'q, `clear_user_cache()` Redis'dagi yagona kalitni o'chiradi
# va navbatdagi o'qishda BARCHA workerlar buni darhol (bir vaqtning
# o'zida) ko'radi.
#
# Agar CLINICFLOW_REDIS_URL sozlanmagan bo'lsa (masalan bitta-worker
# development muhiti) — avvalgi xatti-harakat (worker-ichida xotira
# keshi) saqlanib qoladi, lekin xavf oynasini qisqartirish uchun
# standart TTL 300s dan 60s ga tushirildi va ishga tushishda ANIQ
# ogohlantirish beriladi (production'da bir nechta worker ishlatilsa,
# bu holat hali ham xavfli — README'ning "Production uchun" bo'limiga
# qarang).
# ==============================================

_REDIS_URL = os.environ.get("CLINICFLOW_REDIS_URL")
_REDIS_CACHE_PREFIX = "clinicflow:user_cache:"
_redis_client = None

if _REDIS_URL:
    try:
        import redis as _redis_module

        _redis_client = _redis_module.from_url(_REDIS_URL, decode_responses=True)
        _redis_client.ping()
        logger.info(
            "✅ Foydalanuvchi keshi Redis (%s) orqali markazlashgan holda ishlayapti — "
            "barcha worker prosesslar bo'ylab umumiy.", _REDIS_URL,
        )
    except Exception:
        logger.exception(
            "❌ CLINICFLOW_REDIS_URL sozlangan (%s), lekin Redis'ga ulanib bo'lmadi — "
            "xotiradagi (in-memory), worker-ichida keshga qaytilmoqda. Bu holatda "
            "ko'p-worker rejimida parol/rol o'zgarishi darhol barcha workerlarga "
            "tarqalmasligi mumkin.", _REDIS_URL,
        )
        _redis_client = None

if _redis_client is None:
    if _REDIS_URL:
        pass  # yuqorida allaqachon xatolik logga yozildi
    else:
        logger.warning(
            "⚠️ CLINICFLOW_REDIS_URL o'rnatilmagan — foydalanuvchi keshi xotirada "
            "(in-memory, faqat shu worker prosessiga tegishli) ishlayapti. "
            "Production'da bir nechta gunicorn worker bilan ishlatilsa, admin "
            "tomonidan qilingan parol/rol o'zgarishi qolgan workerlarga darhol "
            "yetib bormasligi mumkin (xavf oynasi: CACHE_TTL_SECONDS). Xavfni "
            "butunlay yo'q qilish uchun CLINICFLOW_REDIS_URL'ni sozlang "
            "(masalan redis://localhost:6379/0)."
        )

_user_cache: Dict[str, Dict[str, Any]] = {}
_cache_timestamps: Dict[str, float] = {}
# Redis bo'lmaganda xavf oynasini kamaytirish uchun standart TTL 300s dan
# 60s ga tushirildi; env orqali sozlanishi ham mumkin.
CACHE_TTL_SECONDS = int(os.environ.get("CLINICFLOW_USER_CACHE_TTL_SECONDS", "60"))


def get_user_cached(username: str) -> Optional[Dict[str, Any]]:
    """Username bo'yicha userni cache'dan olish (Redis mavjud bo'lsa —
    markazlashgan Redis'dan, aks holda shu worker prosessining xotira
    keshidan)."""
    if _redis_client is not None:
        try:
            raw = _redis_client.get(_REDIS_CACHE_PREFIX + username)
            if raw is not None:
                return json.loads(raw)
        except Exception:
            logger.exception("Redis'dan user-cache o'qishda xatolik — bazadan o'qilmoqda")
    else:
        if username in _user_cache:
            if time.time() - _cache_timestamps.get(username, 0) < CACHE_TTL_SECONDS:
                return _user_cache[username]
            else:
                del _user_cache[username]
                del _cache_timestamps[username]

    # Cache'da yo'q (yoki Redis vaqtincha ishlamayapti) → bazadan olish
    from database import SessionLocal
    db = SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.username == username).first()
        if user:
            user_data = {
                "id": user.id,
                "username": user.username,
                "password_hash": user.password_hash,
                "fullname": user.fullname,
                "role": user.role,
                "doctor_id": user.doctor_id
            }
            if _redis_client is not None:
                try:
                    _redis_client.set(
                        _REDIS_CACHE_PREFIX + username, json.dumps(user_data), ex=CACHE_TTL_SECONDS
                    )
                except Exception:
                    logger.exception("Redis'ga user-cache yozishda xatolik")
            else:
                _user_cache[username] = user_data
                _cache_timestamps[username] = time.time()
            return user_data
        return None
    finally:
        db.close()

def clear_user_cache(username: Optional[str] = None):
    """Cache'ni tozalash. Redis mavjud bo'lsa, bu darhol BARCHA
    workerlarga ta'sir qiladi — chunki ular hammasi shu bitta
    markazlashgan kalit(lar)ni o'qiydi."""
    if _redis_client is not None:
        try:
            if username:
                _redis_client.delete(_REDIS_CACHE_PREFIX + username)
            else:
                for key in _redis_client.scan_iter(match=_REDIS_CACHE_PREFIX + "*"):
                    _redis_client.delete(key)
        except Exception:
            logger.exception("Redis'dan user-cache tozalashda xatolik")
        return
    if username:
        _user_cache.pop(username, None)
        _cache_timestamps.pop(username, None)
    else:
        _user_cache.clear()
        _cache_timestamps.clear()

def get_cache_stats() -> Dict[str, Any]:
    """Cache statistikasi."""
    if _redis_client is not None:
        try:
            keys = list(_redis_client.scan_iter(match=_REDIS_CACHE_PREFIX + "*"))
            return {
                "backend": "redis",
                "total_users": len(keys),
                "users": [k[len(_REDIS_CACHE_PREFIX):] for k in keys],
                "ttl_seconds": CACHE_TTL_SECONDS,
            }
        except Exception:
            logger.exception("Redis'dan cache statistikasini olishda xatolik")
            return {"backend": "redis", "total_users": 0, "users": [], "ttl_seconds": CACHE_TTL_SECONDS}
    return {
        "backend": "in-memory",
        "total_users": len(_user_cache),
        "users": list(_user_cache.keys()),
        "ttl_seconds": CACHE_TTL_SECONDS,
    }

# ==============================================
# SESSION MANAGEMENT
# ==============================================

def _sign(value: str) -> str:
    return hmac.new(SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()

def create_session_token(user_id: int) -> str:
    payload = f"{user_id}:{int(time.time())}"
    return f"{payload}:{_sign(payload)}"

def _read_session_token(token: str) -> Optional[int]:
    parts = token.split(":")
    if len(parts) != 3:
        return None
    user_id_str, issued_at_str, signature = parts
    payload = f"{user_id_str}:{issued_at_str}"
    if not hmac.compare_digest(_sign(payload), signature):
        return None
    if time.time() - float(issued_at_str) > SESSION_MAX_AGE_SECONDS:
        return None
    try:
        return int(user_id_str)
    except ValueError:
        return None

def get_current_user(
    cf_session: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
) -> models.User:
    if not cf_session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Tizimga kirilmagan")
    user_id = _read_session_token(cf_session)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessiya yaroqsiz")
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Foydalanuvchi topilmadi")
    return user

def get_current_user_optional(
    cf_session: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
) -> Optional[models.User]:
    if not cf_session:
        return None
    user_id = _read_session_token(cf_session)
    if user_id is None:
        return None
    return db.query(models.User).filter(models.User.id == user_id).first()

def require_role(*allowed_roles: str) -> Callable[[models.User], models.User]:
    def _dependency(user: models.User = Depends(get_current_user)) -> models.User:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Ruxsat yo'q (kerakli rol: {', '.join(allowed_roles)})",
            )
        return user
    return _dependency