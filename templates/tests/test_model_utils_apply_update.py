# tests/test_model_utils_apply_update.py
"""
PROMPT 4 — generic `apply_update()` yordamchi funksiyasi uchun testlar.

Asosiy talab: apply_update() faqat so'rovda (payload'da) AYNAN berilgan
maydonlarni targetga yozadi; berilmagan maydonlar (Pydantic
model_fields_set'ga kirmagan, ya'ni FastAPI so'rov tanasida umuman
kelmagan maydonlar) o'zgarishsiz qoladi.
"""
import pytest
from pydantic import BaseModel
from typing import Optional

import schemas
from model_utils import apply_update


# ── Yordamchi: SQLAlchemy modelini simulyatsiya qiluvchi oddiy obyekt ──
class _DummyTarget:
    """SQLAlchemy model o'rnida — apply_update() faqat setattr() bilan
    ishlagani uchun bu yetarli, haqiqiy DB kerak emas."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class _DummySchema(BaseModel):
    a: Optional[str] = None
    b: Optional[int] = None
    c: Optional[str] = None


class TestApplyUpdateOnlyTouchesGivenFields:
    def test_unset_fields_are_left_untouched(self):
        target = _DummyTarget(a="orig_a", b=1, c="orig_c")
        # Faqat 'a' berilgan — 'b' va 'c' payload konstruktoriga
        # UMUMAN uzatilmagan (ya'ni so'rovda kelmagan deb hisoblanadi).
        payload = _DummySchema(a="new_a")

        apply_update(target, payload)

        assert target.a == "new_a"       # berilgan maydon yangilandi
        assert target.b == 1             # berilmagan — o'zgarmadi
        assert target.c == "orig_c"      # berilmagan — o'zgarmadi

    def test_explicit_none_is_still_applied_when_field_is_set(self):
        """Agar maydon so'rovda AYNAN berilgan bo'lsa (garchi qiymati
        None bo'lsa ham), u baribir yangilanishi kerak — exclude_unset
        faqat "umuman kelmagan" maydonlarni chetlab o'tadi, "kelib,
        qiymati None bo'lgan" maydonlarni emas."""
        target = _DummyTarget(a="orig_a", b=1, c="orig_c")
        payload = _DummySchema(a=None, b=2)  # a va b ikkalasi ham "set"

        apply_update(target, payload)

        assert target.a is None
        assert target.b == 2
        assert target.c == "orig_c"  # hamon berilmagan

    def test_multiple_fields_update_together(self):
        target = _DummyTarget(a="orig_a", b=1, c="orig_c")
        payload = _DummySchema(a="new_a", b=99, c="new_c")

        apply_update(target, payload)

        assert (target.a, target.b, target.c) == ("new_a", 99, "new_c")

    def test_no_fields_given_leaves_target_fully_unchanged(self):
        target = _DummyTarget(a="orig_a", b=1, c="orig_c")
        payload = _DummySchema()  # hech narsa berilmagan

        apply_update(target, payload)

        assert (target.a, target.b, target.c) == ("orig_a", 1, "orig_c")


class TestApplyUpdateExcludeUnsetFalse:
    def test_exclude_unset_false_overwrites_everything_with_defaults(self):
        """exclude_unset=False — to'liq PUT/almashtirish semantikasi:
        schema'dagi berilmagan maydonlar HAM o'z default qiymati
        (odatda None) bilan targetga yoziladi."""
        target = _DummyTarget(a="orig_a", b=1, c="orig_c")
        payload = _DummySchema(a="new_a")

        apply_update(target, payload, exclude_unset=False)

        assert target.a == "new_a"
        assert target.b is None   # default bilan ustidan yozildi
        assert target.c is None   # default bilan ustidan yozildi


class TestApplyUpdateExcludeParam:
    def test_excluded_field_names_are_never_touched(self):
        """`exclude` orqali ro'yxatga olingan maydonlar (masalan,
        alohida validatsiyadan o'tadigan doctor_id) apply_update()
        tomonidan hech qachon yozilmaydi — chaqiruvchi ularni o'zi
        qo'lda o'rnatadi."""
        target = _DummyTarget(a="orig_a", b=1, c="orig_c")
        payload = _DummySchema(a="new_a", b=2, c="new_c")

        apply_update(target, payload, exclude={"b", "c"})

        assert target.a == "new_a"
        assert target.b == 1        # exclude qilingan — o'zgarmadi
        assert target.c == "orig_c"  # exclude qilingan — o'zgarmadi


class TestApplyUpdateSkipsUnknownAttributes:
    def test_field_not_present_on_target_is_silently_skipped(self):
        """Payload'da bor, lekin target obyektida yo'q maydon xatosiz
        chetlab o'tiladi (target'ga tasodifiy yangi atribut
        qo'shilmaydi)."""
        class _NarrowTarget:
            def __init__(self):
                self.a = "orig_a"

        target = _NarrowTarget()
        payload = _DummySchema(a="new_a", b=5)  # target'da 'b' yo'q

        apply_update(target, payload)

        assert target.a == "new_a"
        assert not hasattr(target, "b")

    def test_rejects_non_basemodel_payload(self):
        target = _DummyTarget(a="orig_a")
        with pytest.raises(TypeError):
            apply_update(target, {"a": "new_a"})  # dict, Pydantic model emas


class TestApplyUpdateWithRealProjectSchema:
    """Loyihaning haqiqiy `schemas.PatientUpdate`si bilan — apply_update()
    real update_patient() ssenariysida ham faqat berilgan maydonlarni
    yangilashini tasdiqlaydi."""

    def test_partial_patient_update_preserves_unset_fields(self):
        target = _DummyTarget(
            fullname="Eski Ism",
            phone="+998901112233",
            gender="M",
            birth_date=None,
            address="Eski manzil",
            medical_notes="eski izoh",
            blood_type="A+",
            emergency_contact_name="Ona",
            emergency_contact_phone="+998904445566",
        )

        # Faqat fullname va blood_type berilgan (majburiy `phone` ham
        # birga yuboriladi — PatientUpdate uni talab qiladi), qolgan
        # ixtiyoriy maydonlar so'rovda umuman kelmagan deb hisoblanadi.
        payload = schemas.PatientUpdate(
            fullname="Yangi Ism",
            phone="+998901112233",
            blood_type="B+",
        )

        apply_update(target, payload)

        assert target.fullname == "Yangi Ism"
        assert target.blood_type == "B+"
        # Quyidagilar so'rovda berilmagan — o'zgarishsiz qolishi kerak:
        assert target.gender == "M"
        assert target.address == "Eski manzil"
        assert target.medical_notes == "eski izoh"
        assert target.emergency_contact_name == "Ona"
        assert target.emergency_contact_phone == "+998904445566"
