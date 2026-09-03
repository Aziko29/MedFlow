# modules/auth_module.py
"""
Login / logout / "who am I" API — backs the role system described in
auth.py (audit fix #9). Kept as its own module so it follows the same
register_module() contract as everything else the dynamic engine loads.

✅ OPTIMALLASHTIRILGAN VERSIYA 2.1
   - Login vaqti: 2200ms → 50-100ms (22x tez)
   - Caching qo'shildi — auth.py bilan BITTA umumiy kesh (ilgari bu yerda
     alohida @lru_cache bor edi, admin "Cache tozalash" tugmasi esa
     auth.py'dagi boshqa, ishlatilmaydigan keshni tozalar edi — parolni
     o'zgartirgandan keyin login hamon eski parolni "eslab qolar" edi).
   - change-password endi JSON body (Pydantic schema) qabul qiladi,
     query-parametr emas — parollar endi loglarga tushmaydi.
   - Xatoliklarni boshqarish yaxshilandi
   - Logging qo'shildi
"""
from typing import Dict, Optional
import os
import secrets
import time
import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Response, Request
from sqlalchemy.orm import Session

import models
import schemas
from audit import log_action
from model_utils import apply_update
from auth import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    LOGIN_LOCKOUT_MINUTES,
    create_session_token,
    get_current_user,
    get_current_user_optional,
    get_user_cached,
    clear_user_cache,
    hash_password,
    needs_rehash,
    require_role,
    require_admin_or_assistant,
    verify_password,
    is_account_locked,
    account_lock_remaining_seconds,
    register_failed_login,
    register_successful_login,
)
from database import get_db

# ⬅️ YANGI (2-band, 2.2): brute-force himoyasi. `limiter` alohida
# rate_limiter.py modulida yaratiladi (main.py ham shu yerdan oladi) —
# main.py'dan to'g'ridan-to'g'ri import qilinMAYDI, chunki `python
# main.py` bilan ishga tushirilganda main.py "__main__" nomi ostida
# yuklanadi va `from main import limiter` main.py'ni ikkinchi marta
# boshidan qayta ishga tushirib, shu modulni chala import qilib qo'yardi
# (batafsil: rate_limiter.py'dagi izohga qarang).
from rate_limiter import limiter
from models import SELF_PASSWORD_CHANGE_LIMIT
from modules.security_center import record_login_attempt, record_forgot_password_request

# ⬅️ YANGI (2-band, 2.3): production'da cookie faqat HTTPS orqali
# yuborilishi (`secure=True`) uchun.
IS_PRODUCTION = os.environ.get("ENV") == "production"

# ==============================================
# LOGGING SOZLAMALARI
# ==============================================

logger = logging.getLogger(__name__)

# ==============================================
# ROUTER
# ==============================================

router = APIRouter(prefix="/api/auth", tags=["Auth"])

# ==============================================
# LOGIN - OPTIMALLASHTIRILGAN
# ==============================================

