"""
🔬 Lab Results Module — Tibbiy tahlil va diagnostika natijalarini boshqarish.

YANGI: erkin matn o'rniga 20 ta STANDART TAHLIL SHABLONI (lab_templates.py)
ishlatiladi. Shifokor tahlil turini tanlaydi -> barcha ko'rsatkichlar
me'yoriy (standart) qiymat bilan oldindan to'ldirilgan holda chiqadi ->
faqat o'zgargan ko'rsatkichlarni tahrirlaydi. Bu ishni tezlashtiradi va
ko'rsatkichni noldan qo'lda kiritishda yo'l qo'yiladigan xatolarni
kamaytiradi. Natija DB'da mavjud `result_data` (String) ustuniga JSON
sifatida saqlanadi — DB sxemasini o'zgartirish (ustun qo'shish) shart
emas, shu bilan eski bazalarda ham xatosiz ishlaydi.
"""
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

import models
import lab_templates
from database import get_db
from auth import get_current_user_optional
from audit import log_action

router = APIRouter(prefix="/lab-results", tags=["Lab Results"])
templates = Jinja2Templates(directory="templates")

# Prompt 5: modulga faqat doctor va lab_doctor kirita/o'chiradi.
# admin faqat ko'rishi mumkin (VIEW_ROLES'da bor, CREATE_ROLES'da yo'q).
# cashier, reception, assistant_admin — modulga UMUMAN kira olmaydi.
VIEW_ROLES = ("admin", "doctor", "lab_doctor")
CREATE_ROLES = ("doctor", "lab_doctor")
# Prompt 18: TAHRIRLASH uchun admin ham ruxsat etiladi (boshqa faol
# shifokor nomidan tahrirlash imkoniyati bilan) — lekin YARATISH va
# O'CHIRISH hamon faqat CREATE_ROLES (doctor/lab_doctor) uchun qoladi.
EDIT_ROLES = CREATE_ROLES + ("admin",)


def _ctx(request: Request, user: models.User, active_page: str, extra: Optional[dict] = None) -> dict:
    base = {
        "request": request,
        "active_page": active_page,
        "current_user": user,
        "app_version": "3.0.0",
        "year": datetime.now().year,
    }
    if extra:
        base.update(extra)
    return base


def _parse_result_data(raw: str) -> Optional[dict]:
    """Saqlangan result_data'ni xavfsiz JSON qilib o'qiydi. Eski (shablon
    joriy etilishidan oldingi) yozuvlar oddiy erkin matn bo'lishi mumkin —
    bunday holda None qaytariladi va chaqiruvchi tomon xom matnni
    ko'rsatadi, dastur qulamaydi."""
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "indicators" in data:
            return data
    except (TypeError, ValueError):
        pass
    return None


