# utils/timezone.py
"""
Markaziy vaqt zonasi (timezone) moduli — Prompt 16.

Muammo: datetime.now() / date.today() serverning O'ZIDA o'rnatilgan
vaqt zonasidan foydalanadi. Agar server UTC'da (yoki boshqa istalgan
zonada) ishlayotgan bo'lsa-yu, klinika Asia/Tashkent'da joylashgan
bo'lsa — "bugungi navbat", KPI kunlik hisoblari va shu kabi barcha
sana/vaqtga bog'liq mantiq noto'g'ri natija beradi.

Yechim: klinika vaqt zonasi endi bitta joydan — SystemSettings.timezone
ustunidan (qarang models.py) — o'qiladi, server qanday zonada
ishlashidan qat'i nazar. Butun loyiha bo'ylab datetime.now()/
date.today() o'rniga shu moduldagi funksiyalar ishlatilishi kerak.

Eslatma: bazadagi DateTime ustunlari (appointments.scheduled_time,
*.created_at va h.k.) naiv (tzinfo=None) qiymatlar bo'lib, klinikaning
mahalliy devor-vaqtini (wall clock) ifodalaydi. Shu sababli bu
moduldagi funksiyalar ham standart holatda NAIV datetime qaytaradi —
aks holda DB'dagi naiv ustunlar bilan solishtirishda
"can't compare offset-naive and offset-aware datetimes" xatosi chiqadi.
"""
from datetime import date, datetime
from functools import lru_cache
from typing import Optional

from sqlalchemy.orm import Session
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Asia/Tashkent"


@lru_cache(maxsize=32)
def _load_zoneinfo(tz_name: str) -> ZoneInfo:
    """ZoneInfo obyektini keshlaydi (har chaqiriqda qayta yuklamaslik uchun).

    Noto'g'ri/topilmagan nom kiritilgan bo'lsa (masalan, admin
    SystemSettings.timezone'ga xato qiymat yozib qo'ygan bo'lsa),
    standart Asia/Tashkent'ga qaytadi — bu funksiyalar hech qachon
    xatolik bilan yiqilib tushmasligi kerak.
    """
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def get_clinic_timezone(db: Optional[Session] = None) -> ZoneInfo:
    """Klinikaning vaqt zonasini SystemSettings jadvalidan o'qiydi.

    db=None (yoki qator hali mavjud bo'lmasa/timezone bo'sh bo'lsa)
    holatida standart "Asia/Tashkent" qaytariladi — get-or-create
    qilib yangi qator yaratib yubormaydi, faqat o'qiydi (bu funksiya
    hamma joyda, hatto DB seansi bo'lmagan joyda ham chaqirilishi
    mumkin bo'lgani uchun yon ta'sirsiz bo'lishi kerak).
    """
    tz_name = DEFAULT_TIMEZONE
    if db is not None:
        import models  # doiraviy importdan qochish uchun funksiya ichida

        settings = db.query(models.SystemSettings).first()
        if settings is not None and settings.timezone:
            tz_name = settings.timezone
    return _load_zoneinfo(tz_name)


def now_in_clinic_tz(db: Optional[Session] = None, *, naive: bool = True) -> datetime:
    """Klinika vaqt zonasidagi HOZIRGI vaqtni qaytaradi.

    naive=True (standart): tzinfo olib tashlanadi, natija DB'dagi
    naiv DateTime ustunlari bilan to'g'ridan-to'g'ri solishtirish/
    filtrlash uchun yaroqli bo'ladi.
    naive=False kerak bo'lsa (masalan tashqi API/log uchun aware
    qiymat kerak bo'lsa) — tzinfo saqlanib qoladi.
    """
    aware_now = datetime.now(get_clinic_timezone(db))
    return aware_now.replace(tzinfo=None) if naive else aware_now


def today_in_clinic_tz(db: Optional[Session] = None) -> date:
    """Klinika vaqt zonasidagi BUGUNGI sanani qaytaradi."""
    return now_in_clinic_tz(db).date()