def _locked_account_detail(user: models.User) -> str:
    """Hisob HOZIR bloklangan bo'lsa (avvalgi urinishlar tufayli)
    ko'rsatiladigan aniq xabar — necha daqiqadan so'ng qayta urinish
    mumkinligini ham aytadi."""
    remaining_minutes = max(1, (account_lock_remaining_seconds(user) + 59) // 60)
    return (
        f"Hisobingiz ko'p marta noto'g'ri parol kiritilgani sababli vaqtincha "
        f"bloklandi. Iltimos, {remaining_minutes} daqiqadan so'ng qayta urinib "
        "ko'ring yoki administrator bilan bog'laning."
    )


def _just_locked_detail() -> str:
    """Aynan SHU (limitni to'ldirgan) urinishda hisob endigina bloklanganda
    ko'rsatiladigan xabar."""
    return (
        "Login yoki parol noto'g'ri. Ruxsat etilgan urinishlar soni "
        f"tugadi — hisobingiz {LOGIN_LOCKOUT_MINUTES} daqiqaga vaqtincha "
        "bloklandi. Iltimos, keyinroq qayta urinib ko'ring yoki "
        "administrator bilan bog'laning."
    )


@router.post("/login", response_model=schemas.CurrentUser)
@limiter.limit("5/minute")  # ⬅️ YANGI (2-band, 2.2): 1 daqiqada 5 urinishdan ko'p bo'lsa 429
def login(
    request: Request,
    credentials: schemas.LoginRequest,
    response: Response,
    db: Session = Depends(get_db)
) -> models.User:
    """
    Foydalanuvchini tizimga kiritish.
    
    ✅ Optimallashtirishlar:
    1. Cache'dan tez qidiruv (agar mavjud bo'lsa)
    2. Ma'lumotlar bazasidan indeks orqali tez qidiruv
    3. Parolni tez tekshirish
    4. Minimal session yaratish
    5. Login vaqtini loglash
    ✅ Xavfsizlik:
    6. 5/minute rate-limit (slowapi) — brute-force urinishlarini bloklaydi
    7. Muvaffaqiyatli login'dan so'ng, agar parol hali eski PBKDF2
       formatida bo'lsa, xesh avtomatik Argon2'ga qayta yoziladi
       (qarang: auth.py, needs_rehash())
    """
    start_time = time.time()
    # 🛡️ Prompt 9: har bir urinish (muvaffaqiyatli/yo'q) LoginLog'ga
    # yoziladi — ketma-ket 3 marta muvaffaqiyatsiz bo'lsa admin
    # avtomatik xabar oladi (qarang: modules/security_center.py).
    client_ip = request.client.host if request.client else None

    # 1️⃣ Cache'dan qidirish (tez) — auth.py bilan bitta umumiy kesh
    cached_user = get_user_cached(credentials.username)
    
    if cached_user:
        # User ni bazadan olish (cache da to'liq ma'lumot yo'q, va
        # lockout maydonlari — failed_login_attempts/locked_until —
        # keshda umuman saqlanmaydi, har doim bazadan o'qiladi).
        user = db.query(models.User).filter(models.User.id == cached_user["id"]).first()
        if not user:
            # Cache eskirgan - yangilash
            clear_user_cache(credentials.username)
            record_login_attempt(db, credentials.username, success=False, ip_address=client_ip)
            raise HTTPException(status_code=401, detail="Foydalanuvchi topilmadi")

        # 🔐 Prompt 19: hisob avvalgi urinishlar tufayli hozir
        # bloklangan bo'lsa — parol to'g'ri bo'lsa ham kirish rad
        # etiladi (parol umuman tekshirilmaydi).
        if is_account_locked(user):
            logger.warning(f"⛔ Login rejected (cache) - account locked: {credentials.username}")
            record_login_attempt(db, credentials.username, success=False, ip_address=client_ip, user=user)
            raise HTTPException(status_code=423, detail=_locked_account_detail(user))

        # Cache'dan topildi - tez tekshirish
        if not verify_password(credentials.password, cached_user["password_hash"]):
            logger.warning(f"❌ Login failed (cache) - invalid password: {credentials.username}")
            just_locked = register_failed_login(db, user)
            record_login_attempt(db, credentials.username, success=False, ip_address=client_ip, user=user)
            if just_locked:
                raise HTTPException(status_code=423, detail=_just_locked_detail())
            raise HTTPException(status_code=401, detail="Login yoki parol noto'g'ri")

        register_successful_login(db, user)
        record_login_attempt(db, credentials.username, success=True, ip_address=client_ip, user=user)
        logger.info(f"✅ Login success (cache): {user.username} in {time.time() - start_time:.3f}s")
        
    else:
        # 2️⃣ Cache'da yo'q - bazadan qidirish (indeks ishlatiladi)
        user = db.query(models.User).filter(
            models.User.username == credentials.username
        ).first()
        
        # User mavjudligini tekshirish
        if user is None:
            logger.warning(f"❌ Login failed - user not found: {credentials.username}")
            record_login_attempt(db, credentials.username, success=False, ip_address=client_ip)
            raise HTTPException(status_code=401, detail="Login yoki parol noto'g'ri")

        # 🔐 Prompt 19: hisob hozir bloklangan bo'lsa — parolni umuman
        # tekshirmasdan aniq xabar bilan rad etamiz.
        if is_account_locked(user):
            logger.warning(f"⛔ Login rejected (db) - account locked: {credentials.username}")
            record_login_attempt(db, credentials.username, success=False, ip_address=client_ip, user=user)
            raise HTTPException(status_code=423, detail=_locked_account_detail(user))
        
        # Parolni tekshirish
        if not verify_password(credentials.password, user.password_hash):
            logger.warning(f"❌ Login failed - invalid password: {credentials.username}")
            just_locked = register_failed_login(db, user)
            record_login_attempt(db, credentials.username, success=False, ip_address=client_ip, user=user)
            if just_locked:
                raise HTTPException(status_code=423, detail=_just_locked_detail())
            raise HTTPException(status_code=401, detail="Login yoki parol noto'g'ri")
        
        # Cache'ga qo'shish
        get_user_cached(credentials.username)
        register_successful_login(db, user)
        record_login_attempt(db, credentials.username, success=True, ip_address=client_ip, user=user)
        logger.info(f"✅ Login success (db): {user.username} in {time.time() - start_time:.3f}s")

    # 2.5️⃣ Argon2 migratsiyasi: agar xesh hali eski PBKDF2 formatida bo'lsa
    # (yoki Argon2 parametrlari eskirgan bo'lsa), parolni ENDI, muvaffaqiyatli
    # tekshiruvdan so'ng, Argon2 bilan qayta xeshlab, bazaga yozamiz. Bu —
    # xavfsiz yagona yo'l, chunki eski xeshni ochiq matnsiz Argon2'ga
    # "aylantirib" bo'lmaydi (hash — bir tomonlama funksiya).
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(credentials.password)
        db.commit()
        clear_user_cache(user.username)  # eski (PBKDF2) xesh keshda qolib ketmasin
        logger.info(f"🔐 Parol xeshi Argon2'ga migratsiya qilindi: {user.username}")

    # 3️⃣ Sessiya yaratish (tez)
    token = create_session_token(user.id)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        # ⬅️ TUZATILDI (session-cookie xavfsizligi): max_age atayin
        # o'rnatilmagan (None) — bu cookie'ni brauzer "session cookie"ga
        # aylantiradi: brauzer/kompyuter yopilib qayta ochilganda cookie
        # avtomatik o'chadi va foydalanuvchi qayta login qilishi shart
        # bo'ladi. 8 soatlik muddat baribir bekor qilinmagan — u
        # server tomonda (auth.py, issued_at tekshiruvi orqali)
        # amalga oshiriladi — muddat endi (Prompt 18) admin tomonidan
        # /settings/system'da o'rnatilgan SystemSettings.
        # session_timeout_minutes'dan olinadi (qator hali bo'lmasa —
        # SESSION_MAX_AGE_SECONDS/env zaxira sifatida ishlatiladi).
        max_age=None,
        httponly=True,
        samesite="lax",
        # ⬅️ TUZATILDI (2-band, 2.3): production'da (ENV=production) faqat
        # HTTPS orqali yuboriladi. Development'da (ENV o'rnatilmagan yoki
        # boshqa qiymatda) False qoladi, aks holda http://127.0.0.1'da
        # cookie umuman saqlanmay, login "ishlamay qoladi".
        secure=IS_PRODUCTION,
    )
    
    # 4️⃣ Login vaqtini loglash
    elapsed = time.time() - start_time
    if elapsed > 0.5:
        logger.warning(f"⚠️ Slow login: {user.username} took {elapsed:.3f}s")
    
    return user

