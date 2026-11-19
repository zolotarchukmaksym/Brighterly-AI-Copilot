"""Write tools: tickets, customer-confirmed actions and human-approval proposals."""
from __future__ import annotations
from datetime import timedelta
from .. import clock, db
from . import guardrails as g
from .read_tools import compute_slots

TEAM = {
    "duplicate_charge": "billing_team", "refund_request": "billing_team", "payment_failed": "billing_support",
    "subscription_cancellation": "billing_team", "subscription_pause": "billing_team", "subscription_plan_change": "billing_team",
    "late_cancellation_exception": "scheduling_support", "scheduling_exception": "scheduling_support",
    "tutor_no_show": "educational_department", "tutor_change": "educational_department", "substitute_tutor": "educational_department",
    "lesson_access_issue": "technical_support", "technical_issue": "technical_support", "general_support": "general_support",
    "child_safety_concern": "trust_and_safety_immediately", "progress_followup": "educational_department",
}
TICKET_TYPES = [t for t in TEAM if t not in ("child_safety_concern",)]
DEFAULT_PRIORITY = {"duplicate_charge": "high", "tutor_no_show": "high", "late_cancellation_exception": "high", "lesson_access_issue": "high",
                    "payment_failed": "high", "child_safety_concern": "critical"}
KIND_TO_TICKET = {"refund_review": "refund_request", "credit_lesson_review": "tutor_no_show", "cancellation_review": "subscription_cancellation",
                  "tutor_change": "tutor_change", "plan_change": "subscription_plan_change", "substitute_tutor": "substitute_tutor",
                  "general_review": "general_support", "child_safety": "child_safety_concern"}
CONFIRM_TTL_MIN = 10


def _blocked(rule, reason, **extra):
    return {"ok": False, "error": "blocked_by_guardrail", "guardrail": {"allowed": False, "decision": "BLOCKED", "rule": rule, "reason": reason}, **extra}


def _entity_kind(entity_id: str) -> str | None:
    for pre, kind in (("LESSON", "lesson"), ("SUB", "subscription"), ("PAY", "payment"), ("T-", "tutor"), ("BR-CHILD", "student"), ("BR-STUDENT", "student")):
        if entity_id.startswith(pre):
            return kind
    return None


def _make_ticket(ctx, ttype: str, summary: str, priority: str | None, entity: str | None, extra: dict | None = None) -> tuple[dict, bool]:
    now = clock.now()
    for t in db.tickets():
        if t["user_id"] == ctx.user_id and t["type"] == ttype and t.get("linked_entity_id") == entity and \
                (now - clock.parse_ts(t["created_at"])) < timedelta(hours=24) and t["status"] in ("open", "pending_review"):
            return t, True
    t = {"ticket_id": db.new_id("TKT"), "user_id": ctx.user_id, "conversation_id": ctx.conversation_id, "type": ttype,
         "team": TEAM.get(ttype, "general_support"), "priority": priority or DEFAULT_PRIORITY.get(ttype, "medium"),
         "status": "open", "summary": summary[:600], "linked_entity_id": entity, "created_by": "ai", "created_at": clock.now_iso(),
         **(extra or {})}
    db.add_ticket(t)
    return t, False


def create_ticket(ctx, type: str, summary: str, priority: str | None = None, linked_entity_id: str | None = None) -> dict:
    u = db.get_user(ctx.user_id)
    if type not in TICKET_TYPES:
        return _blocked("ticket_type_not_allowed", f"Ticket type '{type}' cannot be created directly by the assistant.")
    if linked_entity_id:
        kind = _entity_kind(linked_entity_id)
        if kind and not g.owns(u, kind, linked_entity_id):
            return {"ok": False, "error": "forbidden"}
    if type == "duplicate_charge":
        d = g.detect_duplicate_charge(u["payments"])
        if not d["duplicate_found"]:
            return _blocked("no_duplicate_no_ticket", "No duplicate found: ticket not needed.", detail=d["reason"])
        linked_entity_id = linked_entity_id or d["duplicate"]["duplicate_payment_id"]
    t, existed = _make_ticket(ctx, type, summary, priority, linked_entity_id)
    return {"ok": True, "ticket_id": t["ticket_id"], "team": t["team"], "priority": t["priority"], "type": t["type"],
            "deduplicated": existed, "status": t["status"]}


