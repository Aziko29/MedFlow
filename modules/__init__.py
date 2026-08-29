# modules/__init__.py
"""
ClinicFlow Modules Package.

Every module in this package must expose a `register_module()` function
that returns a dict with EXACTLY these three keys:

    {
        "module_name": str,          # human-readable name, e.g. "Patients"
        "version": str,               # e.g. "1.1.0"
        "router": fastapi.APIRouter,  # a real APIRouter, already configured
                                       # with its own prefix + tags
    }

`main.py`'s ClinicFlowEngine validates this contract at import time and
mounts `router` via `app.include_router(router)`. Modules must NOT return
raw route-function dicts ("routes": {...}) — that legacy shape caused the
old dynamic engine to silently drop every APIRouter-based module. There is
exactly one contract, enforced in one place.
"""
from typing import Any, Dict, Optional


def success_response(data: Any = None, message: str = "Muvaffaqiyatli") -> Dict[str, Any]:
    """Standart muvaffaqiyatli API javobi formati (ixtiyoriy yordamchi)."""
    response: Dict[str, Any] = {"status": "success", "message": message}
    if data is not None:
        response["data"] = data
    return response


def error_response(message: str, code: int = 400, details: Optional[Any] = None) -> Dict[str, Any]:
    """Standart xatolik API javobi formati (ixtiyoriy yordamchi)."""
    response: Dict[str, Any] = {"status": "error", "message": message, "error_code": code}
    if details:
        response["details"] = details
    return response