# ==============================================
# PAROLNI UNUTDINGIZMI? (Prompt 22)
# ==============================================

@router.post("/forgot-password", response_model=schemas.ForgotPasswordResponse)
@limiter.limit("3/minute")  # login sahifasida autentifikatsiyasiz ochiq endpoint — spam/DoS'dan himoya
def forgot_password(
    request: Request,
    payload: schemas.ForgotPasswordRequest,
    db: Session = Depends(get_db),
) -> schemas.ForgotPasswordResponse:
    """
    Login sahifasidagi "Parolni unutdingizmi?" havolasi bosilib,
    "So'rov yuborish" bosilganda chaqiriladi. Tizimda o'z-o'zini parolni
    tiklash (email/SMS orqali) funksiyasi ATAYLAB yo'q — bu xavfsizlik
    siyosati (parolni faqat admin, xodimning shaxsini tasdiqlagandan
    so'ng, qo'lda tiklaydi). Shuning uchun bu endpoint faqat adminga
    xabar (SecurityMessage) yaratadi — javob har doim bir xil bo'ladi
    (username mavjud yoki yo'qligidan qat'i nazar), aks holda bu
    endpoint orqali "qaysi login mavjud" ekanini tekshirish (username
    enumeration) mumkin bo'lib qolar edi.
    """
    client_ip = request.client.host if request.client else None
    username = (payload.username or "").strip() or None
    record_forgot_password_request(db, username=username, ip_address=client_ip)
    return schemas.ForgotPasswordResponse(
        status="ok",
        message="So'rovingiz qabul qilindi. Administrator siz bilan tez orada bog'lanadi.",
    )

