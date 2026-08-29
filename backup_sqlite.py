# backup_sqlite.py
r"""
CLI wrapper — cron (Linux/macOS) yoki Task Scheduler (Windows) orqali
chaqirish uchun. Asosiy mantiq endi backup_manager.py'da: bu yerdan
chaqirilganda ham backup yaratiladi VA admin panelda (/admin/backup)
yoqilgan barcha ikkilamchi manzillarga (papka/tarmoq/tashqi xotira)
avtomatik ko'chiriladi.

Eski cron/Task Scheduler sozlamalari o'zgarishsiz ishlashda davom etadi:

Linux/macOS (cron, har kuni soat 23:00):
    0 23 * * * cd /path/to/MedFlow && /path/to/venv/bin/python backup_sqlite.py

Windows (Task Scheduler, har kuni soat 23:00):
    1. Task Scheduler -> Create Basic Task -> Trigger: Daily, 23:00
    2. Action: Start a program
       Program: C:\path\to\venv\Scripts\python.exe
       Arguments: backup_sqlite.py
       Start in: D:\Desktop\MedFlow MMM  (loyiha papkasi)

Eslatma: agar ilova (main.py / uvicorn) ishlab tursa, kunlik backup
allaqachon ilova ichidagi APScheduler orqali avtomatik bajariladi —
bu skript faqat ilova butunlay to'xtatilgan/serverga alohida cron
qo'yilgan holatlar uchun zapasnoy (backup) usul sifatida qoldirilgan.

Qo'lda ishga tushirish:
    python backup_sqlite.py
"""
import sys

from backup_manager import run_backup

if __name__ == "__main__":
    result = run_backup(trigger="scheduled", actor="cron")
    if result.get("ok"):
        print(f"✅ Backup yaratildi: {result.get('filename')} ({result.get('size_bytes', 0)} bayt)")
        for d in result.get("destinations", []):
            mark = "✅" if d.get("status") == "ok" else "⚠️"
            print(f"   {mark} {d.get('label')}: {d.get('path')}" + (f" — {d.get('error')}" if d.get("error") else ""))
        if result.get("removed_old"):
            print(f"🗑️ {result['removed_old']} ta eski backup o'chirildi.")
    else:
        print(f"⚠️ Backup xato: {result.get('error')}")
        sys.exit(1)
