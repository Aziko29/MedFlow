# modules/reminders.py
"""
Telegram eslatma linkidan bekor qilish uchun PUBLIC endpoint.

Diqqat: bu router boshqa modullardan farqli o'laroq
`Depends(get_current_user)` bilan himoyalanMAgan — bemor bu linkni
Telegramdan, login qilmasdan bosadi. Xavfsizlik shu yerda cancel_token'ning
o'zi bilan ta'minlanadi (256-bit tasodifiy, taxmin qilib bo'lmaydi,
faqat shu bitta appointment'ga tegishli, bitta marta ishlaydi).
"""
from typing import Dict

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

import models
from audit import log_action
from database import SessionLocal

router = APIRouter(prefix="/reminders", tags=["Reminders"])


def _page(title: str, message: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!DOCTYPE html>
<html lang="uz"><head><meta charset="utf-8">
<title>{title}</title>
<style>
body{{font-family:sans-serif;max-width:480px;margin:80px auto;text-align:center;color:#222}}
h2{{color:#1a73e8}}
</style></head>
<body><h2>{title}</h2><p>{message}</p></body></html>"""
    )


@router.get("/cancel/{token}", response_class=HTMLResponse)
def cancel_appointment_by_token(token: str) -> HTMLResponse:
    db = SessionLocal()
    try:
        appt = db.query(models.Appointment).filter(models.Appointment.cancel_token == token).first()
        if not appt:
            raise HTTPException(status_code=404, detail="Havola topilmadi yoki eskirgan")

        if appt.status in ("cancelled", "completed", "no_show"):
            return _page(
                "Navbat allaqachon yopilgan",
                f"Bu navbat holati: <b>{appt.status}</b>.",
            )

        appt.status = "cancelled"
        appt.cancel_reason = "Bemor tomonidan Telegram orqali bekor qilindi"
        db.add(appt)
        db.commit()
        log_action(db, None, "appointment.cancel_via_telegram", "Appointment", appt.id, "Telegram link orqali bekor qilindi")

        return _page(
            "✅ Navbat bekor qilindi",
            "Navbatingiz muvaffaqiyatli bekor qilindi. Klinikaga rahmat!",
        )
    finally:
        db.close()


def register_module() -> Dict[str, object]:
    return {
        "module_name": "Reminders",
        "version": "1.0.0",
        "router": router,
    }