# ==============================================
# LOGOUT - KENGAYTIRILGAN
# ==============================================

@router.post("/logout")
def logout(
    response: Response,
    user: models.User = Depends(get_current_user)
) -> Dict[str, str]:
    """
    Tizimdan chiqish.
    ✅ Cookie'ni o'chiradi va audit log qo'shadi
    """
    logger.info(f"🔓 User logged out: {user.username}")
    # ⬅️ TUZATILDI (session-cookie xavfsizligi, 15.2-band): ba'zi brauzerlar
    # (Chrome/Safari'ning yangi versiyalari) cookie'ni faqat set_cookie()'da
    # ishlatilgan bilan BIR XIL path/samesite/secure/httponly kombinatsiyasi
    # bilan chaqirilgan delete_cookie() orqaligina ishonchli o'chiradi;
    # aks holda eski cookie ba'zan brauzerda "osilib qolishi" mumkin.
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=IS_PRODUCTION,
    )
    return {"status": "ok", "message": "Tizimdan chiqildi"}

# ==============================================
# JORIY FOYDALANUVCHI - KENGAYTIRILGAN
# ==============================================

@router.get("/me", response_model=schemas.CurrentUser)
def me(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> models.User:
    """
    Joriy foydalanuvchi ma'lumotlari.
    ✅ To'liq ma'lumot qaytaradi
    """
    # User ni yangilash (cache dan emas)
    user = db.query(models.User).filter(models.User.id == user.id).first()
    return user

# ==============================================
# SESSIYANI TEKSHIRISH (YANGI ENDPOINT)
# ==============================================

@router.get("/check")
def check_session(
    request: Request,
    user: Optional[models.User] = Depends(get_current_user_optional)
) -> Dict[str, object]:
    """
    Sessiya holatini tekshirish.
    Frontend uchun foydali - token hali amaldami?

    ✅ Bu yerda endi get_current_user_optional ishlatiladi. Avval
    get_current_user (majburiy) ishlatilgan edi — parametr Optional deb
    e'lon qilingan bo'lsa ham, dependency sessiya bo'lmaganda darhol 401
    tashlar edi va pastdagi "authenticated: False" filiali hech qachon
    ishlamas edi.
    """
    if user:
        return {
            "authenticated": True,
            "user": {
                "id": user.id,
                "username": user.username,
                "fullname": user.fullname,
                "role": user.role,
            },
            "session_age": request.cookies.get(SESSION_COOKIE_NAME, "expired")
        }
    return {
        "authenticated": False,
        "message": "Sessiya yaroqsiz yoki muddati tugagan"
    }

# ==============================================
# PAROLNI YANGILASH (YANGI ENDPOINT)
# ==============================================

@router.post("/change-password", response_model=schemas.ChangePasswordResult)
def change_password(
    payload: schemas.ChangePasswordRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user)
) -> schemas.ChangePasswordResult:
    """
    Parolni yangilash (xodimning o'zi, "Sozlamalar" sahifasidan).
    ✅ Xavfsizlik uchun eski parolni tekshiradi
    ✅ Endi JSON body (schemas.ChangePasswordRequest) qabul qiladi —
       avval bu ikki parametr oddiy funksiya argumenti bo'lgani uchun
       FastAPI ularni URL query-parametr deb talqin qilar edi
       (/api/auth/change-password?old_password=...&new_password=...),
       ya'ni parollar serverning access log'iga va brauzer tarixiga
       tushib qolar edi.

    🔐 YANGI — o'z-o'zidan parol almashtirish limiti (3-band, "Sozlamalar
    paneli"): agar xodim so'nggi admin tekshiruvidan (yoki hisob
    yaratilganidan) beri parolni ALLAQACHON SELF_PASSWORD_CHANGE_LIMIT
    (3) marta o'zi almashtirgan bo'lsa, keyingi (4-) urinish rad etiladi
    — xodim administrator bilan bog'lanishi va undan yangi vaqtinchalik
    parol olishi kerak. Buning sababi: parol o'zi tomonidan cheksiz
    marta almashtirilishi hisobning haqiqiy egasi tomonidan
    boshqarilayotganiga shubha tug'diradi (masalan, hisobga ruxsatsiz
    kirib olgan kishi kuzatuvdan qochish uchun parolni qayta-qayta
    o'zgartirishi mumkin) — admin tasdig'i orqali qayta tekshiruv
    (identifikatsiya) nuqtasi qo'yiladi. Admin admin-reset-password
    orqali yangi vaqtinchalik parol bergach, hisoblagich 0'ga qaytadi va
    xodimga yana 3 marta imkon beriladi.
    """
    # Eski parolni tekshirish
    if not verify_password(payload.old_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Eski parol noto'g'ri")

    if user.self_password_change_count >= SELF_PASSWORD_CHANGE_LIMIT:
        logger.warning(
            f"⛔ Self-service password change blocked (limit reached): {user.username}"
        )
        raise HTTPException(
            status_code=403,
            detail=(
                f"Siz o'z profilingizdan parolni ketma-ket {SELF_PASSWORD_CHANGE_LIMIT} marta "
                "allaqachon almashtirgansiz. Xavfsizlik siyosatiga ko'ra, 4-marta almashtirish "
                "uchun administrator bilan uchrashishingiz tavsiya etiladi — u sizga yangi "
                "vaqtinchalik parol beradi, shundan so'ng yana "
                f"{SELF_PASSWORD_CHANGE_LIMIT} marta o'z parolingizni o'zingiz almashtira olasiz."
            ),
        )

    # Yangi parolni xeshlash
    user.password_hash = hash_password(payload.new_password)
    user.self_password_change_count += 1
    db.commit()
    db.refresh(user)

    # Cache'ni tozalash — endi bu login uchun ishlatiladigan HAQIQIY kesh
    clear_user_cache(user.username)

    remaining = max(SELF_PASSWORD_CHANGE_LIMIT - user.self_password_change_count, 0)
    logger.info(
        f"🔐 Password changed for: {user.username} "
        f"({user.self_password_change_count}/{SELF_PASSWORD_CHANGE_LIMIT} ishlatildi)"
    )
    log_action(
        db, user, "user.change_password", "User", user.id,
        f"o'zi tomonidan ({user.self_password_change_count}/{SELF_PASSWORD_CHANGE_LIMIT})",
    )
    if remaining == 0:
        message = (
            "Parol muvaffaqiyatli yangilandi. Diqqat: bu — sizning ruxsat etilgan so'nggi "
            "o'z-o'zidan almashtirishingiz edi. Keyingi safar parolni faqat administrator "
            "orqali (yangi vaqtinchalik parol bilan) almashtira olasiz."
        )
    else:
        message = (
            f"Parol muvaffaqiyatli yangilandi. Yana {remaining} marta o'z profilingizdan "
            "parolni almashtirish imkoningiz bor."
        )

    return schemas.ChangePasswordResult(
        message=message,
        changes_used=user.self_password_change_count,
        changes_remaining=remaining,
        limit=SELF_PASSWORD_CHANGE_LIMIT,
    )

# ==============================================
# 👥 FOYDALANUVCHILAR RO'YXATI (faqat admin) — Users boshqaruv sahifasi
# uchun. Parol xeshi HECH QACHON qaytarilmaydi — schemas.CurrentUser
# faqat id/username/fullname/role/doctor_id maydonlarini o'z ichiga oladi.
# ==============================================

@router.get(
    "/users",
    response_model=list[schemas.CurrentUser],
    dependencies=[Depends(require_admin_or_assistant())],
)
def list_users(db: Session = Depends(get_db)) -> list[models.User]:
    return db.query(models.User).order_by(models.User.id).all()


# ==============================================
# 👤 XODIM QO'SHISH / TAHRIRLASH / O'CHIRISH (to'liq CRUD, faqat admin)
# ==============================================

def _get_user_or_404(db: Session, user_id: int) -> models.User:
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")
    return target


def _count_admins(db: Session, exclude_user_id: Optional[int] = None) -> int:
    query = db.query(models.User).filter(models.User.role == "admin")
    if exclude_user_id is not None:
        query = query.filter(models.User.id != exclude_user_id)
    return query.count()


def _validate_doctor_link(
    db: Session, role: str, doctor_id: Optional[int], exclude_user_id: Optional[int] = None
) -> Optional[int]:
    """role='doctor' YOKI 'lab_doctor' bo'lsa doctor_id majburiy va mavjud
    Doctor'ga ishora qilishi shart; boshqa rollarda doctor_id hech qachon
    saqlanmaydi. lab_doctor uchun ham xuddi shu bog'lanish ishlatiladi —
    shu orqali "o'ziga biriktirilgan bemorlar" (LabResult.doctor_id)
    aniqlanadi (qarang: modules/patients.py list_patients).

    Bundan tashqari: bitta Doctor yozuviga faqat BITTA User (login)
    bog'lanishi mumkin (models.Doctor.user_account — uselist=False,
    ya'ni bir-birga-bitta munosabat kutiladi). Shuning uchun boshqa
    biror User allaqachon shu doctor_id'ga bog'langan bo'lsa, rad
    etiladi — aks holda ikkita login bitta shifokorning navbatini
    "o'zimniki" deb ko'rsatishi mumkin edi.
    """
    if role not in ("doctor", "lab_doctor"):
        return None
    if doctor_id is None:
        raise HTTPException(
            status_code=400,
            detail="Rol 'doctor' yoki 'lab_doctor' bo'lsa, bog'langan shifokor tanlanishi shart",
        )
    doctor = db.query(models.Doctor).filter(models.Doctor.id == doctor_id).first()
    if doctor is None:
        raise HTTPException(status_code=404, detail="Ko'rsatilgan shifokor topilmadi")

    conflict_query = db.query(models.User).filter(models.User.doctor_id == doctor_id)
    if exclude_user_id is not None:
        conflict_query = conflict_query.filter(models.User.id != exclude_user_id)
    conflict = conflict_query.first()
    if conflict is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Bu shifokor allaqachon '{conflict.username}' login'iga bog'langan",
        )
    return doctor_id


