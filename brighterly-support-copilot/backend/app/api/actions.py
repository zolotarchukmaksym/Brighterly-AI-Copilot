from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from .. import clock, db
from ..models import RejectRequest
from ..pipeline import events as E
from ..tools import action_tools
from .chat import _find_run, refresh_status
from .deps import agent

router = APIRouter()


@router.get("/actions")
def list_actions(_: str = Depends(agent)):
    return {"actions": db.pending_actions()}


def _decide(action_id: str, approve: bool, reason: str | None):
    a0 = db.get_pending(action_id)
    if not a0:
        raise HTTPException(404, "action_not_found")
    r = action_tools.decide_action(action_id, approve, reason)
    convo = db.get_conversation(a0["conversation_id"]) if a0.get("conversation_id") else None
    run_id = _find_run(convo, "action_id", action_id) if convo else "RUN-0"
    evs = []
    if not r.get("ok"):
        g = r.get("guardrail")
        if g:
            evs.append(E.make(run_id, "guardrail", "blocked", "BLOCKED: " + g["rule"], g["reason"], g, 0, "human_approval"))
        return {"ok": False, "error": r.get("error"), "events": evs}
    if r.get("guardrail"):
        g = r["guardrail"]
        evs.append(E.make(run_id, "guardrail", "done", "ALLOWED: " + g["rule"], g["reason"], g, 0, "human_approval"))
    act = r["action"]
    evs.append(E.make(run_id, "human_decision", "done", "Specialist " + ("approved" if approve else "rejected") + f" {act['type']}",
                      (reason or "Approved after review") , {"action_id": action_id, "decision": r["status"], "decided_by": "Support specialist", "reason": reason, "simulated_effect": r["status"] == "approved"}, 0, "human_approval"))
    status = None
    if convo:
        convo["messages"].append({"role": "specialist", "text": r["customer_message"], "ts": clock.now_iso(), "run_id": run_id, "cards": []})
        convo["events"] += evs
        status = refresh_status(convo)
    return {"ok": True, "status": r["status"], "events": evs, "customer_message": {"role": "specialist", "text": r["customer_message"]}, "action": act, "conversation_status": status,
            "conversation_id": a0.get("conversation_id")}


@router.post("/actions/{action_id}/approve")
def approve(action_id: str, _: str = Depends(agent)):
    return _decide(action_id, True, None)


@router.post("/actions/{action_id}/reject")
def reject(action_id: str, body: RejectRequest, _: str = Depends(agent)):
    return _decide(action_id, False, body.reason)