def escalate_to_human(ctx, kind: str, summary: str, entity_id: str | None = None, reason: str | None = None, months: int | None = None, details: str | None = None) -> dict:
    """Creates a ticket plus (except for safety) a pending_action that ONLY a human specialist can approve."""
    u = db.get_user(ctx.user_id)
    if kind not in KIND_TO_TICKET:
        return _blocked("unknown_escalation_kind", f"Unknown escalation kind '{kind}'.")
    ttype = KIND_TO_TICKET[kind]
    params: dict = {"details": (details or "")[:300]}
    evidence: list = []
    guard = None

    if kind == "child_safety":
        ctx_info = {"account_type": u["account_type"], "students": [c.get("full_name", "").split()[0] for c in u.get("children", [])] or
                    ([u["student_profile"]["full_name"].split()[0]] if u.get("student_profile") else []),
                    "recent_lessons": [{"lesson_id": l["lesson_id"], "tutor": l["tutor_name"], "start": l["scheduled_start"]}
                                       for l in sorted(u["lessons"], key=lambda x: x["scheduled_start"])
                                       if l["status"] != "scheduled"][-2:]}
        t, existed = _make_ticket(ctx, ttype, summary or "Child safety report", "critical", None,
                                  {"verbatim_message": ctx.message, "minimal_context": ctx_info, "status": "open", "handoff": "trust_and_safety_immediately"})
        if existed:
            t.setdefault("additional_messages", []).append({"at": clock.now_iso(), "text": ctx.message})
            db.save()
        return {"ok": True, "kind": kind, "ticket_id": t["ticket_id"], "team": t["team"], "priority": "critical", "pending_action": None, "deduplicated": existed,
                "autonomy": "auto", "safety": True}

    entity = entity_id
    if kind == "refund_review":
        if (reason or "") == "duplicate_charge":
            d = g.detect_duplicate_charge(u["payments"], entity)
            if not d["duplicate_found"]:
                return _blocked("no_duplicate_no_ticket", "No duplicate found: ticket not needed.", detail=d["reason"])
            entity = d["duplicate"]["duplicate_payment_id"]
            ttype = "duplicate_charge"
            evidence = [d["duplicate"]["original_payment_id"], d["duplicate"]["duplicate_payment_id"]]
            params |= {"payment_id": entity, "refund_amount_usd": d["duplicate"]["amount_usd"], "reason": "duplicate_charge", "comparison": d["duplicate"]}
            guard = {"allowed": True, "decision": "REVIEW", "rule": "duplicate_refund_human_approval",
                     "reason": f"Duplicate confirmed ({d['duplicate']['minutes_apart']} min apart, no credits added). Refund requires human approval."}
        else:
            sub = next((s for s in u["subscriptions"] if s["subscription_id"] == entity), None) or (u["subscriptions"][0] if u["subscriptions"] else None)
            if entity and _entity_kind(entity) and not g.owns(u, _entity_kind(entity), entity) and not sub:
                return {"ok": False, "error": "forbidden"}
            v = g.refund_eligible(u, sub["subscription_id"] if sub else None)
            if not v["allowed"]:
                return _blocked(v["rule"], v["reason"])
            entity = v["subscription_id"]
            evidence = [entity]
            params |= {"subscription_id": entity, "unused_paid_lessons": v["unused_paid"], "estimated_amount_usd": v["estimated_amount_usd"], "reason": reason or "customer_request"}
            guard = {k: v[k] for k in ("allowed", "decision", "rule", "reason")}
    elif kind == "credit_lesson_review":
        if not g.owns(u, "lesson", entity):
            return {"ok": False, "error": "forbidden"} if entity else _blocked("lesson_required", "A lesson_id is required.")
        l = next(x for x in u["lessons"] if x["lesson_id"] == entity)
        v = g.tutor_no_show_review(l)
        if not v["allowed"]:
            return _blocked(v["rule"], v["reason"])
        evidence = [entity]
        params |= {"lesson_id": entity, "minutes_waited": v["minutes_waited"]}
        guard = {k: v[k] for k in ("allowed", "decision", "rule", "reason")}
    elif kind == "cancellation_review":
        sub = next((s for s in u["subscriptions"] if s["subscription_id"] == entity), None) or (u["subscriptions"][0] if u["subscriptions"] else None)
        if not sub:
            return _blocked("no_subscription", "No subscription found.")
        v = g.can_cancel_subscription(sub)
        if not v["allowed"]:
            return _blocked(v["rule"], v["reason"])
        entity = sub["subscription_id"]
        evidence = [entity]
        params |= {"subscription_id": entity, "effective_on": v["effective_on"], "notice_ok": v["notice_ok"]}
        guard = {k: v[k] for k in ("allowed", "decision", "rule", "reason")}
    else:
        if entity and _entity_kind(entity) and not g.owns(u, _entity_kind(entity), entity):
            return {"ok": False, "error": "forbidden"}
        params |= {"entity_id": entity}
        evidence = [entity] if entity else []
        guard = {"allowed": True, "decision": "REVIEW", "rule": f"{kind}_human_only", "reason": f"'{kind}' is applied only by a human specialist."}

    t, existed = _make_ticket(ctx, ttype, summary, None, entity, {"status": "pending_review"})
    existing = next((a for a in db.pending_actions() if a["ticket_id"] == t["ticket_id"] and a["status"] == "pending"), None)
    if existing:
        return {"ok": True, "kind": kind, "ticket_id": t["ticket_id"], "team": t["team"], "priority": t["priority"], "pending_action": existing, "deduplicated": True, "guardrail": guard, "autonomy": "human_approval"}
    action = {"action_id": db.new_id("ACT"), "type": kind, "user_id": ctx.user_id, "customer_name": u["identity"]["full_name"],
              "ticket_id": t["ticket_id"], "team": t["team"], "priority": t["priority"], "status": "pending", "summary": summary[:500],
              "evidence": evidence, "params": params, "created_at": clock.now_iso(), "conversation_id": ctx.conversation_id,
              "autonomy": "human_approval", "decided_by": None, "decision_reason": None}
    db.put_pending(action)
    return {"ok": True, "kind": kind, "ticket_id": t["ticket_id"], "team": t["team"], "pending_action": action, "deduplicated": False, "guardrail": guard,
            "autonomy": "human_approval"}