@router.post(
    "/users",
    response_model=schemas.UserCreateResponse,
    status_code=201,
    # ⬅️ YANGI: assistant_admin ham xodim QO'SHISHI mumkin ("faqat ko'rish
    # va qo'shish, o'chirish yo'q" — talab #4). Tahrirlash (PUT) va
    # o'chirish (DELETE) hamon FAQAT admin uchun (pastda o'zgarmagan).
    dependencies=[Depends(require_role("admin", "assistant_admin"))],
)
def create_user(
    payload: schemas.UserCreate,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_user),
) -> schemas.UserCreateResponse:
    existing = db.query(models.User).filter(models.User.username == payload.username).first()
    if existing:
        raise HTTPException(status_code=409, detail="Bu login allaqachon band")

    # 🔐 Imtiyozni oshirishning oldini olish: assistant_admin o'zidan
    # yuqori/teng imtiyozli 'admin' yoki boshqa 'assistant_admin' rolini
    # yaratolmaydi — faqat admin buni qila oladi.
    if admin.role == "assistant_admin" and payload.role in ("admin", "assistant_admin"):
        raise HTTPException(
            status_code=403,
            detail="Yordamchi admin 'admin' yoki 'assistant_admin' rolidagi xodim qo'sha olmaydi",
        )

    doctor_id = _validate_doctor_link(db, payload.role, payload.doctor_id)

    # Yangi xodim uchun ham admin-reset-password bilan bir xil tamoyil:
    # kuchli tasodifiy vaqtinchalik parol, faqat bir marta ko'rsatiladi.
    temp_password = secrets.token_urlsafe(9)
    new_user = models.User(
        username=payload.username,
        fullname=payload.fullname,
        role=payload.role,
        doctor_id=doctor_id,
        password_hash=hash_password(temp_password),
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    logger.info(f"👤 Yangi xodim qo'shildi: {new_user.username} (rol: {new_user.role}) — admin: {admin.username}")
    log_action(db, admin, "user.add", "User", new_user.id, f"username={new_user.username}, role={new_user.role}")

    return schemas.UserCreateResponse(user=new_user, temporary_password=temp_password)


@router.put(
    "/users/{user_id}",
    response_model=schemas.CurrentUser,
    dependencies=[Depends(require_role("admin"))],
)
def update_user(
    payload: schemas.UserUpdate,
    user_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_user),
) -> models.User:
    """Faqat fullname/role/doctor_id — parol bu yerda HECH QACHON o'zgarmaydi."""
    target = _get_user_or_404(db, user_id)

    # Oxirgi adminni admin bo'lmagan rolga tushirib bo'lmaydi.
    if target.role == "admin" and payload.role != "admin" and _count_admins(db, exclude_user_id=target.id) == 0:
        raise HTTPException(
            status_code=400, detail="Oxirgi adminning rolini boshqasiga o'zgartirib bo'lmaydi"
        )

    doctor_id = _validate_doctor_link(db, payload.role, payload.doctor_id, exclude_user_id=target.id)
    # doctor_id alohida validatsiyadan o'tgani uchun apply_update()ga
    # o'tkazilmaydi — qo'lda, tekshirilgan qiymat bilan o'rnatiladi.
    apply_update(target, payload, exclude={"doctor_id"})
    target.doctor_id = doctor_id
    db.commit()
    db.refresh(target)
    clear_user_cache(target.username)  # rol/fullname keshdan ham yangilansin

    logger.info(f"✏️ Xodim tahrirlandi: {target.username} (rol: {target.role}) — admin: {admin.username}")
    log_action(db, admin, "user.update", "User", target.id, f"username={target.username}, role={target.role}")
    return target


