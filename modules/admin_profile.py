# modules/admin_profile.py
"""
Admin profili moduli (Prompt 8) — klinika haqida umumiy ma'lumotlar,
ish o'rinlari (positions), bo'sh lavozimlar va sohalar (departments).

Ruxsatlar:
  - Router darajasida: require_admin_or_assistant() — faqat admin va
    assistant_admin kira oladi, boshqa har qanday rol 403 oladi (bu
    dependencies=[...] BARCHA endpointlarga, jumladan kelajakda
    qo'shiladigan har qanday yangi GET'ga ham avtomatik qo'llanadi).
  - Yozish (POST/PUT/DELETE) endpointlarida QO'SHIMCHA
    require_role("admin") — assistant_admin shu operatsiyalarni chaqira
    olmaydi, faqat GET'lar unga ochiq (talab #5).

Saqlash (talab #6): klinika ma'lumotlari HAM, positions/departments HAM
bitta `admin_profile_settings` jadvalida (models.AdminProfileSettings)
saqlanadi — positions/departments alohida jadval EMAS, balki shu
jadvalning JSON ustunlaridagi ro'yxatlar (qarang: models.py'dagi
AdminProfileSettings docstring). Har bir element o'zining butun sonli
"id"siga ega (next_position_id / next_department_id orqali hisoblanadi),
bu esa PUT/DELETE /positions/{id} kabi endpointlar uchun ishlatiladi.
"""
import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import models
import schemas
from audit import log_action
from auth import get_current_user, require_admin_or_assistant, require_role
from database import get_db

router = APIRouter(
    prefix="/api/admin",
    tags=["Admin / Profil"],
    dependencies=[Depends(require_admin_or_assistant())],
)


# ── Yordamchi funksiyalar ────────────────────────────────────────────
def _get_settings(db: Session) -> models.AdminProfileSettings:
    """Yagona sozlamalar qatorini oladi, mavjud bo'lmasa yaratadi
    (GovIntegrationSettings bilan bir xil get-or-create naqsh)."""
    settings = db.query(models.AdminProfileSettings).first()
    if settings is None:
        settings = models.AdminProfileSettings(positions=[], departments=[])
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def _find_position(settings: models.AdminProfileSettings, position_id: int) -> dict:
    for item in settings.positions or []:
        if item.get("id") == position_id:
            return item
    raise HTTPException(status_code=404, detail="Ish o'rni topilmadi")


def _find_department(settings: models.AdminProfileSettings, department_id: int) -> dict:
    for item in settings.departments or []:
        if item.get("id") == department_id:
            return item
    raise HTTPException(status_code=404, detail="Soha topilmadi")


def _department_staff_count(settings: models.AdminProfileSettings, department_id: int) -> int:
    return sum(
        1
        for p in (settings.positions or [])
        if p.get("department_id") == department_id and p.get("is_occupied")
    )


def _department_read(settings: models.AdminProfileSettings, item: dict) -> schemas.DepartmentRead:
    return schemas.DepartmentRead(
        id=item["id"],
        name=item["name"],
        head_doctor_id=item.get("head_doctor_id"),
        is_active=item.get("is_active", True),
        staff_count=_department_staff_count(settings, item["id"]),
    )


# ── Klinika ma'lumotlari ──────────────────────────────────────────────
@router.get("/profile", response_model=schemas.AdminProfileRead)
def get_admin_profile(db: Session = Depends(get_db)) -> models.AdminProfileSettings:
    return _get_settings(db)


