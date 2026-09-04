# backup_manager.py
"""
🗄️ Zaxira nusxalash (backup) tizimi — markaziy modul.

Nima qiladi:
  1. Bazani (SQLite — to'liq; PostgreSQL — pg_dump orqali, agar mavjud
     bo'lsa) vaqt-tamg'ali (timestamped) nusxasini backups/ papkasiga oladi.
  2. Sozlamalarda (backup_settings.json) yoqilgan HAR BIR ikkilamchi
     manzilga (papka / tarmoqdagi kompyuter / tashqi xotira) nusxani
     avtomatik ko'chiradi — "hammasi bitta joyda" xavfidan himoya.
  3. Har kuni belgilangan vaqtda (standart 23:00) avtomatik ishga tushadi
     (APScheduler) va admin panel orqali istalgan payt qo'lda ham
     ishga tushirish mumkin.
  4. Har bir urinish backups/backup_history.json fayliga yoziladi —
     admin panelda oxirgi urinishlar va ularning har bir manzilga
     ko'chirilgan-ko'chirilmaganligi ko'rinadi.

Bu modulni ham main.py (web admin panel + scheduler), ham backup_sqlite.py
(cron/Task Scheduler orqali eski uslubda qo'lda chaqirish) ishlatadi.
"""
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timedelta
from typing import Optional

from database import DATABASE_URL

logger = logging.getLogger("medflow.backup")

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(PROJECT_DIR, "backups")
CONFIG_PATH = os.path.join(PROJECT_DIR, "backup_settings.json")
HISTORY_PATH = os.path.join(BACKUP_DIR, "backup_history.json")

KEEP_DAYS = 30          # mahalliy backups/ papkasidagi eski nusxalar shu muddatdan keyin o'chiriladi
MAX_HISTORY = 100        # tarixda saqlanadigan yozuvlar soni

# Fikr: uchta doimiy manzil turi — foydalanuvchi so'ragan "papka /
# tarmoqdagi kompyuter / tashqi xotira" checkboxlari. Texnik jihatdan
# uchalasi ham "diskdagi papka manziliga fayl nusxalash" — farqi faqat
# foydalanuvchiga tushunarli bo'lgan yorliq (label) va odatiy yo'l shakli
# (masalan tarmoq uchun \\SERVER\share, tashqi xotira uchun E:\...).
DEFAULT_DESTINATIONS = [
    {"type": "folder", "label": "Papka (shu kompyuterda)", "path": "", "enabled": False},
    {"type": "network", "label": "Tarmoqdagi boshqa kompyuter", "path": "", "enabled": False},
    {"type": "external", "label": "Tashqi xotira (flesh/disk)", "path": "", "enabled": False},
]


# ==============================================
# SOZLAMALAR (backup_settings.json)
# ==============================================
def load_settings() -> dict:
    defaults = {"destinations": [dict(d) for d in DEFAULT_DESTINATIONS],
                "schedule_hour": 23, "schedule_minute": 0}
    if not os.path.exists(CONFIG_PATH):
        return defaults
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        logger.exception("backup_settings.json o'qib bo'lmadi — standart qiymatlar ishlatiladi")
        return defaults
    defaults.update({k: v for k, v in data.items() if k in defaults})
    return defaults


def save_settings(destinations: list) -> dict:
    """Faqat 3 ta ma'lum turdagi manzilni saqlaydi (foydalanuvchi kiritgan
    boshqa maydonlar e'tiborga olinmaydi — xavfsizlik/soddalik uchun)."""
    by_type = {d.get("type"): d for d in destinations if isinstance(d, dict)}
    clean = []
    for default in DEFAULT_DESTINATIONS:
        incoming = by_type.get(default["type"], {})
        path = str(incoming.get("path") or "").strip()
        clean.append({
            "type": default["type"],
            "label": default["label"],
            "path": path,
            "enabled": bool(incoming.get("enabled")) and bool(path),
        })
    settings = load_settings()
    settings["destinations"] = clean
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    return settings