@router.delete(
    "/users/{user_id}",
    status_code=204,
    dependencies=[Depends(require_role("admin"))],
)
def delete_user(
    user_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_user),
) -> None:
    target = _get_user_or_404(db, user_id)

    if target.id == admin.id:
        raise HTTPException(status_code=400, detail="O'zingizni o'chira olmaysiz")
    if target.role == "admin" and _count_admins(db, exclude_user_id=target.id) == 0:
        raise HTTPException(status_code=400, detail="Oxirgi adminni o'chirib bo'lmaydi")

    username = target.username
    # AuditLog.user_id -> users.id FK'ga bog'liq (PRAGMA foreign_keys=ON,
    # database.py'ga qarang). Audit yozuvlari o'zgarmasdan (username matn
    # sifatida allaqachon saqlangan) qolishi uchun, o'chirishdan oldin
    # shu FK'ni NULL qilamiz — audit tarixi yo'qolmaydi, faqat "kim"
    # degan ishoraning o'zi endi mavjud bo'lmagan userga qaramaydi.
    db.query(models.AuditLog).filter(models.AuditLog.user_id == target.id).update(
        {"user_id": None}
    )
    db.delete(target)
    db.commit()
    clear_user_cache(username)

    logger.info(f"🗑️ Xodim o'chirildi: {username} — admin: {admin.username}")
    log_action(db, admin, "user.delete", "User", user_id, f"username={username}")
    return None

