# modules/audit_module.py
"""
4-BAND: Audit jurnalni ko'rish API'si — FAQAT admin uchun.

Yozish tomoni (log_action) audit.py'da, har bir write-endpointda alohida
chaqiriladi. Bu modul esa faqat O'QISH uchun — /admin/audit-log sahifasi
shu yerdan ma'lumot oladi.
"""
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

import models
import schemas
from auth import require_role
from database import get_db

router = APIRouter(
    prefix="/api/admin/audit-log",
    tags=["Admin / Audit"],
    dependencies=[Depends(require_role("admin"))],
)


@router.get("", response_model=schemas.AuditLogPage)
def list_audit_log(
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    action: Optional[str] = Query(None, description="Masalan: payment.add"),
    entity_type: Optional[str] = Query(None, description="Masalan: Payment"),
) -> schemas.AuditLogPage:
    query = db.query(models.AuditLog)
    if action:
        query = query.filter(models.AuditLog.action == action)
    if entity_type:
        query = query.filter(models.AuditLog.entity_type == entity_type)

    total = query.count()
    rows = (
        query.order_by(models.AuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return schemas.AuditLogPage(
        items=rows,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max((total + page_size - 1) // page_size, 1),
    )


def register_module() -> Dict[str, object]:
    return {
        "module_name": "AuditLog",
        "version": "1.0.0",
        "router": router,
    }
