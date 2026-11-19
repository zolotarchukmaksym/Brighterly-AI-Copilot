"""Read-only tools (autonomy: auto). All receive the acting user from the server context."""
from __future__ import annotations
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo
from .. import clock, db
from ..kb import search as kb
from . import guardrails as g

ERROR_HELP = {
    "AUTH_CODE_EXPIRED": "The one-time login code expired before it was used. A new code must be requested and used within a few minutes.",
}
FAILURE_HELP = {
    "bank_declined": "The card issuer (bank) declined the charge. This is usually fixed by checking the card with the bank or using another card, then retrying.",
}
PLAN_OPTIONS = [{"lessons_per_week": n} for n in (1, 2, 3, 4, 5)]


def _user(ctx) -> dict:
    return db.get_user(ctx.user_id)


def _lesson_view(l: dict, now) -> dict:
    h = g.hours_until(l["scheduled_start"], now)
    att = l.get("attendance") or {}
    return {
        "lesson_id": l["lesson_id"], "subject": l["subject"], "topic": l["topic"], "tutor_id": l["tutor_id"],
        "tutor_name": l["tutor_name"], "scheduled_start": l["scheduled_start"], "duration_minutes": l["duration_minutes"],
        "status": l["status"], "hours_until_start": round(h, 2) if l["status"] == "scheduled" else None,
        "starts_in": g.fmt_delta(h) if l["status"] == "scheduled" and h > 0 else None,
        "tutor_joined": bool(att.get("tutor_joined_at")), "student_joined": bool(att.get("student_joined_at")),
        "minutes_waited_for_tutor": att.get("minutes_after_start"), "balance_effect": l.get("lesson_balance_effect"),
        "notes": l.get("notes"),
    }


def get_account_status(ctx) -> dict:
    u = _user(ctx)
    sec = u["security_context"]
    people = [{"id": c["child_id"], "first_name": c["full_name"].split()[0], "grade": c["grade"], "subjects": c["subjects"]} for c in u["children"]]
    if u.get("student_profile"):
        sp = u["student_profile"]
        people = [{"id": sp["student_id"], "first_name": sp["full_name"].split()[0], "grade": sp["grade"], "subjects": sp["subjects"]}]
    return {"ok": True, "account_type": u["account_type"], "account_status": u["account_status"],
            "locked": u["account_status"] != "active", "email_verified": u["identity"]["email_verified"],
            "mfa_enabled": sec["mfa_enabled"], "last_successful_login": sec["last_successful_login"],
            "failed_login_attempts_last_24h": sec["failed_login_attempts_last_24h"], "students": people,
            "timezone": u["identity"]["timezone"]}


def get_subscription(ctx) -> dict:
    u = _user(ctx)
    subs = [{k: s[k] for k in ("subscription_id", "status", "subject", "plan_name", "lessons_per_week", "price_usd_per_billing_cycle",
                               "billing_cycle", "started_on", "current_period_ends_on", "next_billing_date", "auto_renew",
                               "pause_history_current_year_months", "cancellation_requested_on", "cancellation_effective_on",
                               "assigned_tutor_ids", "lesson_balance")} | {"pause_requested": s.get("pause_request")} for s in u["subscriptions"]]
    return {"ok": True, "subscriptions": subs, "plan_change_history": u["plan_change_history"][-2:], "plan_options": PLAN_OPTIONS,
            "plan_change_rule": "Plan changes are applied only by a support specialist."}


def _pm_view(u: dict) -> dict | None:
    pm = next((m for m in u["payment_methods"] if m["is_default"]), None)
    if not pm:
        return None
    exp = (pm["expiration_year"], pm["expiration_month"]) < (clock.now().year, clock.now().month)
    return {"brand": pm["brand"], "status": pm["status"], "expired": exp}


def get_payment_history(ctx) -> dict:
    u = _user(ctx)
    pays = [{"payment_id": p["payment_id"], "subscription_id": p["subscription_id"], "amount_usd": p["amount_usd"], "status": p["status"],
             "processed_at": p["processed_at"], "lesson_credits_added": p.get("lesson_credits_added"),
             "failure_reason": p.get("failure_reason"), "failure_explanation": FAILURE_HELP.get(p.get("failure_reason")),
             "refund_status": p["refund_status"]} for p in u["payments"][-8:]]
    cmp_ = g.detect_duplicate_charge(u["payments"])
    subs = [{"subscription_id": s["subscription_id"], "status": s["status"], "next_billing_date": s["next_billing_date"],
             "current_period_ends_on": s["current_period_ends_on"], "billing_cycle": s["billing_cycle"]} for s in u["subscriptions"]]
    return {"ok": True, "payments": pays, "default_payment_method": _pm_view(u), "compare_payments": cmp_, "billing": subs}


