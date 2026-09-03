# model_utils.py
"""
Prompt 4 — Modellarni update qilish uchun GENERIC yordamchi funksiya.

Muammo: update_user, update_patient va shunga o'xshash funksiyalarda
har bir maydon qo'lda (`target.fullname = payload.fullname`, ...)
yozilgan edi. Schema'ga yangi maydon qo'shilganda, lekin update
funksiyasidagi qo'lda yozilgan ro'yxatni yangilashni kimdir unutib
qo'ysa — o'sha maydon "jimgina" hech qachon yangilanmay qoladi va bu
xatoni topish qiyin (test yozilmasa, sezilmasdan qoladi).

Yechim: `apply_update()` — Pydantic schema (payload)dagi maydonlarni
SQLAlchemy model obyektiga (target) avtomatik va xavfsiz o'tkazadi,
shuning uchun qo'lda field-by-field yozishga umuman ehtiyoj qolmaydi.
"""
from typing import Any, Iterable, Optional, TypeVar

from pydantic import BaseModel

ModelT = TypeVar("ModelT")


def apply_update(
    target: ModelT,
    payload: BaseModel,
    exclude_unset: bool = True,
    *,
    exclude: Optional[Iterable[str]] = None,
) -> ModelT:
    """`payload`dagi maydonlarni `target`ga o'tkazadi va `target`ni
    qaytaradi (chaqiruvchi keyin xohlasa `db.commit()/db.refresh()`
    qiladi — bu funksiya DB bilan ishlamaydi, faqat obyekt
    atributlarini o'rnatadi).

    Parametrlar:
        target: SQLAlchemy model obyekti (allaqachon DB'dan olingan
            yoki `db.add()` qilingan yangi obyekt).
        payload: Pydantic BaseModel (masalan `schemas.UserUpdate`).
        exclude_unset: True (standart) bo'lsa, so'rovda AYNAN
            berilmagan maydonlar (masalan PATCH'da yozilmagan yoki
            model_fields_set'ga kirmagan maydonlar) targetga umuman
            tegilmaydi — mavjud qiymat o'zgarishsiz qoladi. Bu xuddi
            shu Promptning asosiy talabi: "berilmagan maydonlarni
            o'zgartirmasdan qoldirish". False bo'lsa, schema'dagi
            BARCHA maydonlar (shu jumladan None qiymatlar ham) targetga
            yoziladi — to'liq PUT/almashtirish semantikasi.
        exclude: target'da qo'lda/alohida validatsiyadan so'ng
            o'rnatiladigan maydon nomlari (masalan parol yoki
            bog'liq-ID'lar) — bu funksiya ularga tegmaydi, chaqiruvchi
            keyinroq o'zi o'rnatadi.

    Xavfsizlik: `target`da mavjud bo'lmagan (hasattr() False qaytaradigan)
    har qanday maydon sukut bo'yicha o'tkazib yuboriladi — shuning
    uchun schema'dagi begona/ortiqcha maydon modelga tasodifan yangi
    (DB ustuniga bog'liq bo'lmagan) Python atributi sifatida yozilib
    ketmaydi.
    """
    if not isinstance(payload, BaseModel):
        raise TypeError(
            f"apply_update(): payload Pydantic BaseModel bo'lishi kerak, "
            f"olindi: {type(payload).__name__}"
        )

    data: dict[str, Any] = payload.model_dump(exclude_unset=exclude_unset)

    exclude_set = set(exclude) if exclude else set()
    for field, value in data.items():
        if field in exclude_set:
            continue
        if not hasattr(target, field):
            continue
        setattr(target, field, value)

    return target
