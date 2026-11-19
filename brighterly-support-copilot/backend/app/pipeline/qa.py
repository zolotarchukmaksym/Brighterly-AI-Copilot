"""QA at conversation close: LLM rubric + deterministic checks that cap the scores."""
from __future__ import annotations
import json
from .. import clock, db, prompts
from ..llm import get_llm
from ..models import QAScores
from . import events as E
from .reply_validator import validate


def run_qa(convo: dict) -> dict:
    llm = get_llm()
    user = db.get_user(convo["user_id"])
    replies = [m["text"] for m in convo["messages"] if m["role"] == "assistant"]
    viol = [v for r in convo["runs"] for v in r.get("violations", [])]
    extra = [v for r in replies for v in validate(r, user["identity"]["email"], convo["user_id"], [])]
    tickets = [t for t in db.tickets() if t.get("conversation_id") == convo["conversation_id"]]
    safety_ok = (not convo.get("safety")) or any(t["type"] == "child_safety_concern" and t["priority"] == "critical" for t in tickets)
    acts = [a for a in db.pending_actions() if a.get("conversation_id") == convo["conversation_id"]]
    had_action = bool(tickets or acts or any(m.get("cards") for m in convo["messages"]))
    checks = {"policy_violations": len(viol) + len(extra), "safety_ok": safety_ok, "facts_consistent": not any(v.startswith("false_action_claim") for v in viol + extra),
              "tickets": [t["ticket_id"] for t in tickets], "pending_actions": [a["action_id"] for a in acts]}
    data = {"transcript": [{"role": m["role"], "text": m["text"][:400]} for m in convo["messages"]][-12:], "assistant_replies": replies,
            "tool_facts": [{"run": r["run_id"], "counts": r.get("counts")} for r in convo["runs"]], "deterministic_checks": checks, "status": convo["status"], "had_action": had_action}
    s: QAScores = llm.classify(prompts.load("qa"), json.dumps(data, default=str), QAScores, "fast")
    meta = llm.pop_meta()
    d = s.model_dump()
    if checks["policy_violations"]:
        d["policy_compliance"] = min(d["policy_compliance"], 2)
    if not safety_ok:
        d["safety"] = 1
    scores = [d[k] for k in ("empathy", "factual_accuracy", "policy_compliance", "resolution", "safety")]
    d["overall"] = round(sum(scores) / 5, 1)
    d["deterministic_checks"] = checks
    d["conversation_id"] = convo["conversation_id"]
    d["created_at"] = clock.now_iso()
    d["llm"] = meta
    db.add_qa(d)
    return d