def get_lessons(ctx, scope: str = "all", lesson_id: str | None = None) -> dict:
    u = _user(ctx)
    now = clock.now()
    ls = u["lessons"]
    if lesson_id:
        if not g.owns(u, "lesson", lesson_id):
            return {"ok": False, "error": "forbidden"}
        ls = [l for l in ls if l["lesson_id"] == lesson_id]
    elif scope == "upcoming":
        ls = [l for l in ls if l["status"] == "scheduled"]
    elif scope == "recent":
        ls = [l for l in ls if l["status"] != "scheduled"]
    ls = sorted(ls, key=lambda l: l["scheduled_start"])
    out = [_lesson_view(l, now) for l in ls]
    for o, l in zip(out, ls):
        if l["status"] == "tutor_no_show":
            o["no_show_review"] = g.tutor_no_show_review(l)
    nxt = next((o for o in out if o["status"] == "scheduled"), None)
    return {"ok": True, "lessons": out[:8], "next_lesson": nxt, "tutors": [{"tutor_id": t["tutor_id"], "name": t["name"], "subject": t["subject"]} for t in u["tutors"]]}


def get_learning_progress(ctx) -> dict:
    u = _user(ctx)
    return {"ok": True, "progress": u["learning_progress"]}


def get_technical_events(ctx) -> dict:
    u = _user(ctx)
    ev = [dict(e, explanation=ERROR_HELP.get(e["error_code"])) for e in u["technical_events"][-5:]]
    return {"ok": True, "events": ev, "account_locked": u["account_status"] != "active",
            "failed_login_attempts_last_24h": u["security_context"]["failed_login_attempts_last_24h"]}


def check_policy(ctx, policy: str, lesson_id: str | None = None, subscription_id: str | None = None, months: int = 1, payment_id: str | None = None) -> dict:
    u = _user(ctx)
    now = clock.now()
    if policy in ("reschedule", "cancel_lesson", "tutor_no_show"):
        if not lesson_id:
            nxt = next((l for l in sorted(u["lessons"], key=lambda l: l["scheduled_start"]) if l["status"] == "scheduled"), None)
            if policy == "tutor_no_show":
                nxt = next((l for l in u["lessons"] if l["status"] == "tutor_no_show"), None)
            lesson_id = nxt["lesson_id"] if nxt else None
        if not lesson_id:
            return {"ok": False, "error": "no_matching_lesson"}
        if not g.owns(u, "lesson", lesson_id):
            return {"ok": False, "error": "forbidden"}
        l = next(x for x in u["lessons"] if x["lesson_id"] == lesson_id)
        v = g.tutor_no_show_review(l) if policy == "tutor_no_show" else g.can_reschedule(l, now)
        return {"ok": True, "policy": policy, "lesson_id": lesson_id, "verdict": v, "guardrail": v}
    sub = None
    if policy in ("pause", "subscription_cancellation", "refund"):
        subs = u["subscriptions"]
        if subscription_id:
            if not g.owns(u, "subscription", subscription_id):
                return {"ok": False, "error": "forbidden"}
            subs = [s for s in subs if s["subscription_id"] == subscription_id]
        sub = subs[0] if subs else None
        if not sub:
            return {"ok": False, "error": "no_subscription"}
    if policy == "pause":
        v = g.can_pause(sub, int(months), now)
    elif policy == "subscription_cancellation":
        v = g.can_cancel_subscription(sub, now)
    elif policy == "refund":
        v = g.refund_eligible(u, sub["subscription_id"] if sub else None)
    else:
        return {"ok": False, "error": "unknown_policy"}
    return {"ok": True, "policy": policy, "subscription_id": sub["subscription_id"], "verdict": v, "guardrail": v}


def get_available_slots(ctx, lesson_id: str | None = None) -> dict:
    u = _user(ctx)
    if not lesson_id:
        l = next((x for x in sorted(u["lessons"], key=lambda x: x["scheduled_start"]) if x["status"] == "scheduled"), None)
    else:
        if not g.owns(u, "lesson", lesson_id):
            return {"ok": False, "error": "forbidden"}
        l = next(x for x in u["lessons"] if x["lesson_id"] == lesson_id)
    if not l:
        return {"ok": False, "error": "no_matching_lesson"}
    return {"ok": True, "lesson_id": l["lesson_id"], "tutor": l["tutor_name"], "slots": compute_slots(u, l)}


def compute_slots(u: dict, lesson: dict, n: int = 6) -> list[dict]:
    """Deterministic: top-of-hour slots 16:00-19:00 in the tutor's timezone, next 7 days, no clash with own lessons."""
    tutor = next((t for t in u["tutors"] if t["tutor_id"] == lesson["tutor_id"]), None)
    tz = ZoneInfo(tutor["timezone"] if tutor else "UTC")
    now = clock.now()
    busy = {clock.parse_ts(l["scheduled_start"]) for l in u["lessons"] if l["status"] == "scheduled"}
    orig_hour = clock.parse_ts(lesson["scheduled_start"]).astimezone(tz).hour
    day0 = (now.astimezone(tz) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    slots = []
    for d in range(7):
        for hour in sorted((16, 17, 18, 19), key=lambda h: abs(h - orig_hour)):
            start = (day0 + timedelta(days=d)).replace(hour=hour).astimezone(timezone.utc)
            if start in busy or (start - now).total_seconds() / 3600 <= g.FREE_CHANGE_HOURS:
                continue
            slots.append({"start": clock.iso(start), "label": start.astimezone(tz).strftime("%a %b %d, %H:%M %Z")})
            break
    return slots[:n]


def search_knowledge_base(ctx, query: str) -> dict:
    return {"ok": True, "results": kb.search(query, 3)}


def ask_clarifying_question(ctx, question: str) -> dict:
    return {"ok": True, "question": question[:300]}