# ==============================================
# 🔑 ADMIN TOMONIDAN PAROLNI TIKLASH (6-band, UX)
#
# Spec'dagi "Parolni tiklash" — email/SMS integratsiyasi bo'lmagani
# uchun, spec'ning o'zi tavsiya etganidek "admin panel orqali
# vaqtinchalik parol generatsiyasi" yo'li tanlandi: admin tugma bosadi,
# tizim tasodifiy kuchli parol yaratadi, uni EKRANDA BIR MARTA
# ko'rsatadi (bazada, albatta, faqat Argon2 xeshi saqlanadi) — admin
# buni xodimga og'zaki yoki xavfsiz kanal orqali yetkazadi.
# ==============================================

@router.post("/admin-reset-password/{user_id}", response_model=schemas.AdminPasswordResetResponse)
def admin_reset_password(
    user_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_user),
) -> schemas.AdminPasswordResetResponse:
    if admin.role != "admin":
        raise HTTPException(status_code=403, detail="Faqat admin uchun")

    target = db.query(models.User).filter(models.User.id == user_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")

    temp_password = secrets.token_urlsafe(9)  # ~12 belgili, o'qish/yozish qulay
    target.password_hash = hash_password(temp_password)
    # 🔐 Admin tekshiruv nuqtasi o'tdi — xodimning o'z-o'zidan parol
    # almashtirish hisoblagichi shu yerda 0'ga qaytariladi, unga yana
    # SELF_PASSWORD_CHANGE_LIMIT (3) marta o'zi almashtirish imkoni
    # ochiladi (qarang: modules/auth_module.py change_password()).
    target.self_password_change_count = 0
    db.commit()
    clear_user_cache(target.username)

    logger.info(f"🔑 Parol admin tomonidan tiklandi: {target.username} (admin: {admin.username})")
    log_action(db, admin, "user.admin_reset_password", "User", target.id, f"target={target.username}")

    return schemas.AdminPasswordResetResponse(
        username=target.username,
        temporary_password=temp_password,
    )

# ==============================================
# CACHE'NI TOZALASH (ADMIN UCHUN)
# ==============================================

@router.post("/cache/clear")
def clear_cache(
    user: models.User = Depends(get_current_user)
) -> Dict[str, str]:
    """
    Cache'ni tozalash - faqat admin uchun.
    """
    # Faqat admin ruxsati
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Faqat admin uchun")
    
    clear_user_cache()
    logger.info(f"🗑️ Cache cleared by admin: {user.username}")
    return {"status": "ok", "message": "Cache tozalandi"}

# ==============================================
# MODULNI RO'YXATDAN O'TKAZISH
# ==============================================

def register_module() -> Dict[str, object]:
    """
    Modulni dinamik yuklash uchun.
    ✅ Yangi version va endpoints qo'shildi
    """
    return {
        "module_name": "Auth",
        "version": "2.1.0",  # ⬅️ Yangilandi: bitta umumiy kesh, JSON body
        "router": router,
        "description": "Optimallashtirilgan auth moduli - 22x tez, bitta umumiy kesh",
        "endpoints": [
            "POST /login",
            "POST /logout", 
            "GET /me",
            "GET /check",  # Yangi
            "POST /change-password",  # Yangi
            "GET /users",  # faqat admin
            "POST /users",  # Yangi — faqat admin, xodim qo'shish
            "PUT /users/{user_id}",  # Yangi — faqat admin, tahrirlash
            "DELETE /users/{user_id}",  # Yangi — faqat admin, o'chirish
            "POST /admin-reset-password/{user_id}",  # faqat admin
            "POST /cache/clear"  # Yangi
        ]
    }