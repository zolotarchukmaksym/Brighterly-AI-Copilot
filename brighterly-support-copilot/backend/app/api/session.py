from __future__ import annotations
from fastapi import APIRouter, HTTPException
from .. import db, security
from ..models import SessionRequest
from ..scenarios import SCENARIOS

router = APIRouter()


@router.post("/session")
def create_session(body: SessionRequest):
    u = db.get_user(body.user_id)
    if not u:
        raise HTTPException(404, "unknown_user")
    return {"token": security.make_token(u["user_id"], "customer"), "user": _user_card(u)}


@router.post("/session/agent")
def create_agent_session():
    # Demo simplification: the UI obtains the specialist role automatically. Production would use real staff SSO.
    return {"token": security.make_token(None, "agent"), "role": "agent", "name": "Support specialist"}


def _user_card(u: dict) -> dict:
    p = u["support_context"]["primary_demo_case"]
    return {"user_id": u["user_id"], "name": u["identity"]["full_name"], "account_type": u["account_type"], "scenario": p["case_type"],
            "suggested_message": p["user_message"], "account_status": u["account_status"]}


@router.get("/demo/users")
def demo_users():
    return {"users": [_user_card(u) for u in db.list_users()]}


@router.get("/demo/scenarios")
def demo_scenarios():
    out = []
    for s in SCENARIOS:
        u = db.get_user(s["user_id"])
        out.append({**s, "user_name": u["identity"]["full_name"], "account_type": u["account_type"]})
    return {"scenarios": out}
