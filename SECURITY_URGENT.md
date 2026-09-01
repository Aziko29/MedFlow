# 🔴 SHOSHILINCH: Kalitlarni almashtiring (rotate)

Oldingi arxivda haqiqiy `.env` va to'ldirilgan `clinicflow.db` bor edi. Ular ushbu
arxivdan olib tashlandi, lekin qiymatlar allaqachon sizning qo'lingizga (va
ehtimol boshqa joyga) tarqalgan bo'lishi mumkin — shuning uchun ularni **hozir
chiqib ketgan** deb hisoblang va quyidagilarni bajaring:

## 1. Kalitlarni yangilang

```bash
python -c "import secrets; print(secrets.token_hex(32))"   # CLINICFLOW_SECRET_KEY uchun
python -c "import secrets; print(secrets.token_hex(32))"   # CLINICFLOW_BLIND_INDEX_KEY uchun
```

- `CLINICFLOW_SECRET_KEY` — darhol yangilang. Bu sessiya tokenlarini imzolaydi;
  eski qiymat chiqqan bo'lsa, uni bilgan kishi istalgan foydalanuvchi nomidan
  sessiya token yasay oladi.
- `CLINICFLOW_BLIND_INDEX_KEY` — yangilang, lekin buni o'zgartirsangiz mavjud
  telefon-raqam qidiruv/duplikat-tekshiruv indekslarini qayta hisoblash kerak
  bo'ladi (pastga qarang).
- `CLINICFLOW_FIELD_KEY` — **DIQQAT**: buni oddiy o'zgartirsangiz, bazadagi
  BARCHA shifrlangan maydonlar (telefon, manzil, tibbiy eslatmalar) o'qilmay
  qoladi. To'g'ri yo'l: eski kalit bilan barcha yozuvlarni deshifrlab, yangi
  kalit bilan qayta shifrlash kerak (`crypto_fields.py` / `rotate_keys.py`
  shu ish uchun mavjud — ishga tushirishdan oldin bazadan zaxira nusxa oling).

## 2. Ishlatilgan hisobni tekshiring

`clinicflow.db` ichida `Aziko29` foydalanuvchisi (Argon2 xesh) bor edi.
- Shu foydalanuvchi parolini albatta yangilang.
- Audit logdan (`audit.py` / audit jadvali) shu hisob ostida so'nggi kunlarda
  kutilmagan harakat bo'lganini tekshiring.

## 3. Tarixdan tozalang

Agar bu fayllar biror joyga (git, chat, cloud disk, zip arxiv) yuborilgan
bo'lsa:
- Git tarixidan olib tashlang: `git rm --cached .env clinicflow.db` va kerak
  bo'lsa `git filter-repo` / BFG bilan tarixdan butunlay o'chiring.
- Fayl almashish xizmatlaridagi eski arxivlarni o'chiring.

## 4. Kelajakda oldini olish

- `.gitignore`da `.env` va `*.db` allaqachon bor — yaxshi. Lekin **zip
  arxiv qilishdan oldin** ham shu fayllarni qo'lda tekshirib chiqing, chunki
  zip gitignore'ga bo'ysunmaydi.
- Tavsiya: relizni tayyorlashda `git archive` dan foydalaning (u
  `.gitignore`dagi kuzatilmagan fayllarni avtomatik chiqarib tashlaydi) yoki
  arxivlashdan oldin `.env`/`*.db` borligini tekshiruvchi kichik skript
  qo'shing.

Ushbu fayl vazifasini bajargach o'chirib tashlashingiz mumkin.