@router.get("/", response_class=HTMLResponse)
def list_lab_results(
    request: Request,
    search: Optional[str] = None,
    patient_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """Barcha tahlil natijalari ro'yxati (qidiruv va filtrlar bilan)."""
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    # ⬅️ Prompt 5: cashier/reception/assistant_admin bu bo'limga UMUMAN
    # kira olmaydi — to'g'ridan-to'g'ri URL orqali ham 403 qaytadi
    # (sidebar'da yashirilgan bo'lishi yetarli emas).
    if user.role not in VIEW_ROLES:
        raise HTTPException(status_code=403, detail="Bu bo'limga kirish huquqingiz yo'q")

    query = db.query(models.LabResult)

    if search:
        search_filter = f"%{search}%"
        query = query.filter(models.LabResult.test_name.ilike(search_filter))

    if patient_id:
        query = query.filter(models.LabResult.patient_id == patient_id)

    # lab_doctor barcha tahlil so'rovlarini ko'radi (bu model faqat
    # laboratoriya natijalarini saqlaydi, shu bilan talab #5 avtomatik
    # bajariladi) — o'ziga tegishli bo'lmagan yozuvlarni ham ko'rish
    # kerak, chunki kim kiritishi kerakligini shundan bilishadi. Tahrirlash
    # va o'chirish esa pastda alohida (faqat o'ziga tegishli) cheklangan.

    results = query.order_by(models.LabResult.created_at.desc()).all()
    patients = db.query(models.Patient).all()
    doctors = db.query(models.Doctor).filter(models.Doctor.is_active == True).all()

    # Har bir natija uchun ko'rsatiladigan "view model" tayyorlaymiz:
    # JSON shablon natijasi bo'lsa — ko'rsatkichlar + necha tasi
    # me'yordan chetga chiqqani; eski erkin-matn yozuv bo'lsa — xom matn.
    rows = []
    for r in results:
        parsed = _parse_result_data(r.result_data)
        rows.append({
            "obj": r,
            "parsed": parsed,
            "abnormal_count": parsed.get("abnormal_count", 0) if parsed else None,
            "raw_text": None if parsed else r.result_data,
        })

    return templates.TemplateResponse(
        request=request,
        name="lab_results.html",
        context=_ctx(request, user, "lab_results", {
            "rows": rows,
            "patients": patients,
            "doctors": doctors,
            "search": search or "",
            "selected_patient": patient_id or "",
            "lab_templates_list": lab_templates.list_templates(),
            "lab_templates_map": lab_templates.LAB_TEMPLATES,
        })
    )


def _redirect_with_error(message: str):
    from urllib.parse import quote
    return RedirectResponse(url=f"/lab-results/?form_error={quote(message)}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/add")
def add_lab_result(
    request: Request,
    patient_id: int = Form(...),
    doctor_id: Optional[int] = Form(None),
    template_key: str = Form(...),
    indicators_json: str = Form("{}"),
    note: str = Form(""),
    status_val: str = Form("Tayyor"),
    db: Session = Depends(get_db)
):
    """Yangi tahlil natijasini qo'shish (Faqat Admin, Doctor yoki Reception).
    Ko'rsatkichlar shablon bo'yicha VALIDATSIYA qilinadi va bayroqlar
    (past/yuqori/me'yorda) serverda qayta hisoblanadi — brauzerdan
    kelgan hech qanday hisob-kitobga ishonilmaydi."""
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    if user.role not in CREATE_ROLES:
        raise HTTPException(status_code=403, detail="Bu amal uchun ruxsatingiz yo'q")

    # ⬅️ Prompt 5: doctor va lab_doctor natijani faqat O'Z nomidan kirita
    # oladi — formadan boshqa shifokor tanlangan bo'lsa ham e'tiborsiz
    # qoldiriladi (boshqa shifokor nomidan yozuv qo'shib bo'lmaydi).
    doctor_id = user.doctor_id

    patient = db.query(models.Patient).filter(models.Patient.id == patient_id).first()
    if not patient:
        return _redirect_with_error("Bemor topilmadi. Ro'yxatdan bemorni tanlang.")

    try:
        submitted = json.loads(indicators_json)
        if not isinstance(submitted, dict):
            raise ValueError("Ko'rsatkichlar noto'g'ri formatda yuborildi.")
    except (TypeError, ValueError):
        return _redirect_with_error("Ko'rsatkichlar noto'g'ri formatda yuborildi. Qaytadan urinib ko'ring.")

    try:
        payload = lab_templates.build_result_payload(template_key, submitted, note)
    except ValueError as exc:
        return _redirect_with_error(str(exc))

    if status_val not in ("Tayyor", "Kutilmoqda"):
        status_val = "Tayyor"

    try:
        new_result = models.LabResult(
            patient_id=patient_id,
            doctor_id=doctor_id if doctor_id and doctor_id != 0 else None,
            test_name=payload["template_name"],
            result_data=json.dumps(payload, ensure_ascii=False),
            status=status_val,
        )
        db.add(new_result)
        db.commit()
        db.refresh(new_result)

        log_action(
            db, user, "lab_result.add", "LabResult", new_result.id,
            f"Bemor #{patient_id} uchun '{payload['template_name']}' tahlili qo'shildi "
            f"({payload['abnormal_count']} ta me'yordan chetga chiqqan ko'rsatkich).",
        )
    except Exception:
        db.rollback()
        return _redirect_with_error("Saqlashda kutilmagan xatolik yuz berdi. Qaytadan urinib ko'ring.")

    return RedirectResponse(url="/lab-results", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/edit/{result_id}")
def edit_lab_result(
    request: Request,
    result_id: int,
    doctor_id: Optional[int] = Form(None),
    indicators_json: str = Form("{}"),
    note: str = Form(""),
    status_val: str = Form("Tayyor"),
    db: Session = Depends(get_db)
):
    """Mavjud tahlil natijasini tahrirlash (ko'rsatkichlarni tuzatish).
    Tahlil turi (shablon) va bemor o'zgartirilmaydi — faqat qiymatlar,
    izoh, mas'ul shifokor va holat.

    ⬅️ Prompt 18: ikki xil rol, ikki xil huquq bilan bu yerga kirishi
    mumkin endi:
      - doctor / lab_doctor: FAQAT o'ziga biriktirilgan natijani
        tahrirlaydi, mas'ul shifokorni o'zgartira olmaydi — Form
        orqali kelgan doctor_id e'tiborga olinmaydi (Prompt 5'dagi
        himoya o'zgarishsiz saqlanadi).
      - admin: istalgan natijani, istalgan FAOL shifokor nomidan
        tahrirlashi mumkin — Form orqali kelgan doctor_id endi
        haqiqatan ishlatiladi (avval bu qiymat har doim e'tiborsiz
        qoldirilib, mantiqsiz Form parametriga aylangan edi)."""
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    if user.role not in EDIT_ROLES:
        raise HTTPException(status_code=403, detail="Bu amal uchun ruxsatingiz yo'q")

    result = db.query(models.LabResult).filter(models.LabResult.id == result_id).first()
    if not result:
        return _redirect_with_error("Tahlil natijasi topilmadi.")

    if user.role == "admin":
        # Admin — Form orqali kelgan doctor_id haqiqiy, FAOL shifokorga
        # tegishli bo'lishi kerak (yoki "mas'ul shifokorsiz" holat
        # uchun bo'sh/0 qoldirilishi mumkin).
        if doctor_id and doctor_id != 0:
            target_doctor = (
                db.query(models.Doctor)
                .filter(models.Doctor.id == doctor_id, models.Doctor.is_active.is_(True))
                .first()
            )
            if not target_doctor:
                return _redirect_with_error(
                    "Ko'rsatilgan shifokor topilmadi yoki faol emas."
                )
        else:
            doctor_id = None
    else:
        # ⬅️ Prompt 5: doctor va lab_doctor faqat O'ZIGA biriktirilgan
        # natijani tahrirlaydi va shifokorni boshqasiga o'zgartira olmaydi.
        if result.doctor_id != user.doctor_id:
            raise HTTPException(status_code=403, detail="Bu tahlil natijasi sizga biriktirilmagan")
        doctor_id = user.doctor_id

    parsed = _parse_result_data(result.result_data)
    if not parsed:
        return _redirect_with_error("Bu eski (shablonsiz) yozuvni tahrirlab bo'lmaydi.")

    try:
        submitted = json.loads(indicators_json)
        if not isinstance(submitted, dict):
            raise ValueError("Ko'rsatkichlar noto'g'ri formatda yuborildi.")
    except (TypeError, ValueError):
        return _redirect_with_error("Ko'rsatkichlar noto'g'ri formatda yuborildi. Qaytadan urinib ko'ring.")

    try:
        payload = lab_templates.build_result_payload(parsed["template_key"], submitted, note)
    except ValueError as exc:
        return _redirect_with_error(str(exc))

    if status_val not in ("Tayyor", "Kutilmoqda"):
        status_val = "Tayyor"

    try:
        result.doctor_id = doctor_id if doctor_id and doctor_id != 0 else None
        result.result_data = json.dumps(payload, ensure_ascii=False)
        result.status = status_val
        db.commit()

        log_action(
            db, user, "lab_result.edit", "LabResult", result.id,
            f"'{payload['template_name']}' tahlili tahrirlandi "
            f"({payload['abnormal_count']} ta me'yordan chetga chiqqan ko'rsatkich).",
        )
    except Exception:
        db.rollback()
        return _redirect_with_error("Saqlashda kutilmagan xatolik yuz berdi. Qaytadan urinib ko'ring.")

    return RedirectResponse(url="/lab-results", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/delete/{result_id}")
def delete_lab_result(
    request: Request,
    result_id: int,
    db: Session = Depends(get_db)
):
    """Tahlil natijasini o'chirish — faqat doctor/lab_doctor, va faqat
    o'zi kiritgan natijani (Prompt 5, talab #1 va #3)."""
    user = get_current_user_optional(request.cookies.get("cf_session"), db)
    if not user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    if user.role not in CREATE_ROLES:
        raise HTTPException(status_code=403, detail="Bu amal uchun ruxsatingiz yo'q")

    result = db.query(models.LabResult).filter(models.LabResult.id == result_id).first()
    if not result:
        return _redirect_with_error("Tahlil topilmadi.")

    if result.doctor_id != user.doctor_id:
        raise HTTPException(status_code=403, detail="Bu tahlil natijasi sizga biriktirilmagan")

    test_name = result.test_name
    db.delete(result)
    db.commit()

    log_action(
        db, user, "lab_result.delete", "LabResult", result_id,
        f"'{test_name}' tahlil natijasi o'chirildi.",
    )

    return RedirectResponse(url="/lab-results", status_code=status.HTTP_303_SEE_OTHER)


# MedFlow dvigateli uchun majburiy ro'yxatdan o'tkazish funksiyasi
def register_module():
    return {
        "module_name": "Lab Results",
        "version": "3.0.0",
        "router": router
    }
