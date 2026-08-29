# ClinicFlow (MedFlow) — v3.0

Klinika/tibbiyot markazi uchun to'liq boshqaruv tizimi: bemorlar,
shifokorlar, qabullar (navbat), to'lovlar va laboratoriya tahlil
natijalarini bitta joyda, bir-biriga bog'langan holda yuritadi.

## Asosiy imkoniyatlar

- **Bemorlar** — ro'yxatga olish, tahrirlash, o'chirish, qidiruv.
  Har bir bemor kartochkasida (`/patients/{id}`) **hammasi sinxron**:
  qabullar tarixi, to'lovlar tarixi va tahlil natijalari — bittasiga
  qo'shilgan yozuv darhol o'sha bemor sahifasida ham ko'rinadi.
- **Shifokorlar** — narxnoma (`consultation_price`), ish jadvali,
  har bir shifokor kartochkasida (`/doctors/{id}`) o'sha shifokorning
  barcha qabullari.
- **Qabullar (navbat)** — band qilish, holat state-machine (waiting →
  in_progress → completed, + delayed/cancelled/no_show), ko'chirish
  (reschedule), bekor qilish, ikki bemorni bir vaqtga yozib qo'yishning
  oldini olish (double-booking taqiqi).
- **To'lovlar** — har bir to'lov aniq bitta qabulga bog'langan, qarz
  (`narx - to'langan`) real vaqtda hisoblanadi (saqlanadigan soxta
  "balans" yo'q), qaytarim (refund), CSV eksport.
- **Tahlil natijalari (Lab Results)** — 20 ta standart tahlil shabloni
  (qon, siydik, biokimyo va h.k.), ko'rsatkichlar me'yoriy qiymat bilan
  oldindan to'ldiriladi, me'yordan chetga chiqish (past/yuqori/diqqat)
  serverda avtomatik hisoblanadi.
- **Rollar** — admin / reception / doctor / cashier, har biri faqat
  o'ziga tegishli amallarni bajara oladi.
- **Audit jurnal** — har bir yozuvchi (qo'shish/o'zgartirish/o'chirish)
  amal kim, qachon, nima qilgani bilan birga yoziladi (faqat admin
  ko'radi, `/admin/audit-log`).
- **Dashboard** — bugungi/jami KPI ko'rsatkichlari va jonli navbat.

## Ishga tushirish (development)

```bash
pip install -r requirements.txt
cp .env.example .env         # so'ng .env faylini o'zingiznikiga to'ldiring
alembic upgrade head           # bo'sh, lekin to'liq schema yaratiladi
python create_users.py          # birinchi admin hisobini yaratish
python main.py                   # yoki: uvicorn main:app --reload
```

**MUHIM:** `.env` faylida `CLINICFLOW_SECRET_KEY` bo'sh bo'lsa, dastur
ATAYLAB ishga tushmaydi (xavfsizlik uchun). Kalit generatsiya qilish:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

**MUHIM (maydon shifrlash):** `patient.phone/address/medical_notes`
AES-256-GCM bilan shifrlanadi (`crypto_fields.py`). `CLINICFLOW_FIELD_KEY`
va `CLINICFLOW_BLIND_INDEX_KEY` — ikkalasi ham `.env.example`da bor va
`cp .env.example .env` qilganingizda avtomatik ravishda `.env`ingizga
tushadi; generatsiya buyrug'i va izohlar o'sha faylda (bitta manba —
ikki joyda takrorlanmasin). Ular bo'sh qolsa, dastur birinchi bemor
amalida `FieldEncryptionError` bilan qulaydi.

Mavjud bazangiz bo'lsa, `alembic upgrade head` shu qatorlarni avtomatik
shifrlaydi va `phone_bidx` ustunini to'ldiradi (dublikat-telefon tekshiruvi
endi shu ustun orqali, DB unique constraint emas). Kalit rotatsiyasi uchun
`rotate_keys.py` ga qarang.

So'ng brauzerda `http://127.0.0.1:8000` oching — `/login`ga
yo'naltiriladi. Kirish hisobini `python create_users.py` orqali
o'zingiz yaratasiz (login/parol/rol so'raladi) — hech qanday
standart/demo hisob loyihada yo'q.

## Ma'lumotlar bazasi migratsiyalari (Alembic)

Schema (jadval/ustun) o'zgarishlari endi Alembic orqali versiyalanadi —
`models.py`ga to'g'ridan-to'g'ri ustun qo'shib qo'yish YETARLI EMAS,
mavjud (production) bazasi buni bilmaydi. Har bir model o'zgarishidan
keyin:

```bash
# 1. models.py'dagi o'zgarishga mos migratsiya avtomatik yaratiladi
alembic revision --autogenerate -m "qisqa tavsif, masalan: patients.email ustuni"

# 2. generatsiya qilingan faylni alembic/versions/ ichida albatta
#    ko'zdan kechiring — autogenerate har doim 100% to'g'ri topavermaydi
#    (masalan ustun nomini o'zgartirishni drop+add deb tushunishi mumkin)

# 3. migratsiyani qo'llash
alembic upgrade head
```

Boshqa foydali buyruqlar:

```bash
alembic current          # baza hozir qaysi migratsiyada turibdi
alembic history           # barcha migratsiyalar zanjiri
alembic downgrade -1      # oxirgi migratsiyani orqaga qaytarish
alembic check              # models.py bilan oxirgi migratsiya orasida farq bormi
```

Migratsiya qaysi bazaga tegishli ekanini `alembic/env.py` loyihaning
o'zidagi `database.py`dan (demak `.env`dagi `DATABASE_URL`dan) o'qiydi —
alembic.ini yoki env.py'da baza manzilini qo'lda yozish shart emas. Ya'ni
`alembic upgrade head` development'da SQLite'ga, `.env`da
`DATABASE_URL=postgresql+psycopg2://...` o'rnatilgan production'da esa
avtomatik PostgreSQL'ga ishlaydi.

**Yangi (bo'sh) baza bilan ishga tushirish** (production'dagi birinchi
deploy) quyidagicha bo'ladi:

```bash
alembic upgrade head     # bo'sh, lekin to'liq schema yaratiladi
python create_users.py   # birinchi admin hisobini yaratish
```

## Loyiha strukturasi

```
MedFlow/
├── database.py            # SQLAlchemy engine & session (.env orqali sozlanadi)
├── models.py               # ORM modellar: User, Doctor, Patient, Appointment,
│                             Payment, LabResult, AuditLog + state-machine
├── schemas.py                # Pydantic v2 sxemalar
├── auth.py                    # Parol xeshlash + signed-cookie sessiya + rol dependency
├── audit.py                    # Audit jurnal yozish yordamchisi
├── rate_limiter.py               # slowapi Limiter (brute-force himoyasi)
├── lab_templates.py                # 20 ta standart tahlil shabloni (ko'rsatkichlar, me'yorlar)
├── main.py                          # FastAPI kirish nuqtasi + dinamik modul dvigateli + GUI
├── create_admin.py                    # Doctor yozuvi yaratish skripti
├── create_users.py                      # Login hisoblarini yaratish/yangilash skripti
├── migrate_passwords.py                   # Parol xeshlash migratsiyasi (audit skript)
├── backup_sqlite.py                         # SQLite backup skripti
├── rotate_keys.py                             # Shifrlash kalitlarini rotatsiya qilish
├── .env.example                                # Namuna environment fayli
├── deploy/                                        # Production shablonlari
│   ├── clinicflow.service.example                   # systemd xizmat fayli
│   ├── nginx_clinicflow.conf.example                  # Nginx reverse-proxy
│   └── backup_postgres.sh.example                       # PostgreSQL backup
├── modules/
│   ├── auth_module.py    # /api/auth/login, /logout, /me, admin-reset-password
│   ├── patients.py        # Bemorlar CRUD + moliyaviy hisob (compute_financials)
│   ├── doctors.py          # Shifokorlar CRUD + narxnoma
│   ├── appointments.py      # Band qilish, holat state-machine, bekor qilish
│   ├── payments.py           # To'lov + qaytarim + CSV eksport
│   ├── lab_results.py         # Tahlil natijalari: qo'shish, tahrirlash, o'chirish
│   ├── audit_module.py         # Audit jurnalni ko'rish API (admin)
│   └── dashboard.py             # Bugungi/jami KPI + jonli navbat
└── templates/
    ├── base.html, login.html, dashboard.html
    ├── patients.html, patient_detail.html         # Bemorlar ro'yxati + to'liq kartochka
    ├── doctors.html, doctor_detail.html            # Shifokorlar ro'yxati + kartochka
    ├── appointments.html, payments.html
    ├── lab_results.html                              # Tahlil natijalari ro'yxati
    ├── reports.html
    └── audit_log.html                                  # Audit jurnal sahifasi (admin)
```

## Bemor kartochkasi (`/patients/{id}`) — sinxron ma'lumot

Bemor ismiga bosilganda ochiladigan sahifada uch bo'lim bir manbadan
(bazadan) real vaqtda o'qiladi, shu sababli har doim sinxron:

1. **Qabullar tarixi** — qaysi shifokorda, qachon, qanday narxda,
   qarzi qancha, holati (kutmoqda/jarayonda/yakunlandi/bekor va h.k.).
2. **To'lovlar tarixi** — har bir to'lov, qaysi qabulga tegishli
   ekani, qaytarilgan bo'lsa shu belgi bilan.
3. **Tahlil natijalari** — shu bemorga tegishli barcha lab tahlillari;
   "Batafsil" tugmasi bosilsa har bir ko'rsatkich, me'yoriy oraliq va
   bayroq (past/yuqori/me'yorda) ko'rsatiluvchi oyna ochiladi — bu xuddi
   `/lab-results/` sahifasidagi ma'lumot bilan bir xil, chunki ikkalasi
   ham bitta `LabResult` jadvalidan o'qiydi.

## Production uchun

- **`.env` fayli**: `database.py` ishga tushganda avtomatik yuklaydi
  (`python-dotenv`). `.env.example`ni nusxalab, o'zingiznikini to'ldiring.
- **PostgreSQL'ga o'tish**: `.env`da `DATABASE_URL`ni
  `postgresql+psycopg2://user:parol@host:5432/dbname` ga o'zgartiring va
  `pip install psycopg2-binary` qiling.
- **Backup**: `python backup_sqlite.py` (SQLite) yoki
  `deploy/backup_postgres.sh.example` (PostgreSQL) — kunlik cron/Task
  Scheduler orqali avtomatlashtirish tavsiya etiladi.
- **Deployment**: `deploy/clinicflow.service.example` (systemd) va
  `deploy/nginx_clinicflow.conf.example` (reverse-proxy + TLS).
- **Schema migratsiyalari**: hozircha `Base.metadata.create_all`
  ishlatiladi (yangi jadval yaratadi, lekin mavjud jadvalni xavfsiz
  o'zgartirmaydi). Modelga yangi ustun qo'shilsa va production
  ma'lumoti bo'lsa, `alembic` bilan haqiqiy migratsiya qo'shish tavsiya
  etiladi.

**Eslatma:** `CLINICFLOW_SECRET_KEY` muhit o'zgaruvchisi (yoki `.env`
fayli) orqali production uchun albatta belgilanishi kerak — bo'sh
bo'lsa dastur ishga tushmaydi.

### Ko'p-worker (gunicorn `--workers N > 1`) rejimi — Redis tavsiya etiladi

Bitta worker bilan (`python main.py` yoki `gunicorn --workers 1`)
hech narsa qo'shimcha sozlash shart emas. Lekin production'da bir
nechta worker (masalan `gunicorn main:app --workers 4 ...`) ishlatilsa,
ikkita joyda worker-ichida (in-memory) saqlanadigan holat bor edi:

- **`auth.py`dagi foydalanuvchi keshi** — admin parol/rolni
  o'zgartirganda, `clear_user_cache()` faqat o'sha so'rovni qabul
  qilgan workerda ishlaydi; qolgan workerlar eski ma'lumotni
  `CLINICFLOW_USER_CACHE_TTL_SECONDS` (standart 60s) gacha ishlatishda
  davom etishi mumkin.
- **`rate_limiter.py`dagi `/login` brute-force cheklovi** — har bir
  worker o'z hisoblagichini alohida yuritadi, shuning uchun amaldagi
  limit worker soniga bo'linib zaiflashadi (masalan 4 worker ≈ 4x
  zaifroq himoya).

**Yechim:** `.env`da `CLINICFLOW_REDIS_URL`ni sozlang (masalan
`redis://localhost:6379/0`) va `pip install redis` qiling — ikkalasi
ham avtomatik ravishda markazlashgan Redis backend'iga o'tadi (kesh
tozalash barcha workerlarga darhol ta'sir qiladi, rate-limit hisoblagich
umumiy bo'ladi). Sozlanmasa, ilova baribir ishlaydi, lekin yuqoridagi
xavf saqlanib qoladi — bu holatda dastur ishga tushishda log'ga aniq
ogohlantirish yozadi.