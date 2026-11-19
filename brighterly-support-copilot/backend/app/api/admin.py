from __future__ import annotations
from fastapi import APIRouter, Header, HTTPException
from .. import clock, db, security
from ..config import settings

router = APIRouter()


def _admin(x_admin_token: str | None, authorization: str | None = None):
    """Admin = the ADMIN_TOKEN header, or (demo convenience for the UI's Reset button) a signed specialist session."""
    if x_admin_token == settings.admin_token:
        return
    if authorization and authorization.lower().startswith("bearer "):
        d = security.read_token(authorization[7:])
        if d and d.get("role") == "agent":
            return
    raise HTTPException(403, "admin_token_required")


@router.post("/admin/reset")
def reset(x_admin_token: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    _admin(x_admin_token, authorization)
    db.reset()
    return {"ok": True}


@router.get("/admin/config")
def config():
    return {"provider": settings.llm_provider, "model_fast": settings.model_fast if settings.llm_provider == "gemma" else "mock",
            "model_strong": settings.model_strong if settings.llm_provider == "gemma" else "mock", "demo_now": clock.now_iso(), "prompt_version": settings.prompt_version}


@router.get("/admin/audit")
def audit(limit: int = 100, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    return {"audit": db.audit_log()[-limit:]}


@router.get("/health")
def health():
    return {"ok": True, "provider": settings.llm_provider}