def _new_confirmation(ctx, tool: str, params: dict, summary: str, extra: dict | None = None) -> dict:
    c = {"token": db.new_id("CNF") + "-" + db.new_id("T")[2:], "user_id": ctx.user_id, "conversation_id": ctx.conversation_id, "tool": tool,
         "params": params, "summary": summary, "status": "pending", "created_at": clock.now_iso(),
         "expires_at": clock.iso(clock.now() + timedelta(minutes=CONFIRM_TTL_MIN))}
    db.put_confirmation(c)
    return {"ok": True, "status": "awaiting_customer_confirmation", "confirmation_token": c["token"], "summary": summary,
            "expires_at": c["expires_at"], "autonomy": "user_confirmation", **(extra or {})}


def reschedule_lesson(ctx, lesson_id: str, new_start: str) -> dict:
    u = db.get_user(ctx.user_id)
    if not g.owns(u, "lesson", lesson_id):
        return {"ok": False, "error": "forbidden"}
    l = next(x for x in u["lessons"] if x["lesson_id"] == lesson_id)
    v = g.can_reschedule(l)
    if not v["allowed"]:
        return {"ok": False, "error": "blocked_by_guardrail", "guardrail": v}
    slots = compute_slots(u, l)
    slot = next((s for s in slots if s["start"] == new_start), None)
    if not slot:
        return _blocked("slot_not_available", "The requested time is not one of the available slots.", available_slots=slots)
    summary = f"Reschedule {l['subject']} lesson with {l['tutor_name']} to {slot['label']}?"
    return _new_confirmation(ctx, "reschedule_lesson", {"lesson_id": lesson_id, "new_start": new_start, "label": slot["label"]}, summary,
                             {"guardrail": v, "label": slot["label"], "alternatives": [s for s in slots if s["start"] != new_start][:3]})


