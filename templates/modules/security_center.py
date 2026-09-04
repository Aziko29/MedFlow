# modules/security_center.py
"""
🛡️ Xavfsizlik markazi (Prompt 9) — uchta mustaqil, lekin bir-biriga
bog'liq qism:

  1. Shifokor xabarnoma tizimi — doctor/lab_doctor adminga savol/xabar
     yuboradi (SecurityMessage). Faqat admin ko'radi, o'qilgan deb
     belgilaydi va o'chiradi (assistant_admin FAQAT ko'radi — audit_log
     bilan bir xil "read-only" naqsh, qarang: main.py ROLE_MODULE_ACCESS).
  2. Tizim xatoliklari monitoringi — har qanday 500 xatolik va har
     qanday ruxsatsiz kirish urinishi (401/403) main.py'dagi global
     handlerlar orqali AVTOMATIK shu yerga (SystemError) yoziladi.
     record_system_error() shuning uchun HTTP orqali emas, to'g'ridan-
     to'g'ri Python funksiyasi sifatida chaqiriladi (main.py, boshqa
     modullar) — POST /security/system-errors endpoint esa faqat
     frontend (brauzer JS xatolik ushlagichi) kabi tashqi chaqiruvchilar
     uchun mo'ljallangan, u ham xuddi shu funksiyani ishlatadi.
  3. Kirish loglari — har bir login urinishi (muvaffaqiyatli/
     muvaffaqiyatsiz) modules/auth_module.py login() orqali
     record_login_attempt() bilan yoziladi. Bitta username uchun ketma-
     ket 3 (keyin 6, 9, ...) marta muvaffaqiyatsiz urinish aniqlansa,
     adminga avtomatik SecurityMessage (priority="high") yaratiladi —
     xabar shifokor xabarlari bilan BIR XIL jadvalda (SecurityMessage),
     faqat from_user_id=None ("tizim" yuborgan) bilan ajratiladi.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from sqlalchemy.orm import Session

import models
import schemas
from audit import log_action
from auth import get_current_user, require_admin_or_assistant, require_role
from database import SessionLocal, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/security", tags=["Security Center"])

# Ketma-ket shuncha marta muvaffaqiyatsiz login urinishi bo'lsa,
# adminga avtomatik xabarnoma yuboriladi (keyin 6, 9, ... da yana).
FAILED_LOGIN_ALERT_THRESHOLD = 3


# ==============================================
# YORDAMCHI (INTERNAL) FUNKSIYALAR — boshqa modullar (main.py,
# modules/auth_module.py) shu yerdan to'g'ridan-to'g'ri import qilib
# chaqiradi, HTTP orqali emas.
# ==============================================


def _serialize_message(msg: models.SecurityMessage) -> schemas.SecurityMessageOut:
    return schemas.SecurityMessageOut(
        id=msg.id,
        from_user_id=msg.from_user_id,
        from_user_name=msg.from_user.fullname if msg.from_user else None,
        subject=msg.subject,
        message=msg.message,
        priority=msg.priority,
        is_read=msg.is_read,
        created_at=msg.created_at,
    )


def _serialize_error(err: models.SystemError) -> schemas.SystemErrorOut:
    return schemas.SystemErrorOut(
        id=err.id,
        user_id=err.user_id,
        username=err.user.username if err.user else None,
        endpoint=err.endpoint,
        error_message=err.error_message,
        traceback=err.traceback,
        created_at=err.created_at,
    )


def _serialize_login_log(entry: models.LoginLog) -> schemas.LoginLogOut:
    return schemas.LoginLogOut(
        id=entry.id,
        user_id=entry.user_id,
        username=entry.username,
        success=entry.success,
        ip_address=entry.ip_address,
        created_at=entry.created_at,
    )


def record_system_error(
    db: Session,
    endpoint: str,
    error_message: str,
    traceback_str: Optional[str] = None,
    user: Optional[models.User] = None,
) -> models.SystemError:
    """Bitta SystemError qatorini yozadi va DARHOL commit qiladi (audit.py
    log_action() bilan bir xil tamoyil — xatolikni qayd etish, asosiy
    so'rov muvaffaqiyatli/muvaffaqiyatsiz bo'lishidan mustaqil bo'lishi
    kerak). Chaqiruvchi tomon (main.py) o'z db seansi ishonchsiz holatda
    bo'lishi mumkin bo'lgan joylarda (masalan global 500-handler ichida)
    buning o'rniga alohida, yangi SessionLocal() ishlatishi kerak —
    qarang: record_system_error_isolated().
    """
    entry = models.SystemError(
        user_id=user.id if user else None,
        endpoint=endpoint,
        error_message=error_message,
        traceback=traceback_str,
    )
    db.add(entry)
    db.commit()
    return entry


def record_system_error_isolated(
    endpoint: str,
    error_message: str,
    traceback_str: Optional[str] = None,
    user_id: Optional[int] = None,
) -> None:
    """record_system_error()ga PARALLEL — lekin FastAPI Depends(get_db)
    dan tashqarida (masalan global `Exception` handler ichida, so'rovning
    o'z DB seansi allaqachon buzilgan/rollback holatida bo'lishi mumkin
    bo'lgan joyda) xavfsiz chaqirish uchun. O'zining alohida, qisqa
    umrli SessionLocal() seansini ochadi va yopadi."""
    isolated_db = SessionLocal()
    try:
        entry = models.SystemError(
            user_id=user_id,
            endpoint=endpoint,
            error_message=error_message,
            traceback=traceback_str,
        )
        isolated_db.add(entry)
        isolated_db.commit()
    except Exception:  # nosemgrep — xatolikni qayd etish o'zi qulab tushmasligi shart
        logger.exception("SystemError yozib bo'lmadi (isolated)")
        isolated_db.rollback()
    finally:
        isolated_db.close()


def _consecutive_failed_logins(db: Session, username: str) -> int:
    """Berilgan username uchun eng so'nggi urinishlardan boshlab, ketma-ket
    nechta muvaffaqiyatsiz (success=False) urinish borligini sanaydi
    (birinchi muvaffaqiyatli urinishga yoki tarix tugashiga yetguncha)."""
    recent = (
        db.query(models.LoginLog)
        .filter(models.LoginLog.username == username)
        .order_by(models.LoginLog.created_at.desc())
        .limit(50)
        .all()
    )
    streak = 0
    for entry in recent:
        if not entry.success:
            streak += 1
        else:
            break
    return streak


def record_login_attempt(
    db: Session,
    username: str,
    success: bool,
    ip_address: Optional[str] = None,
    user: Optional[models.User] = None,
) -> models.LoginLog:
    """Har bir login urinishini (muvaffaqiyatli yoki yo'q) LoginLog'ga
    yozadi. Muvaffaqiyatsiz urinish ketma-ket FAILED_LOGIN_ALERT_THRESHOLD
    (3, keyin 6, 9, ...) ga yetsa, admin(lar)ga avtomatik SecurityMessage
    (priority="high") yaratadi — qarang: modul docstring'i."""
    entry = models.LoginLog(
        user_id=user.id if user else None,
        username=username,
        success=success,
        ip_address=ip_address,
    )
    db.add(entry)
    db.commit()

    if not success:
        streak = _consecutive_failed_logins(db, username)
        if streak >= FAILED_LOGIN_ALERT_THRESHOLD and streak % FAILED_LOGIN_ALERT_THRESHOLD == 0:
            alert = models.SecurityMessage(
                from_user_id=None,  # tizim tomonidan avtomatik yaratilgan
                subject=f"⚠️ Shubhali login urinishlari: '{username}'",
                message=(
                    f"'{username}' login uchun ketma-ket {streak} marta muvaffaqiyatsiz "
                    f"kirish urinishi qayd etildi (so'nggi urinish IP: {ip_address or 'nomalum'}). "
                    "Agar bu xodimning o'zi bo'lmasa, hisobni vaqtincha bloklash yoki "
                    "parolni admin orqali tiklashni ko'rib chiqing."
                ),
                priority="high",
            )
            db.add(alert)
            db.commit()
            logger.warning(
                f"🚨 {streak} marta ketma-ket muvaffaqiyatsiz login: '{username}' — admin ogohlantirildi"
            )

    return entry


def record_forgot_password_request(
    db: Session,
    username: Optional[str],
    ip_address: Optional[str] = None,
) -> models.SecurityMessage:
    """Login sahifasidagi "Parolni unutdingizmi?" havolasi bosilib,
    so'rov yuborilganda chaqiriladi (Prompt 22). Foydalanuvchi hali
    tizimga kirmagani uchun bu FAQAT admin(lar)ga ko'rinadigan tizim
    xabari — parolni haqiqatda tiklamaydi, faqat adminni xabardor
    qiladi (u keyin "Vaqtinchalik parol" funksiyasi orqali qo'lda
    tiklaydi, qarang: POST /api/auth/admin-reset-password/{user_id}).
    from_user_id=None — muvaffaqiyatsiz login ogohlantirishlari bilan
    bir xil naqsh ("tizim" yuborgan xabar)."""
    who = f"'{username}'" if username else "(login kiritilmagan)"
    alert = models.SecurityMessage(
        from_user_id=None,
        subject=f"🔑 Parolni tiklash so'rovi: {who}",
        message=(
            f"Login sahifasida foydalanuvchi {who} parolni unutganini bildirdi "
            f"(IP: {ip_address or 'nomalum'}). Agar bu haqiqiy xodim bo'lsa, "
            "uning shaxsini tasdiqlagandan so'ng \"Foydalanuvchilar\" bo'limidan "
            "vaqtinchalik parol tiklab bering."
        ),
        priority="medium",
    )
    db.add(alert)
    db.commit()
    logger.info(f"🔑 Parolni tiklash so'rovi qayd etildi: {who} (IP: {ip_address or 'nomalum'})")
    return alert


def record_unauthorized_access(
    db: Session,
    endpoint: str,
    detail: str,
    status_code: int,
    user: Optional[models.User] = None,
) -> None:
    """401/403 javoblarini SystemError sifatida qayd etadi (traceback
    yo'q — bu dastur xatosi emas, huquq tekshiruvi natijasi)."""
    who = f"user_id={user.id} ({user.username})" if user else "anonim"
    record_system_error(
        db,
        endpoint=endpoint,
        error_message=f"{status_code} Ruxsatsiz kirish urinishi ({who}): {detail}",
        traceback_str=None,
        user=user,
    )


# ==============================================
# 📨 SHIFOKOR XABARNOMA TIZIMI
# ==============================================


@router.post("/messages", response_model=schemas.SecurityMessageOut, status_code=201)
def send_security_message(
    payload: schemas.SecurityMessageCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_role("doctor", "lab_doctor")),
) -> schemas.SecurityMessageOut:
    """Shifokor (yoki lab shifokori) adminga savol/xabar yuboradi."""
    msg = models.SecurityMessage(
        from_user_id=user.id,
        subject=payload.subject,
        message=payload.message,
        priority=payload.priority,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    logger.info(f"📨 Yangi xavfsizlik xabari: '{msg.subject}' — {user.username} ({payload.priority})")
    return _serialize_message(msg)


@router.get("/messages", response_model=List[schemas.SecurityMessageOut])
def list_security_messages(
    db: Session = Depends(get_db),
    _user: models.User = Depends(require_admin_or_assistant()),
) -> List[schemas.SecurityMessageOut]:
    """Barcha xabarlar, eng yangisidan boshlab (admin + assistant_admin — FAQAT o'qish)."""
    messages = db.query(models.SecurityMessage).order_by(models.SecurityMessage.created_at.desc()).all()
    return [_serialize_message(m) for m in messages]


@router.get("/messages/unread", response_model=List[schemas.SecurityMessageOut])
def list_unread_security_messages(
    db: Session = Depends(get_db),
    _user: models.User = Depends(require_admin_or_assistant()),
) -> List[schemas.SecurityMessageOut]:
    """Faqat o'qilmagan xabarlar — dashboard badge shu endpoint (yoki
    /summary'dagi unread_security_messages) asosida hisoblanadi."""
    messages = (
        db.query(models.SecurityMessage)
        .filter(models.SecurityMessage.is_read.is_(False))
        .order_by(models.SecurityMessage.created_at.desc())
        .all()
    )
    return [_serialize_message(m) for m in messages]


@router.patch("/messages/{message_id}/read", response_model=schemas.SecurityMessageOut)
def mark_message_read(
    message_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_role("admin")),
) -> schemas.SecurityMessageOut:
    """O'qilgan deb belgilash — yozish (state o'zgarishi) amali bo'lgani
    uchun, audit_log'dagi kabi FAQAT admin (assistant_admin emas)."""
    msg = db.query(models.SecurityMessage).filter(models.SecurityMessage.id == message_id).first()
    if msg is None:
        raise HTTPException(status_code=404, detail="Xabar topilmadi")
    if not msg.is_read:
        msg.is_read = True
        db.commit()
        db.refresh(msg)
        log_action(db, admin, "security_message.read", "SecurityMessage", msg.id, f"subject={msg.subject}")
    return _serialize_message(msg)


@router.delete("/messages/{message_id}", status_code=204)
def delete_security_message(
    message_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_role("admin")),
) -> None:
    """Xabarni o'chirish — faqat admin."""
    msg = db.query(models.SecurityMessage).filter(models.SecurityMessage.id == message_id).first()
    if msg is None:
        raise HTTPException(status_code=404, detail="Xabar topilmadi")
    subject = msg.subject
    db.delete(msg)
    db.commit()
    logger.info(f"🗑️ Xavfsizlik xabari o'chirildi: '{subject}' — admin: {admin.username}")
    log_action(db, admin, "security_message.delete", "SecurityMessage", message_id, f"subject={subject}")
    return None


