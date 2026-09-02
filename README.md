# ClinicFlow (MedFlow) v3.0

> Klinika va tibbiyot markazlari uchun to‘liq avtomatlashtirilgan boshqaruv tizimi.

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688)](https://fastapi.tiangolo.com)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0%2B-red)](https://sqlalchemy.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## 📋 Mundarija

- [Loyiha haqida](#-loyiha-haqida)
- [Asosiy imkoniyatlar](#-asosiy-imkoniyatlar)
- [Texnologik stek](#-texnologik-stek)
- [O‘rnatish](#-ornatish)
- [Loyiha tuzilishi](#-loyiha-tuzilishi)
- [Rollar va ruxsatlar](#-rollar-va-ruxsatlar)
- [Xavfsizlik](#-xavfsizlik)
- [API hujjatlari](#-api-hujjatlari)
- [Ekran tasvirlari](#-ekran-tasvirlari)
- [Litsenziya](#-litsenziya)

---

## 🏥 Loyiha haqida

**ClinicFlow** — klinika, shifoxona va tibbiyot markazlari uchun mo‘ljallangan veb-asosida ishlaydigan boshqaruv tizimi. Bemorlarni ro‘yxatga olish, navbatlarni boshqarish, to‘lovlarni kuzatish, laboratoriya tahlillarini yuritish hamda xodimlar faoliyatini monitoring qilish — barchasi bitta platformada.

Loyiha **FastAPI** asosida qurilgan bo‘lib, yuqori samaradorlik, xavfsizlik va kengaytiriluvchanlikni ta‘minlaydi.

---

## ✨ Asosiy imkoniyatlar

### 👥 Bemorlar boshqaruvi
- Bemorlarni ro‘yxatga olish, tahrirlash va o‘chirish
- Shaxsiy ma'lumotlarning **AES-256-GCM** shifrlanishi
- Telefon raqami bo‘yicha dublikat tekshiruvi (blind index)
- Bemor rasmini yuklash va saqlash
- **Allergiyalar** va **surunkali kasalliklar** tarixini yuritish
- Davolanish tarixini (tashxis va davolash rejasi) vaqt chizig‘i ko‘rinishida ko‘rish
- Palataga yotqizish/chiqarish (statsionar davolash)

### 👨‍⚕️ Shifokorlar boshqaruvi
- Shifokorlar ro‘yxati va ularning mutaxassisliklari
- Konsultatsiya narxi (narxnoma)
- Ish jadvali va malaka toifasi
- Har bir shifokor uchun alohida profil va qabullar tarixi

### 📅 Qabullar (Navbat)
- Onlayn band qilish va vaqtini ko‘chirish
- **State-machine** asosida holat boshqaruvi:
  - `waiting` → `in_progress` → `completed`
  - `delayed`, `cancelled`, `no_show`
- Bir vaqtda ikki bemor yozishning oldini olish (double-booking taqiqi)
- Qabul bekor qilish sababini qayd etish

### 💰 To‘lovlar
- Har bir to‘lov aniq bitta qabulga bog‘langan
- Real vaqtda qarz hisoblash: `narx - to‘langan`
- **Ikki bosqichli qaytarim:**
  1. Admin to‘lovni bekor qiladi (`cancelled`)
  2. Kassir haqiqiy pulni qaytaradi (`refunded`)
- To‘lovlar tarixi CSV formatida eksport qilish

### 🔬 Laboratoriya tahlillari
- 20 ta standart tahlil shabloni (qon, siydik, biokimyo va boshqalar)
- Ko‘rsatkichlar me‘yoriy qiymat bilan oldindan to‘ldiriladi
- Me‘yordan chetga chiqish avtomatik aniqlanadi (past/yuqori/diqqat)
- Tahlil natijalarini bemor kartochkasida sinxron ko‘rish

### 🔐 Rollar va ruxsatlar
- **Admin** — to‘liq huquq
- **Reception** — bemorlar, qabullar, to‘lovlar
- **Doctor** — o‘z qabullari, tahlillar, davolanish tarixi
- **Cashier** — faqat to‘lovlar va qaytarimlar
- **Lab Doctor** — faqat tahlil natijalari va o‘ziga biriktirilgan bemorlar
- **Assistant Admin** — faqat o‘qish (hisobotlar, audit, xodimlar)

### 🛡️ Xavfsizlik markazi
- **Audit jurnali** — har bir yozuvchi amalni qayd etish (kim, qachon, nima qildi)
- Kirish (login) loglari va muvaffaqiyatsiz urinishlarni kuzatish
- Tizim xatoliklarini avtomatik yozib olish
- Xodimlar o‘rtasida xavfsizlik xabarlari yuborish

### ⚙️ Qo'shimcha funksiyalar
- Dashboard — bugungi/jami KPI ko‘rsatkichlar va jonli navbat
- Hisobotlar — shifokor samaradorligi, band vaqt tahlili, bekor qilish sabablari
- Zaxira nusxa (backup) — avtomatik va qo‘lda
- Telegram bot orqali eslatmalar (24 soat va 2 soat oldin)
- Dark/Light/Auto mavzu rejimi
- Bemor portali (FAZA 2) — SMS-kod orqali kirish
- Davlat identifikatsiya tizimi integratsiyasi (OneID)

---

## 🛠 Texnologik stek

| Katgoriya | Texnologiya |
|-----------|-------------|
| **Backend** | Python 3.11+, FastAPI, Uvicorn |
| **Ma'lumotlar bazasi** | SQLAlchemy 2.0, Alembic (migratsiyalar) |
| **Shablonlar** | Jinja2 |
| **Frontend** | Vanilla JS, Chart.js, Font Awesome |
| **Shifrlash** | AES-256-GCM (cryptography) |
| **Parol xeshlash** | PBKDF2 |
| **Rate Limiting** | slowapi |
| **Eslatmalar** | APScheduler + python-telegram-bot |
| **SMS** | Eskiz.uz integratsiyasi |

---

## 🚀 O‘rnatish

### 1. Loyihani klonlash

```bash
git clone https://github.com/Aziko29/medflow.git
cd medflow
```

### 2. Virtual muhit yaratish

```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# yoki
venv\Scripts\activate   # Windows
```

### 3. Bog‘liqliklarni o‘rnatish

```bash
pip install -r requirements.txt
```

### 4. Muhit o‘zgaruvchilarini sozlash

```bash
cp .env.example .env
```

`.env` faylini tahrirlang:

```env
DATABASE_URL=sqlite:///./clinicflow.db
# yoki PostgreSQL uchun:
# DATABASE_URL=postgresql+psycopg2://user:password@localhost:5432/clinicflow

CLINICFLOW_SECRET_KEY=your-secret-key-here
CLINICFLOW_FIELD_KEY=your-field-encryption-key
CLINICFLOW_BLIND_INDEX_KEY=your-blind-index-key
CLINICFLOW_ADMIN_ACTIONS_PASSWORD=your-dangerous-actions-password

# Redis (ko‘p worker uchun, ixtiyoriy):
# CLINICFLOW_REDIS_URL=redis://localhost:6379/0

# Telegram (ixtiyoriy):
# TELEGRAM_BOT_TOKEN=your-bot-token
```

Kalitlarni generatsiya qilish:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 5. Ma'lumotlar bazasini yaratish

```bash
alembic upgrade head
```

### 6. Birinchi admin hisobini yaratish

```bash
python create_users.py
```

### 7. Ilovani ishga tushirish

```bash
# Development
python main.py
# yoki
uvicorn main:app --reload

# Production
gunicorn main:app --workers 4 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

Brauzerda `http://127.0.0.1:8000` oching.

---

## 📁 Loyiha tuzilishi

```
MedFlow/
├── main.py                    # FastAPI kirish nuqtasi
├── models.py                  # SQLAlchemy ORM modellar
├── schemas.py                 # Pydantic v2 sxemalar
├── database.py                # DB engine & session
├── auth.py                    # Autentifikatsiya va avtorizatsiya
├── audit.py                   # Audit jurnal yordamchisi
├── rate_limiter.py            # Brute-force himoyasi
├── lab_templates.py           # 20 ta tahlil shabloni
├── crypto_fields.py           # Maydon shifrlash
├── create_users.py            # Foydalanuvchi yaratish skripti
├── backup_manager.py          # Zaxira nusxa boshqaruvi
├── reminder_service.py        # Telegram eslatmalar xizmati
├── eskiz_client.py            # SMS xizmati integratsiyasi
├── .env.example               # Muhit o‘zgaruvchilari namunasi
│
├── modules/                   # Modullar (avtomatik yuklanadi)
│   ├── auth_module.py         # Login/logout/parol boshqaruvi
│   ├── patients.py            # Bemorlar CRUD
│   ├── doctors.py             # Shifokorlar CRUD
│   ├── appointments.py        # Qabullar va state-machine
│   ├── payments.py            # To‘lovlar va qaytarimlar
│   ├── lab_results.py         # Tahlil natijalari
│   ├── dashboard.py           # Dashboard ma'lumotlari
│   ├── reports.py             # Hisobotlar
│   ├── audit_module.py        # Audit jurnal API
│   ├── security_center.py     # Xavfsizlik markazi
│   ├── settings_module.py     # Tizim sozlamalari
│   ├── admin_profile.py       # Klinika profili
│   ├── gov_integration.py     # Davlat tizimi integratsiyasi
│   └── patient_portal.py      # Bemor portali (FAZA 2)
│
├── templates/                 # Jinja2 HTML shablonlar
│   ├── base.html              # Asosiy shablon (sidebar, modal)
│   ├── login.html             # Kirish sahifasi
│   ├── dashboard.html         # Boshqaruv paneli
│   ├── patients.html          # Bemorlar ro‘yxati
│   ├── patient_detail.html    # Bemor kartochkasi (tab'lar)
│   ├── doctors.html           # Shifokorlar
│   ├── doctor_detail.html     # Shifokor kartochkasi
│   ├── appointments.html      # Qabullar
│   ├── payments.html          # To‘lovlar
│   ├── lab_results.html       # Tahlil natijalari
│   ├── reports.html           # Hisobotlar
│   ├── users.html             # Xodimlar boshqaruvi
│   ├── audit_log.html         # Audit jurnali
│   ├── backup.html            # Zaxira nusxa
│   ├── profile.html           # Sozlamalar
│   └── errors/                # Xato sahifalari (403, 404, 500)
│
├── static/                    # Statik fayllar
│   ├── css/theme.css          # CSS o‘zgaruvchilar (dark/light)
│   └── uploads/               # Yuklangan rasmlar
│       ├── patients/
│       └── doctors/
│
├── alembic/                   # DB migratsiyalari
├── deploy/                    # Production shablonlar
│   ├── clinicflow.service.example    # systemd
│   ├── nginx_clinicflow.conf.example # Nginx konfiguratsiyasi
│   └── backup_postgres.sh.example    # PostgreSQL zaxira
│
└── tests/                     # Testlar (pytest)
```

---

## 👤 Rollar va ruxsatlar

| Rol | Bemorlar | Qabullar | Shifokorlar | To‘lovlar | Tahlillar | Hisobotlar | Xodimlar | Audit |
|-----|:--------:|:--------:|:-----------:|:---------:|:---------:|:----------:|:--------:|:-----:|
| **Admin** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Reception** | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Doctor** | 👁️ | 👁️ | 👁️ | ❌ | ✅ | ❌ | ❌ | ❌ |
| **Cashier** | 👁️ | 👁️ | 👁️ | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Lab Doctor** | 👁️ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ |
| **Assistant Admin** | 👁️ | 👁️ | ❌ | ❌ | ❌ | 👁️ | 👁️ | 👁️ |

> ✅ — to‘liq CRUD  |  👁️ — faqat ko‘rish  |  ❌ — ruxsat yo‘q

---

## 🔒 Xavfsizlik

- **Parol xeshlash:** PBKDF2 (salt + 100,000 iterations)
- **Maydon shifrlash:** AES-256-GCM (telefon, manzil, tibbiy eslatmalar, PINFL, pasport)
- **Blind Index:** Shifrlangan maydonlar bo‘yicha qidiruv/dublikat tekshiruvi
- **Sessiya:** Signed cookie (Fernet) + 8 soatlik muddati
- **Rate Limiting:** Login uchun brute-force himoyasi (slowapi)
- **CSP Headers:** Content Security Policy, X-Frame-Options, XSS Protection
- **Audit:** Har bir yozuvchi amal (qo'shish/o'zgartirish/o'chirish) jurnalga yoziladi
- **2-qatlam parol:** Xavfli amallar (bazani tozalash, tizimni qayta ishga tushirish) uchun alohida parol
- **Inactivity Timer:** 15 daqiqa harakatsizlikdan so‘ng avtomatik chiqish

---

## 📚 API hujjatlari

Development rejimida avtomatik hujjatlar mavjud:

- **Swagger UI:** `http://127.0.0.1:8000/docs`
- **ReDoc:** `http://127.0.0.1:8000/redoc`
- **OpenAPI JSON:** `http://127.0.0.1:8000/openapi.json`

> Production'da (`ENV=production`) ushbu yo'llar o'chiriladi.

---

## 🖼️ Ekran tasvirlari

| Sahifa | Tavsif |
|--------|--------|
| **Dashboard** | KPI kartochkalari, jonli navbat, bugungi statistika |
| **Bemorlar** | Ro‘yxat, qidiruv, filtrlash, yangi bemor qo‘shish |
| **Bemor kartochkasi** | Tab'lar: Umumiy, Tibbiy tarix, Davolanishlar, Tashriflar, Tahlillar, To‘lovlar |
| **Qabullar** | Holat boshqaruvi (dropdown), to‘lov qabul qilish, vaqtini ko‘chirish |
| **To‘lovlar** | CSV eksport, qaytarim boshqaruvi |
| **Tahlillar** | 20 ta shablon, avtomatik me‘yor tekshiruvi, batafsil ko‘rish |
| **Hisobotlar** | Shifokor samaradorligi, soatlik yuklama, bekor qilish sabablari |

---

## 🧪 Testlarni ishga tushirish

```bash
pytest tests/ -v
```

---

## 📝 Migratsiyalar

Schema o‘zgarishlari Alembic orqali boshqariladi:

```bash
# Yangi migratsiya yaratish
alembic revision --autogenerate -m "yangi ustun qo‘shildi"

# Migratsiyani qo‘llash
alembic upgrade head

# Oxirgi migratsiyani bekor qilish
alembic downgrade -1

# Joriy holatni tekshirish
alembic current
```

---

## 🤝 Hissa qo'shish

1. Fork qiling
2. Yangi branch yarating (`git checkout -b feature/yangi-funksiya`)
3. O'zgarishlarni kiritib commit qiling (`git commit -am 'Yangi funksiya qo‘shildi'`)
4. Push qiling (`git push origin feature/yangi-funksiya`)
5. Pull Request yarating

---

## 📄 Litsenziya

Ushbu loyiha [MIT](LICENSE) litsenziyasi ostida tarqatiladi.

---

> **Eslatma:** `.env` faylidagi `CLINICFLOW_SECRET_KEY` va shifrlash kalitlari production uchun albatta kuchli va noyob bo‘lishi kerak. Kalitlarni hech qachon ochiq repozitoriyga qo‘ymang!

---

**Muallif:** [Aziko29](https://github.com/Aziko29)  
**Versiya:** 3.0.0  
**Oxirgi yangilanish:** 2026