def check_path(path: str) -> dict:
    """Yo'lni FAQAT TEKSHIRADI: mavjudmi, yozish huquqi bormi.
    Yo'q papkani hech qachon yaratmaydi — buni alohida
    `create_path()` funksiyasi (admin so'rovi bilan) bajaradi."""
    path = (path or "").strip()
    if not path:
        return {"exists": False, "writable": False, "message": "Yo'l kiritilmagan"}
    if not os.path.isdir(path):
        return {"exists": False, "writable": False,
                 "message": "Papka mavjud emas. Iltimos, avval papka yaratib, keyin tekshiring."}
    writable = os.access(path, os.W_OK)
    return {"exists": True, "writable": writable,
            "message": "Yo'l topildi, yozish mumkin" if writable else "Yo'l mavjud, lekin yozish huquqi yo'q"}


def create_path(path: str) -> dict:
    """Yo'q papkani (va kerak bo'lsa ota-papkalarni) yaratadi. Faqat
    admin so'rovi bilan, foydalanuvchi ongli ravishda tasdiqlaganda
    chaqiriladi — check_path() bunga hech qachon o'zi murojaat qilmaydi."""
    path = (path or "").strip()
    if not path:
        return {"exists": False, "writable": False, "message": "Yo'l kiritilmagan"}
    if os.path.isdir(path):
        writable = os.access(path, os.W_OK)
        return {"exists": True, "writable": writable,
                "message": "Papka allaqachon mavjud edi."}
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        return {"exists": False, "writable": False,
                 "message": f"Papkani yaratib bo'lmadi: {e}"}
    writable = os.access(path, os.W_OK)
    return {"exists": True, "writable": writable,
            "message": "Papka muvaffaqiyatli yaratildi." if writable else
                       "Papka yaratildi, lekin yozish huquqi yo'q."}


# ==============================================
# ASOSIY (PRIMARY) BACKUP YARATISH
# ==============================================
def _is_sqlite() -> bool:
    return DATABASE_URL.startswith("sqlite")


def _sqlite_db_path() -> str:
    raw = DATABASE_URL.split("///", 1)[-1]
    if not os.path.isabs(raw):
        raw = os.path.join(PROJECT_DIR, raw)
    return os.path.normpath(raw)


def _checkpoint_wal() -> None:
    """WAL rejimida ba'zi yozuvlar hali -wal faylida bo'lishi mumkin —
    checkpoint qilmasdan nusxalash noto'liq baza berishi mumkin."""
    try:
        from database import engine
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA wal_checkpoint(FULL)")
    except Exception:
        logger.exception("WAL checkpoint bajarilmadi — baribir nusxalashda davom etamiz")


def _create_primary_backup(timestamp: str) -> dict:
    os.makedirs(BACKUP_DIR, exist_ok=True)

    if _is_sqlite():
        db_path = _sqlite_db_path()
        if not os.path.exists(db_path):
            return {"ok": False, "error": f"Baza fayli topilmadi: {db_path}"}
        _checkpoint_wal()
        filename = f"clinicflow_{timestamp}.db"
        dest = os.path.join(BACKUP_DIR, filename)
        shutil.copy2(db_path, dest)
        return {"ok": True, "path": dest, "filename": filename}

    if DATABASE_URL.startswith("postgresql"):
        filename = f"clinicflow_{timestamp}.sql"
        dest = os.path.join(BACKUP_DIR, filename)
        if shutil.which("pg_dump") is None:
            return {"ok": False, "error": "pg_dump topilmadi (PostgreSQL client o'rnatilmagan)."}
        try:
            subprocess.run(["pg_dump", "--dbname", DATABASE_URL, "-f", dest],
                            check=True, timeout=600, capture_output=True)
            return {"ok": True, "path": dest, "filename": filename}
        except Exception as e:
            return {"ok": False, "error": f"pg_dump xato berdi: {e}"}

    return {"ok": False,
            "error": "Bu baza turi uchun avtomatik backup qo'llab-quvvatlanmaydi "
                      "(hozircha faqat SQLite va PostgreSQL)."}


def _copy_to_destinations(primary_path: str, filename: str, destinations: list) -> list:
    results = []
    for dest in destinations:
        if not dest.get("enabled") or not dest.get("path"):
            continue
        target_dir = dest["path"]
        entry = {"type": dest["type"], "label": dest.get("label", dest["type"]), "path": target_dir}
        try:
            os.makedirs(target_dir, exist_ok=True)
            shutil.copy2(primary_path, os.path.join(target_dir, filename))
            entry["status"] = "ok"
        except Exception as e:
            entry["status"] = "error"
            entry["error"] = str(e)
            logger.exception("Zaxirani '%s' manziliga ko'chirib bo'lmadi", target_dir)
        results.append(entry)
    return results


