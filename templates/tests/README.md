# tests/ — Prompt 15.1 RBAC test suite

## Ishga tushirish

```bash
pip install -r requirements.txt   # pytest va httpx allaqachon oxirida bor
pytest tests/ -v
```

Hech qanday qo'shimcha sozlash shart emas — `tests/conftest.py`:

- o'zining maxfiy kalitlarini (`CLINICFLOW_SECRET_KEY`,
  `CLINICFLOW_FIELD_KEY`, `CLINICFLOW_BLIND_INDEX_KEY`) avtomatik
  generatsiya qiladi (production `.env`ga TEGILMAYDI),
- `DATABASE_URL`ni vaqtinchalik (temp) SQLite fayliga yo'naltiradi
  (production `clinicflow.db` ISHLATILMAYDI, HECH QACHON o'zgartirilmaydi),
- har bir test funksiyasidan oldin sxemani (`Base.metadata`) qayta
  yaratadi — testlar bir-biridan mustaqil,
- slowapi rate-limiter hisoblagichini ham har bir testdan oldin
  tozalaydi (aks holda `/api/auth/login`ga turli testlardan qilingan
  chaqiruvlar bitta umumiy "5/minute" limitni bo'lishib, kutilmagan
  429 qaytarishi mumkin edi).

## Qamrov

| Fayl | Nima tekshiradi |
|---|---|
| `TestDatabaseSchema` | Yangi jadvallar (`GovIntegrationSettings`, `SecurityMessage`, `SystemError`, `LoginLog`, `AdminProfileSettings`), `Patient`ning yangi ustunlari, `PAYMENT_STATUSES` |
| `TestRBACLabResults` | `/lab-results/add` — doctor/lab_doctor muvaffaqiyatli (303 redirect, JSON emas — pastga qarang), cashier 403, login'siz 303→/login |
| `TestRBACPayments` | `/api/payments/{id}/cancel` (admin 200, cashier 403), `/api/payments/{id}/refund` (cashier 200, doctor 403) |
| `TestRBACPatientsAdmit` | `/api/patients/{id}/admit` (doctor/reception 200, cashier 403) |
| `TestRBACAdminProfile` | `/api/admin/profile` — GET admin+assistant_admin 200, PUT faqat admin |
| `TestRBACSecurityMessages` | `/security/messages` — POST doctor/lab_doctor, GET admin+assistant_admin |
| `TestDangerousAction` | `/settings/dangerous-action` — to'g'ri/noto'g'ri 2-qatlam parol, admin bo'lmagan rol, `CLINICFLOW_ADMIN_ACTIONS_PASSWORD` o'rnatilmagan holat (500) |
| `TestBackupCheckPath` | `/admin/backup/check-path` — **asosiy tuzatish**: mavjud bo'lmagan yo'l endi 400 qaytaradi |
| `TestLoginSecurityAlerts` | 3 ketma-ket muvaffaqiyatsiz login → avtomatik `SecurityMessage` (priority=high) |

## Bilib qo'yish kerak bo'lgan 2 ta ataylab qoldirilgan nomuvofiqlik

1. **`/lab-results/add`** — talabnomada "200 JSON" deyilgan, lekin bu
   endpoint HTML-frontend uchun forma-based (redirect + flash-error).
   Muvaffaqiyatli so'rov ham `303`ni qaytaradi. Buni o'zgartirish
   `templates/lab_results.html`dagi mavjud JS'ni buzadi, shuning uchun
   testlar HAQIQIY xatti-harakatni tasdiqlaydi.
2. **`PAYMENT_STATUSES`** — aniq `"pending_refund"` literali yo'q;
   bu holat `status="cancelled"` orqali ifodalanadi. `test_payment_statuses_updated`
   shu joriy holatni hujjatlashtiradi.