def submit_pause_request(ctx, subscription_id: str, months: int) -> dict:
    u = db.get_user(ctx.user_id)
    if not g.owns(u, "subscription", subscription_id):
        return {"ok": False, "error": "forbidden"}
    sub = next(s for s in u["subscriptions"] if s["subscription_id"] == subscription_id)
    v = g.can_pause(sub, int(months))
    if not v["allowed"]:
        return {"ok": False, "error": "blocked_by_guardrail", "guardrail": v}
    return _new_confirmation(ctx, "submit_pause_request", {"subscription_id": subscription_id, "months": int(months)},
                             f"Pause your {sub['plan_name']} for {int(months)} month(s)?", {"guardrail": v})


# ------------------------------------------------------------------ customer confirmation
def execute_confirmation(user_id: str, token: str, accept: bool) -> dict:
    c = db.get_confirmation(token)
    if not c or c["user_id"] != user_id:
        db.audit("system", "confirmation_rejected_unknown_or_foreign", token, user_id=user_id)
        return {"ok": False, "error": "forbidden"}
    if c["status"] != "pending":
        return {"ok": False, "error": "already_" + c["status"]}
    if clock.now() > clock.parse_ts(c["expires_at"]):
        c["status"] = "expired"; db.save()
        return {"ok": False, "error": "expired", "message": "This confirmation expired. Please ask again."}
    if not accept:
        c["status"] = "declined"; db.save()
        db.audit("user", "confirmation_declined", c["tool"], c["params"], user_id=user_id)
        return {"ok": True, "status": "declined", "tool": c["tool"], "message": "No problem, nothing was changed."}
    u = db.get_user(user_id)
    p = c["params"]
    if c["tool"] == "reschedule_lesson":
        l = next((x for x in u["lessons"] if x["lesson_id"] == p["lesson_id"]), None)
        v = g.can_reschedule(l) if l else {"allowed": False, "reason": "lesson not found", "rule": "x", "decision": "BLOCKED"}
        if not v["allowed"]:
            return {"ok": False, "error": "blocked_by_guardrail", "guardrail": v}
        old = l["scheduled_start"]
        def fn(_s):
            l["rescheduled_from"] = old
            l["scheduled_start"] = p["new_start"]
        db.mutate(fn)
        msg = f"Done. Your lesson is now on {p['label']}."
        res = {"lesson_id": l["lesson_id"], "from": old, "to": p["new_start"]}
    else:
        sub = next((s for s in u["subscriptions"] if s["subscription_id"] == p["subscription_id"]), None)
        v = g.can_pause(sub, p["months"]) if sub else {"allowed": False, "reason": "subscription not found", "rule": "x", "decision": "BLOCKED"}
        if not v["allowed"]:
            return {"ok": False, "error": "blocked_by_guardrail", "guardrail": v}
        def fn(_s):
            sub["pause_request"] = {"months": p["months"], "requested_on": clock.now_iso(), "status": "submitted"}
        db.mutate(fn)
        class _C: pass
        cx = _C(); cx.user_id = user_id; cx.conversation_id = c["conversation_id"]
        t, _ = _make_ticket(cx, "subscription_pause", f"Pause request: {p['months']} month(s) for {p['subscription_id']} (customer confirmed).", "low", p["subscription_id"])
        msg = f"Your pause request for {p['months']} month(s) has been submitted (ticket {t['ticket_id']}). The billing team will confirm the dates."
        res = {"subscription_id": sub["subscription_id"], "ticket_id": t["ticket_id"]}
    c["status"] = "confirmed"; db.save()
    db.audit("user", "confirmation_executed", c["tool"], p, res, user_id=user_id)
    return {"ok": True, "status": "confirmed", "tool": c["tool"], "message": msg, "result": res, "guardrail": v}