def _cleanup_old_backups() -> int:
    if not os.path.isdir(BACKUP_DIR):
        return 0
    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
    removed = 0
    for filename in os.listdir(BACKUP_DIR):
        if not (filename.startswith("clinicflow_") and (filename.endswith(".db") or filename.endswith(".sql"))):
            continue
        filepath = os.path.join(BACKUP_DIR, filename)
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
            if mtime < cutoff:
                os.remove(filepath)
                removed += 1
        except Exception:
            logger.exception("Eski backup o'chirilmadi: %s", filepath)
    return removed


# ==============================================
# TARIX (backup_history.json)
# ==============================================
def _load_history() -> list:
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_history(history: list) -> None:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history[-MAX_HISTORY:], f, ensure_ascii=False, indent=2)


# ==============================================
# OMMAVIY (PUBLIC) API
# ==============================================
def run_backup(trigger: str = "manual", actor: str = "system") -> dict:
    """Backup yaratadi (aniq sana-vaqt fayl nomida) va yoqilgan barcha
    ikkilamchi manzillarga tarqatadi. trigger: 'manual' | 'scheduled'."""
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    settings = load_settings()

    result: dict = {
        "trigger": trigger,
        "actor": actor,
        "started_at": now.isoformat(timespec="seconds"),
    }

    primary = _create_primary_backup(timestamp)
    result["ok"] = primary.get("ok", False)

    if not primary["ok"]:
        result["error"] = primary.get("error")
        logger.error("❌ Backup muvaffaqiyatsiz (%s, %s): %s", trigger, actor, primary.get("error"))
    else:
        result["filename"] = primary["filename"]
        result["size_bytes"] = os.path.getsize(primary["path"])
        result["destinations"] = _copy_to_destinations(
            primary["path"], primary["filename"], settings.get("destinations", [])
        )
        result["removed_old"] = _cleanup_old_backups()
        logger.info("✅ Backup yaratildi (%s, %s): %s", trigger, actor, primary["filename"])

    history = _load_history()
    history.append(result)
    _save_history(history)
    return result


def get_history(limit: int = 20) -> list:
    return list(reversed(_load_history()[-limit:]))


def get_status() -> dict:
    settings = load_settings()
    history = _load_history()
    return {
        "destinations": settings.get("destinations", DEFAULT_DESTINATIONS),
        "schedule_hour": settings.get("schedule_hour", 23),
        "schedule_minute": settings.get("schedule_minute", 0),
        "last_backup": history[-1] if history else None,
        "db_engine": "sqlite" if _is_sqlite() else DATABASE_URL.split("://", 1)[0],
    }


# ==============================================
# KUNLIK AVTOMATIK ISHGA TUSHIRISH (APScheduler)
# ==============================================
_scheduler = None
_lock_file = None


def _acquire_singleton_lock() -> bool:
    """gunicorn --workers N bilan bir nechta worker bir vaqtda ishga
    tushsa ham, backup job'i faqat BITTA worker'da ishlashi uchun
    fayl-darajasidagi lock (reminder_service.py'dagi bilan bir xil naqsh)."""
    global _lock_file
    lock_path = os.environ.get("BACKUP_LOCK_FILE", "/tmp/medflow_backup.lock")
    try:
        import fcntl
        _lock_file = open(lock_path, "w")
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except ImportError:
        return True  # Windows dev muhiti — odatda bitta process
    except OSError:
        return False


def start_backup_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    if not _acquire_singleton_lock():
        logger.info("Bu worker'da backup scheduler ishga tushirilmadi — boshqa worker boshqarmoqda.")
        return

    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    settings = load_settings()
    hour = int(settings.get("schedule_hour", 23))
    minute = int(settings.get("schedule_minute", 0))

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        lambda: run_backup(trigger="scheduled", actor="system"),
        trigger=CronTrigger(hour=hour, minute=minute),
        id="daily_backup",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("🗄️ Backup scheduler ishga tushdi: har kuni soat %02d:%02d.", hour, minute)


def stop_backup_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