@router.put(
    "/profile",
    response_model=schemas.AdminProfileRead,
    dependencies=[Depends(require_role("admin"))],
)
def update_admin_profile(
    profile_data: schemas.AdminProfileUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> models.AdminProfileSettings:
    settings = _get_settings(db)
    for field, value in profile_data.model_dump().items():
        setattr(settings, field, value)
    settings.updated_at = datetime.datetime.utcnow()
    db.commit()
    db.refresh(settings)
    log_action(db, user, "admin_profile.update", "AdminProfileSettings", settings.id, "clinic info updated")
    return settings


# ── Ish o'rinlari (Positions) ─────────────────────────────────────────
# STATIK yo'l (/positions/vacant) dinamik /positions/{position_id}dan
# OLDIN e'lon qilinishi shart (aks holda "vacant" position_id sifatida
# talqin qilinib, 422 xatolik qaytaradi).
@router.get("/positions/vacant", response_model=List[schemas.PositionRead])
def list_vacant_positions(db: Session = Depends(get_db)) -> List[dict]:
    settings = _get_settings(db)
    return [p for p in (settings.positions or []) if not p.get("is_occupied")]


@router.get("/positions", response_model=List[schemas.PositionRead])
def list_positions(db: Session = Depends(get_db)) -> List[dict]:
    settings = _get_settings(db)
    return settings.positions or []


@router.post(
    "/positions",
    response_model=schemas.PositionRead,
    status_code=201,
    dependencies=[Depends(require_role("admin"))],
)
def add_position(
    position_data: schemas.PositionCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> dict:
    settings = _get_settings(db)

    if position_data.department_id is not None:
        _find_department(settings, position_data.department_id)

    new_id = settings.next_position_id
    now = datetime.datetime.utcnow()
    item = {
        "id": new_id,
        "title": position_data.title,
        "role": position_data.role,
        "specialty": position_data.specialty,
        "is_occupied": position_data.is_occupied,
        "salary_min": position_data.salary_min,
        "salary_max": position_data.salary_max,
        "requirements": position_data.requirements,
        "department_id": position_data.department_id,
        # Yangi lavozim band bo'lmasa, "bo'sh" hisobi shu paytdan boshlanadi.
        "vacant_since": None if position_data.is_occupied else now.isoformat(),
    }
    positions = list(settings.positions or [])
    positions.append(item)
    settings.positions = positions
    settings.next_position_id = new_id + 1
    db.commit()
    db.refresh(settings)

    log_action(db, user, "admin_profile.position_add", "Position", new_id, f"title={item['title']}")
    return item


@router.put(
    "/positions/{position_id}",
    response_model=schemas.PositionRead,
    dependencies=[Depends(require_role("admin"))],
)
def update_position(
    position_id: int,
    position_data: schemas.PositionUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> dict:
    settings = _get_settings(db)
    existing = _find_position(settings, position_id)

    if position_data.department_id is not None:
        _find_department(settings, position_data.department_id)

    was_occupied = bool(existing.get("is_occupied"))
    now_occupied = position_data.is_occupied

    # Band/bo'sh holati o'zgarganda vacant_since shunga mos yangilanadi
    # (bemorni palataga yotqizish/chiqarish bilan bir xil tamoyil):
    #   band -> bo'sh   : vacant_since = hozir
    #   bo'sh -> band   : vacant_since = None
    #   o'zgarmagan     : eski qiymat saqlanadi
    if was_occupied and not now_occupied:
        vacant_since = datetime.datetime.utcnow().isoformat()
    elif not was_occupied and now_occupied:
        vacant_since = None
    else:
        vacant_since = existing.get("vacant_since")

    updated = {
        "id": position_id,
        "title": position_data.title,
        "role": position_data.role,
        "specialty": position_data.specialty,
        "is_occupied": now_occupied,
        "salary_min": position_data.salary_min,
        "salary_max": position_data.salary_max,
        "requirements": position_data.requirements,
        "department_id": position_data.department_id,
        "vacant_since": vacant_since,
    }

    positions = [updated if p.get("id") == position_id else p for p in (settings.positions or [])]
    settings.positions = positions
    db.commit()
    db.refresh(settings)

    log_action(db, user, "admin_profile.position_update", "Position", position_id, f"title={updated['title']}")
    return updated


@router.delete(
    "/positions/{position_id}",
    status_code=204,
    dependencies=[Depends(require_role("admin"))],
)
def delete_position(
    position_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> None:
    settings = _get_settings(db)
    existing = _find_position(settings, position_id)  # 404 agar topilmasa

    positions = [p for p in (settings.positions or []) if p.get("id") != position_id]
    settings.positions = positions
    db.commit()

    log_action(db, user, "admin_profile.position_delete", "Position", position_id, f"title={existing.get('title')}")
    return None


# ── Sohalar (Departments) ─────────────────────────────────────────────
@router.get("/departments", response_model=List[schemas.DepartmentRead])
def list_departments(db: Session = Depends(get_db)) -> List[schemas.DepartmentRead]:
    settings = _get_settings(db)
    return [_department_read(settings, item) for item in (settings.departments or [])]


@router.post(
    "/departments",
    response_model=schemas.DepartmentRead,
    status_code=201,
    dependencies=[Depends(require_role("admin"))],
)
def add_department(
    department_data: schemas.DepartmentCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> schemas.DepartmentRead:
    settings = _get_settings(db)

    if department_data.head_doctor_id is not None:
        doctor = (
            db.query(models.Doctor)
            .filter(models.Doctor.id == department_data.head_doctor_id)
            .first()
        )
        if doctor is None:
            raise HTTPException(status_code=404, detail="Bosh shifokor (Doctor) topilmadi")

    new_id = settings.next_department_id
    item = {
        "id": new_id,
        "name": department_data.name,
        "head_doctor_id": department_data.head_doctor_id,
        "is_active": department_data.is_active,
    }
    departments = list(settings.departments or [])
    departments.append(item)
    settings.departments = departments
    settings.next_department_id = new_id + 1
    db.commit()
    db.refresh(settings)

    log_action(db, user, "admin_profile.department_add", "Department", new_id, f"name={item['name']}")
    return _department_read(settings, item)


@router.put(
    "/departments/{department_id}",
    response_model=schemas.DepartmentRead,
    dependencies=[Depends(require_role("admin"))],
)
def update_department(
    department_id: int,
    department_data: schemas.DepartmentUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> schemas.DepartmentRead:
    settings = _get_settings(db)
    _find_department(settings, department_id)  # 404 agar topilmasa

    if department_data.head_doctor_id is not None:
        doctor = (
            db.query(models.Doctor)
            .filter(models.Doctor.id == department_data.head_doctor_id)
            .first()
        )
        if doctor is None:
            raise HTTPException(status_code=404, detail="Bosh shifokor (Doctor) topilmadi")

    updated = {
        "id": department_id,
        "name": department_data.name,
        "head_doctor_id": department_data.head_doctor_id,
        "is_active": department_data.is_active,
    }
    departments = [
        updated if d.get("id") == department_id else d for d in (settings.departments or [])
    ]
    settings.departments = departments
    db.commit()
    db.refresh(settings)

    log_action(db, user, "admin_profile.department_update", "Department", department_id, f"name={updated['name']}")
    return _department_read(settings, updated)


def register_module() -> Dict[str, object]:
    return {
        "module_name": "AdminProfile",
        "version": "1.0.0",
        "router": router,
    }
