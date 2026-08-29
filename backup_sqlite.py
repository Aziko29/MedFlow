# backup_sqlite.py
r"""
5-BAND (5.3): Avtomatik backup — SQLite fayli uchun.

Bu skript clinicflow.db faylining vaqt-tamg'ali (timestamped) nusxasini
backups/ papkasiga oladi va 30 kundan eski nusxalarni avtomatik o'chiradi
(disk to'lib ketmasligi uchun).

MUHIM: bu skript faqat SQLite uchun ishlaydi. Agar DATABASE_URL
PostgreSQL'ga o'rnatilgan bo'lsa, buning o'rniga `pg_dump` ishlatiladi —
qarang: deploy/backup_postgres.sh.example

Kunlik avtomatik ishga tushirish uchun (Linux/macOS, cron):
    0 3 * * * cd /path/to/MedFlow && /path/to/venv/bin/python backup_sqlite.py

Windows uchun (Task Scheduler bilan har kuni soat 03:00):
    1. Task Scheduler ochish -> Create Basic Task
    2. Trigger: Daily, 03:00
    3. Action: Start a program
       Program: C:\path\to\venv\Scripts\python.exe
       Arguments: backup_sqlite.py
       Start in: D:\Desktop\MedFlow MMM  (loyiha papkasi)

Qo'lda ishga tushirish:
    python backup_sqlite.py
"""
import os
import shutil
import sys
from datetime import datetime, timedelta

DB_FILENAME = "clinicflow.db"
BACKUP_DIR = "backups"
KEEP_DAYS = 30


def backup_now() -> None:
    project_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(project_dir, DB_FILENAME)
    backup_dir = os.path.join(project_dir, BACKUP_DIR)

    if not os.path.exists(db_path):
        print(f"⚠️ {DB_FILENAME} topilmadi ({db_path}) — backup qilinmadi.")
        print("   (Agar DATABASE_URL PostgreSQL'ga o'rnatilgan bo'lsa, bu normal —")
        print("    buning o'rniga deploy/backup_postgres.sh.example'ni ishlating.)")
        sys.exit(1)

    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"clinicflow_{timestamp}.db"
    backup_path = os.path.join(backup_dir, backup_filename)

    shutil.copy2(db_path, backup_path)
    print(f"✅ Backup yaratildi: {backup_path}")

    _cleanup_old_backups(backup_dir)


def _cleanup_old_backups(backup_dir: str) -> None:
    """KEEP_DAYS kundan eski backup fayllarini o'chiradi."""
    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
    removed = 0
    for filename in os.listdir(backup_dir):
        if not (filename.startswith("clinicflow_") and filename.endswith(".db")):
            continue
        filepath = os.path.join(backup_dir, filename)
        mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
        if mtime < cutoff:
            os.remove(filepath)
            removed += 1
    if removed:
        print(f"🗑️ {removed} ta {KEEP_DAYS} kundan eski backup o'chirildi.")


if __name__ == "__main__":
    backup_now()