# ==============================================
# 🛑 TIZIM XATOLIKLARI MONITORINGI
# ==============================================


@router.post("/system-errors", response_model=schemas.SystemErrorOut, status_code=201)
def report_system_error(
    payload: schemas.SystemErrorCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> schemas.SystemErrorOut:
    """Xatolikni qo'lda/tashqaridan (masalan brauzer JS xatolik
    ushlagichi) qayd etish uchun. 500 xatoliklar buni chaqirmaydi —
    ular main.py global exception handler orqali to'g'ridan-to'g'ri
    record_system_error_isolated() bilan yoziladi (login talab
    qilinmaydigan holatlarda ham ishlashi kerak)."""
    err = record_system_error(
        db,
        endpoint=payload.endpoint,
        error_message=payload.error_message,
        traceback_str=payload.traceback,
        user=user,
    )
    return _serialize_error(err)


@router.get("/system-errors", response_model=List[schemas.SystemErrorOut])
def list_system_errors(
    db: Session = Depends(get_db),
    _user: models.User = Depends(require_admin_or_assistant()),
) -> List[schemas.SystemErrorOut]:
    errors = db.query(models.SystemError).order_by(models.SystemError.created_at.desc()).limit(500).all()
    return [_serialize_error(e) for e in errors]


@router.get("/system-errors/recent", response_model=List[schemas.SystemErrorOut])
def list_recent_system_errors(
    db: Session = Depends(get_db),
    _user: models.User = Depends(require_admin_or_assistant()),
) -> List[schemas.SystemErrorOut]:
    """Oxirgi 24 soatdagi xatoliklar."""
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)
    errors = (
        db.query(models.SystemError)
        .filter(models.SystemError.created_at >= since)
        .order_by(models.SystemError.created_at.desc())
        .all()
    )
    return [_serialize_error(e) for e in errors]