# ------------------------------------------------------------------ human decisions
def decide_action(action_id: str, approve: bool, reason: str | None = None) -> dict:
    a = db.get_pending(action_id)
    if not a:
        return {"ok": False, "error": "not_found"}
    if a["status"] != "pending":
        return {"ok": False, "error": "already_" + a["status"]}
    u = db.get_user(a["user_id"])
    ticket = next((t for t in db.tickets() if t["ticket_id"] == a["ticket_id"]), None)
    p = a["params"]
    if not approve:
        def fn(_s):
            a.update(status="rejected", decided_by="agent", decision_reason=reason, decided_at=clock.now_iso())
            if ticket: ticket["status"] = "closed_rejected"
        db.mutate(fn)
        db.audit("agent", "action_rejected", action_id, {"type": a["type"], "reason": reason}, user_id=a["user_id"])
        return {"ok": True, "status": "rejected", "action": a, "customer_message": f"A support specialist reviewed your request and was not able to approve it. Reason: {reason}. Please reply here if you would like to discuss it further."}
    # re-check guardrails at execution time
    guard = {"allowed": True, "decision": "ALLOWED", "rule": "recheck", "reason": "Guardrails re-validated at approval time."}
    msg = "A support specialist approved your request."
    if a["type"] == "refund_review":
        if p.get("reason") == "duplicate_charge":
            d = g.detect_duplicate_charge(u["payments"], p["payment_id"])
            if not d["duplicate_found"]:
                return {"ok": False, "error": "blocked_by_guardrail", "guardrail": {"allowed": False, "decision": "BLOCKED", "rule": "no_duplicate_no_ticket", "reason": "Duplicate no longer detected at approval time."}}
            pay = next(x for x in u["payments"] if x["payment_id"] == p["payment_id"])
            def fn(_s): pay["refund_status"] = "refund_approved"; u["refund_history"].append({"refund_request_id": db.new_id("REFUND"), "requested_on": clock.now().date().isoformat(), "payment_id": pay["payment_id"], "amount_usd": pay["amount_usd"], "status": "approved", "reason": "duplicate_charge", "reviewed_by": "support_agent"})
            db.mutate(fn)
            msg = f"Good news: a support specialist confirmed the duplicate charge and approved a refund of ${pay['amount_usd']:.2f}. It should appear on your original payment method within 5-10 business days."
            guard["reason"] = "Duplicate re-verified at approval time."
        else:
            v = g.refund_eligible(u, p["subscription_id"])
            if not v["allowed"]:
                return {"ok": False, "error": "blocked_by_guardrail", "guardrail": v}
            def fn(_s): u["refund_history"].append({"refund_request_id": db.new_id("REFUND"), "requested_on": clock.now().date().isoformat(), "payment_id": None, "amount_usd": v["estimated_amount_usd"], "status": "approved", "reason": p.get("reason"), "reviewed_by": "support_agent"})
            db.mutate(fn)
            msg = f"A support specialist approved a refund for your unused paid lessons (about ${v['estimated_amount_usd']:.2f}). You will be emailed the exact amount and timing."
    elif a["type"] == "credit_lesson_review":
        l = next(x for x in u["lessons"] if x["lesson_id"] == p["lesson_id"])
        v = g.tutor_no_show_review(l)
        if not v["allowed"]:
            return {"ok": False, "error": "blocked_by_guardrail", "guardrail": v}
        sub = u["subscriptions"][0]
        def fn(_s):
            l["lesson_balance_effect"] = "credited_back"
            sub["lesson_balance"]["unused_paid"] += 1
        db.mutate(fn)
        msg = "A support specialist confirmed the tutor no-show and returned the lesson to your balance."
    elif a["type"] == "cancellation_review":
        sub = next(s for s in u["subscriptions"] if s["subscription_id"] == p["subscription_id"])
        v = g.can_cancel_subscription(sub)
        def fn(_s):
            sub["cancellation_requested_on"] = clock.now().date().isoformat()
            sub["cancellation_effective_on"] = v["effective_on"]
            sub["auto_renew"] = False
        db.mutate(fn)
        msg = f"A support specialist approved your cancellation. Renewal is stopped and the subscription ends on {v['effective_on']}."
    elif a["type"] == "tutor_change":
        msg = "A specialist from the educational department approved a tutor change. We will match a new tutor and email you the options."
    elif a["type"] == "substitute_tutor":
        msg = "The educational department is arranging a substitute tutor for your next lesson and will confirm shortly."
    elif a["type"] == "plan_change":
        msg = "A support specialist approved your plan change request. It will apply from the next billing period."
    def fn2(_s):
        a.update(status="approved", decided_by="agent", decision_reason=reason, decided_at=clock.now_iso())
        if ticket: ticket["status"] = "resolved"
    db.mutate(fn2)
    db.audit("agent", "action_approved", action_id, {"type": a["type"]}, {"ticket": a["ticket_id"]}, user_id=a["user_id"])
    return {"ok": True, "status": "approved", "action": a, "customer_message": msg, "guardrail": guard}
