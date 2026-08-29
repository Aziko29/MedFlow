# audit.py
"""
4-BAND: Audit jurnal — "kim, qachon, nima qildi".

Bu fayl faqat BITTA yordamchi funksiya beradi — log_action() — barcha
write (yozuvchi) endpointlar shu funksiyani muvaffaqiyatli db.commit()'dan
KEYIN chaqiradi. Yozuv hech qachon o'chirilmaydi/tahrirlanmaydi, faqat
qo'shiladi — bu haqiqiy audit iz bo'lishi uchun shart.

Ishlatilishi (masalan modules/payments.py'da):

    from audit import log_action

    db.commit()
    log_action(db, current_user, "payment.add", "Payment", new_payment.id,
               f"amount={new_payment.amount}, appointment_id={appointment.id}")
"""
from typing import Optional

from sqlalchemy.orm import Session

import models


def log_action(
    db: Session,
    user: Optional[models.User],
    action: str,
    entity_type: str,
    entity_id: Optional[int] = None,
    details: Optional[str] = None,
) -> None:
    """Bitta audit qatorini yozadi va DARHOL commit qiladi.

    MUHIM: bu funksiya o'zining alohida commit()ini qiladi — asosiy
    write-amal (masalan to'lov qo'shish) allaqachon muvaffaqiyatli
    commit bo'lgandan KEYIN chaqirilishi kerak. Shunda audit yozuvining
    muvaffaqiyatsizligi (masalan disk to'lib qolsa) asosiy amalni
    ortga qaytarmaydi — audit ikkinchi darajali, asosiy amal esa
    birinchi navbatda muvaffaqiyatli bo'lishi kerak.
    """
    entry = models.AuditLog(
        user_id=user.id if user else None,
        username=user.username if user else "tizim",
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
    )
    db.add(entry)
    db.commit()