# ==============================================
# 🔑 KIRISH LOGLARI
# ==============================================


@router.get("/login-logs", response_model=List[schemas.LoginLogOut])
def list_login_logs(
    db: Session = Depends(get_db),
    _user: models.User = Depends(require_admin_or_assistant()),
) -> List[schemas.LoginLogOut]:
    logs = db.query(models.LoginLog).order_by(models.LoginLog.created_at.desc()).limit(500).all()
    return [_serialize_login_log(l) for l in logs]


@router.get("/login-logs/failed", response_model=List[schemas.LoginLogOut])
def list_failed_login_logs(
    db: Session = Depends(get_db),
    _user: models.User = Depends(require_admin_or_assistant()),
) -> List[schemas.LoginLogOut]:
    logs = (
        db.query(models.LoginLog)
        .filter(models.LoginLog.success.is_(False))
        .order_by(models.LoginLog.created_at.desc())
        .limit(500)
        .all()
    )
    return [_serialize_login_log(l) for l in logs]


# ==============================================
# MODULNI RO'YXATDAN O'TKAZISH
# ==============================================


def register_module() -> Dict[str, object]:
    return {
        "module_name": "SecurityCenter",
        "version": "1.0.0",
        "router": router,
        "description": "Xavfsizlik monitoringi: shifokor xabarlari, tizim xatoliklari, kirish loglari",
        "endpoints": [
            "POST /security/messages",
            "GET /security/messages",
            "GET /security/messages/unread",
            "PATCH /security/messages/{id}/read",
            "DELETE /security/messages/{id}",
            "POST /security/system-errors",
            "GET /security/system-errors",
            "GET /security/system-errors/recent",
            "GET /security/login-logs",
            "GET /security/login-logs/failed",
        ],
    }
