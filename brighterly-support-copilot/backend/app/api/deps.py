from __future__ import annotations
from fastapi import Header, HTTPException
from .. import security


def _tok(authorization: str | None) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing_token")
    d = security.read_token(authorization[7:])
    if not d:
        raise HTTPException(401, "invalid_token")
    return d


def customer(authorization: str | None = Header(default=None)) -> str:
    d = _tok(authorization)
    if d["role"] != "customer" or not d.get("user_id"):
        raise HTTPException(403, "customer_role_required")
    return d["user_id"]


def agent(authorization: str | None = Header(default=None)) -> str:
    d = _tok(authorization)
    if d["role"] != "agent":
        raise HTTPException(403, "agent_role_required")
    return "agent"
