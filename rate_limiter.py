# rate_limiter.py
"""
Alohida, betaraf modul — faqat `limiter` (slowapi) obyektini yaratish
uchun.

🩹 BUG FIX: avvalgi versiyada `limiter` main.py'da yaratilib,
modules/auth_module.py uni `from main import limiter` orqali olardi. Bu
uvicorn yoki TestClient orqali ishga tushirilganda ishlardi (ular
main.py'ni "main" nomli modul sifatida import qiladi), LEKIN
`python main.py` bilan TO'G'RIDAN-TO'G'RI ishga tushirilganda
MUVAFFAQIYATSIZ bo'lardi:

  - `python main.py` skriptni "__main__" nomi bilan ishga tushiradi,
    "main" nomi esa sys.modules'da HALI YO'Q bo'ladi.
  - modules/auth_module.py `from main import limiter` deganda, Python
    "main" nomli modulni topolmay, main.py faylini YANA BIR MARTA,
    BOSHIDAN qayta ishga tushiradi (bu safar "main" nomi ostida).
  - Bu ikkinchi ijro o'z navbatida yana barcha modullarni (shu jumladan
    modules/auth_module.py'ning o'zini ham) qayta yuklashga urinadi —
    natijada auth_module.py "register_module()" funksiyasiga yetib
    bormasdan, chala holda import qilingan bo'lib qoladi. Aynan shu
    yerdan "auth_module.py faylida 'register_module' funksiyasi yo'q"
    degan ogohlantirish kelib chiqqan edi.

Yechim: `limiter` ikkalasi (main.py VA modules/auth_module.py) tomonidan
ham shu UCHINCHI, hech kimga bog'liq bo'lmagan moduldan import qilinadi —
o'z-o'ziga bog'liqlik (self-import) butunlay yo'q qilinadi, qaysi usulda
ishga tushirilishidan (`python main.py`, `uvicorn main:app`, TestClient)
qat'i nazar bir xil ishlaydi.

⬅️ TUZATILDI (4-band, 4.2): avval `slowapi` standart (in-memory) storage
bilan ishlardi — bu HAR BIR gunicorn worker o'z hisoblagichini alohida
saqlashi degani. Natijada `/login` uchun brute-force cheklovi amalda
worker soniga (masalan 4 worker = 4x) bo'linib zaiflashardi: hujumchi
har so'rovni boshqa workerga yo'naltirib (load balancer round-robin
qiladi), amaldagi limitni 4 baravar oshirib o'tishi mumkin edi.

YECHIM: agar CLINICFLOW_REDIS_URL sozlangan bo'lsa, `slowapi`ning Redis
storage backend'i ishlatiladi (`storage_uri="redis://..."`) — hisoblagich
BARCHA workerlar bo'ylab BITTA, markazlashgan joyda saqlanadi, shuning
uchun cheklov haqiqiy (umumiy) bo'ladi. Sozlanmagan bo'lsa, avvalgi
xotiradagi (worker-ichida) storage'ga qaytiladi va ishga tushishda ANIQ
ogohlantirish beriladi — bu, Redis qo'shilmaguncha, oshkora hujjatlashtirilgan
xavf sifatida qoladi (README'ning "Production uchun" bo'limiga qarang).
"""
import logging
import os

from slowapi import Limiter
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)

_REDIS_URL = os.environ.get("CLINICFLOW_REDIS_URL")

if _REDIS_URL:
    try:
        limiter = Limiter(key_func=get_remote_address, storage_uri=_REDIS_URL)
        logger.info(
            "✅ Rate limiter Redis (%s) orqali markazlashgan holda ishlayapti — "
            "/login cheklovi barcha worker prosesslar bo'ylab umumiy.", _REDIS_URL,
        )
    except Exception:
        logger.exception(
            "❌ CLINICFLOW_REDIS_URL sozlangan (%s), lekin rate limiter uchun "
            "Redis storage'ni ishga tushirib bo'lmadi — xotiradagi (in-memory) "
            "storage'ga qaytilmoqda (ko'p-worker rejimida cheklov zaiflashadi).",
            _REDIS_URL,
        )
        limiter = Limiter(key_func=get_remote_address)
else:
    limiter = Limiter(key_func=get_remote_address)
    logger.warning(
        "⚠️ CLINICFLOW_REDIS_URL o'rnatilmagan — rate limiter xotirada "
        "(in-memory, faqat shu worker prosessiga tegishli) ishlayapti. "
        "Production'da bir nechta gunicorn worker bilan ishlatilsa, "
        "/login uchun brute-force cheklovi amalda worker soniga bo'linib "
        "zaiflashadi (masalan 4 worker = amaldagi limit 4x). Xavfni "
        "butunlay yo'q qilish uchun CLINICFLOW_REDIS_URL'ni sozlang "
        "(masalan redis://localhost:6379/0)."
    )
