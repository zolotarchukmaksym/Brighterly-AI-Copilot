from __future__ import annotations
import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from .. import clock, db, security
from ..models import ChatRequest, ConfirmRequest
from ..pipeline import events as E, orchestrator
from ..pipeline.qa import run_qa
from ..tools import action_tools
from .deps import customer

router = APIRouter()


def _guard(user_id: str, body: ChatRequest):
    if security.rate_limited("chat:" + user_id):
        raise HTTPException(429, "rate_limited")
    if body.conversation_id:
        c = db.get_conversation(body.conversation_id)
        if not c or c["user_id"] != user_id:
            raise HTTPException(404, "conversation_not_found")
        if c.get("closed"):
            raise HTTPException(409, "conversation_closed")


@router.post("/chat/stream")
def chat_stream(body: ChatRequest, user_id: str = Depends(customer)):
    _guard(user_id, body)

    def gen():
        for e in orchestrator.stream(user_id, body.conversation_id, body.message):
            yield json.dumps(e, ensure_ascii=False) + "\n"
    return StreamingResponse(gen(), media_type="application/x-ndjson", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/chat")
def chat(body: ChatRequest, user_id: str = Depends(customer)):
    _guard(user_id, body)
    evs = list(orchestrator.stream(user_id, body.conversation_id, body.message))
    final = next((e for e in reversed(evs) if e["type"] == "final"), None)
    return {"events": evs, "final": final}


def _find_run(convo: dict, key: str, val: str) -> str:
    for m in convo["messages"]:
        for c in m.get("cards", []) or []:
            if c.get(key) == val:
                return m.get("run_id", "RUN-0")
    return convo["runs"][-1]["run_id"] if convo["runs"] else "RUN-0"


def refresh_status(convo: dict) -> str:
    cid = convo["conversation_id"]
    pend = any(a["status"] == "pending" and a.get("conversation_id") == cid for a in db.pending_actions())
    conf = any(c["status"] == "pending" and c["conversation_id"] == cid for c in db.state()["confirmations"].values())
    if convo.get("safety"):
        convo["status"] = "escalated"
    else:
        convo["status"] = "awaiting_customer" if conf else "awaiting_human" if pend else "resolved"
    db.put_conversation(convo)
    return convo["status"]


def _confirm(body: ConfirmRequest, user_id: str, accept: bool):
    convo = db.get_conversation(body.conversation_id)
    if not convo or convo["user_id"] != user_id:
        raise HTTPException(404, "conversation_not_found")
    r = action_tools.execute_confirmation(user_id, body.confirmation_token, accept)
    run_id = _find_run(convo, "token", body.confirmation_token)
    if not r.get("ok"):
        g = r.get("guardrail")
        evs = [E.make(run_id, "guardrail", "blocked", "BLOCKED: " + g["rule"], g["reason"], g)] if g else []
        msg = r.get("message") or (g["reason"] if g else "Sorry, I couldn't complete that.")
        convo["messages"].append({"role": "assistant", "text": msg, "ts": clock.now_iso(), "run_id": run_id, "cards": []})
        db.put_conversation(convo)
        return {"ok": False, "error": r.get("error"), "message": msg, "events": evs, "conversation_status": refresh_status(convo)}
    evs = []
    if r.get("guardrail"):
        g = r["guardrail"]
        evs.append(E.make(run_id, "guardrail", "done", "ALLOWED: " + g["rule"], "Re-validated at execution time: " + g["reason"], g, 0, "user_confirmation"))
    evs.append(E.make(run_id, "confirmation_request", "done", "Customer " + ("confirmed" if accept else "declined"), r["message"], {"tool": r["tool"], "status": r["status"], "result": r.get("result")}, 0, "user_confirmation"))
    for m in convo["messages"]:
        for c in m.get("cards", []) or []:
            if c.get("token") == body.confirmation_token:
                c["state"] = r["status"]
    convo["messages"].append({"role": "assistant", "text": r["message"], "ts": clock.now_iso(), "run_id": run_id, "cards": []})
    convo["events"] += evs
    db.put_conversation(convo)
    return {"ok": True, "message": r["message"], "status": r["status"], "events": evs, "conversation_status": refresh_status(convo)}


@router.post("/chat/confirm")
def confirm(body: ConfirmRequest, user_id: str = Depends(customer)):
    return _confirm(body, user_id, True)


@router.post("/chat/decline")
def decline(body: ConfirmRequest, user_id: str = Depends(customer)):
    return _confirm(body, user_id, False)


@router.get("/conversations/{cid}")
def get_conversation(cid: str, user_id: str = Depends(customer)):
    c = db.get_conversation(cid)
    if not c or c["user_id"] != user_id:
        raise HTTPException(404, "conversation_not_found")
    return c


@router.post("/conversations/{cid}/close")
def close(cid: str, user_id: str = Depends(customer)):
    c = db.get_conversation(cid)
    if not c or c["user_id"] != user_id:
        raise HTTPException(404, "conversation_not_found")
    qa = run_qa(c)
    c["closed"], c["qa"] = True, qa
    db.put_conversation(c)
    ev = E.make(c["runs"][-1]["run_id"] if c["runs"] else "RUN-0", "qa", "done", "QA review complete", f"overall {qa['overall']}/5", qa, qa["llm"].get("latency_ms") if qa.get("llm") else 0)
    c["events"].append(ev)
    db.save()
    db.audit("ai", "qa_scored", cid, {}, {"overall": qa["overall"]}, user_id=user_id)
    return {"event": ev, "qa": qa}
