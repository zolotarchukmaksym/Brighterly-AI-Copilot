"""Deterministic business rules. Pure functions: the model never decides policy.

Every function returns a Verdict-like dict:
  {"allowed": bool, "decision": "ALLOWED"|"BLOCKED"|"REVIEW", "rule": str, "reason": str, ...extra}
"""
from __future__ import annotations
from datetime import date, datetime, timedelta
from .. import clock
from ..db import meta

FREE_CHANGE_HOURS = 6
NO_SHOW_REVIEW_MINUTES = 15
PAUSE_MAX_SINGLE = 3
PAUSE_MAX_YEAR = 4
CANCEL_NOTICE_BUSINESS_DAYS = 3


def fmt_delta(hours: float) -> str:
    total = int(abs(hours) * 60)
    h, m = divmod(total, 60)
    if h >= 48:
        return f"{h // 24}d {h % 24}h"
    return f"{h}h {m:02d}m" if h else f"{m}m"


def hours_until(start_iso: str, now: datetime | None = None) -> float:
    now = now or clock.now()
    return (clock.parse_ts(start_iso) - now).total_seconds() / 3600


def _v(allowed: bool, rule: str, reason: str, decision: str | None = None, **extra) -> dict:
    return {"allowed": allowed, "decision": decision or ("ALLOWED" if allowed else "BLOCKED"),
            "rule": rule, "reason": reason, **extra}


# ---------------------------------------------------------------- ownership
def owned_ids(user: dict) -> dict[str, set]:
    sids = {c["child_id"] for c in user.get("children", [])}
    if user.get("student_profile"):
        sids.add(user["student_profile"]["student_id"])
    return {
        "lesson": {l["lesson_id"] for l in user["lessons"]},
        "subscription": {s["subscription_id"] for s in user["subscriptions"]},
        "payment": {p["payment_id"] for p in user["payments"]},
        "tutor": {t["tutor_id"] for t in user["tutors"]},
        "student": sids,
    }


def owns(user: dict, kind: str, entity_id: str | None) -> bool:
    return bool(entity_id) and entity_id in owned_ids(user).get(kind, set())


# ---------------------------------------------------------------- lessons
def can_reschedule(lesson: dict, now: datetime | None = None) -> dict:
    """Free reschedule/cancel only if the lesson starts MORE than 6 hours from now."""
    h = hours_until(lesson["scheduled_start"], now)
    if lesson["status"] != "scheduled":
        return _v(False, "lesson_must_be_scheduled", f"Lesson is '{lesson['status']}', not an upcoming scheduled lesson.", hours_to_start=round(h, 2))
    if h > FREE_CHANGE_HOURS:
        return _v(True, "free_change_over_6h", f"Lesson starts in {fmt_delta(h)}; free changes require more than {FREE_CHANGE_HOURS}h.", hours_to_start=round(h, 2))
    return _v(False, "free_change_over_6h", f"Lesson starts in {fmt_delta(h)}, free cancellation requires > {FREE_CHANGE_HOURS}h.", hours_to_start=round(h, 2))


can_cancel_lesson = can_reschedule  # same 6-hour rule


def tutor_no_show_review(lesson: dict) -> dict:
    att = lesson.get("attendance") or {}
    waited = att.get("minutes_after_start")
    if lesson["status"] == "tutor_no_show" and not att.get("tutor_joined_at") and (waited or 0) >= NO_SHOW_REVIEW_MINUTES:
        return _v(True, "tutor_no_show_15min", f"Tutor did not join; student waited {waited} min (review threshold {NO_SHOW_REVIEW_MINUTES} min).", decision="REVIEW", minutes_waited=waited)
    return _v(False, "tutor_no_show_15min", "No tutor no-show recorded for this lesson (or the wait was under 15 minutes).")


# ---------------------------------------------------------------- subscriptions
def can_pause(sub: dict, months: int, now: datetime | None = None) -> dict:
    used = sub.get("pause_history_current_year_months", 0)
    if sub["status"] != "active":
        return _v(False, "pause_requires_active", f"Subscription status is '{sub['status']}'; only active subscriptions can be paused.")
    if months < 1:
        return _v(False, "pause_min", "Pause length must be at least 1 month.")
    if months > PAUSE_MAX_SINGLE:
        return _v(False, "pause_max_single", f"A single pause is limited to {PAUSE_MAX_SINGLE} months (requested {months}).")
    if used + months > PAUSE_MAX_YEAR:
        return _v(False, "pause_max_year", f"Pauses are limited to {PAUSE_MAX_YEAR} months per calendar year; {used} already used, {PAUSE_MAX_YEAR - used} remaining.")
    return _v(True, "pause_ok", f"{months} month(s) pause is within limits ({used} of {PAUSE_MAX_YEAR} months used this year).", months=months, remaining_after=PAUSE_MAX_YEAR - used - months)


def business_days_between(start: date, end: date) -> int:
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def can_cancel_subscription(sub: dict, now: datetime | None = None) -> dict:
    """Cancellation is never mid-period and needs 3 business days notice; always human-reviewed."""
    now = now or clock.now()
    today = now.date()
    period_end = date.fromisoformat(sub["current_period_ends_on"])
    next_bill = date.fromisoformat(sub["next_billing_date"])
    if sub["status"] not in ("active", "past_due", "pause_requested"):
        return _v(False, "cancel_requires_live_sub", f"Subscription status is '{sub['status']}'.")
    notice = business_days_between(today, next_bill)
    notice_ok = notice >= CANCEL_NOTICE_BUSINESS_DAYS
    mid_period = today < period_end
    reason = (f"Cancellation cannot take effect mid-period: it would stop renewal at the end of the paid period ({period_end.isoformat()}). "
              f"Next billing is {next_bill.isoformat()}, {notice} business day(s) away; {CANCEL_NOTICE_BUSINESS_DAYS} are required for notice "
              f"({'met' if notice_ok else 'NOT met, so the next renewal may still be charged'}). A specialist must review it.")
    return _v(True, "cancel_end_of_period_human_review", reason, decision="REVIEW", effective_on=period_end.isoformat(),
              mid_period=mid_period, notice_business_days=notice, notice_ok=notice_ok)


# ---------------------------------------------------------------- payments
def detect_duplicate_charge(payments: list[dict], payment_id: str | None = None) -> dict:
    """Two successful payments, same subscription+amount, within 48h => duplicate. ~30 days apart => normal renewals."""
    ok = [p for p in payments if p["status"] == "successful"]
    ok.sort(key=lambda p: p["processed_at"])
    pairs, dup = [], None
    for i in range(len(ok)):
        for j in range(i + 1, len(ok)):
            a, b = ok[i], ok[j]
            if a["subscription_id"] != b["subscription_id"] or a["amount_usd"] != b["amount_usd"]:
                continue
            mins = (clock.parse_ts(b["processed_at"]) - clock.parse_ts(a["processed_at"])).total_seconds() / 60
            declared = b.get("duplicate_of_payment_id") == a["payment_id"]
            is_dup = declared or mins <= 48 * 60
            if not is_dup and mins > 20 * 24 * 60:
                kind = "regular_renewal"
            elif not is_dup:
                continue
            else:
                kind = "duplicate"
            row = {"payment_a": a["payment_id"], "payment_b": b["payment_id"], "amount_usd": a["amount_usd"],
                   "minutes_apart": round(mins), "days_apart": round(mins / 1440, 1), "same_amount": True,
                   "credits_a": a.get("lesson_credits_added"), "credits_b": b.get("lesson_credits_added"),
                   "verdict": kind,
                   "reason": ("Same amount charged within minutes/hours and the second charge added no lesson credits." if kind == "duplicate"
                              else f"Charges are {round(mins / 1440)} days apart and each added its own lesson credits: normal monthly renewals.")}
            pairs.append(row)
            if kind == "duplicate" and (payment_id in (None, b["payment_id"], a["payment_id"])):
                dup = row | {"duplicate_payment_id": b["payment_id"], "original_payment_id": a["payment_id"]}
    return {"duplicate_found": dup is not None, "duplicate": dup, "pairs": pairs[-4:],
            **({"reason": dup["reason"]} if dup else {"reason": "No duplicate found: successful charges are regular monthly renewals."})}


def refund_eligible(user: dict, subscription_id: str | None = None) -> dict:
    """Refunds are never automatic and only cover unused paid lessons; human approval always required."""
    subs = [s for s in user["subscriptions"] if subscription_id in (None, s["subscription_id"])]
    if not subs:
        return _v(False, "refund_unused_paid_only", "No matching subscription.")
    s = max(subs, key=lambda x: x["lesson_balance"]["unused_paid"])
    lb = s["lesson_balance"]
    unused = lb["unused_paid"] - lb["reserved"] if lb["unused_paid"] > lb["reserved"] else lb["unused_paid"]
    pays = [p for p in user["payments"] if p["subscription_id"] == s["subscription_id"] and p["status"] == "successful" and p.get("lesson_credits_added")]
    per = (pays[-1]["amount_usd"] / pays[-1]["lesson_credits_added"]) if pays else None
    if lb["unused_paid"] <= 0:
        return _v(False, "refund_unused_paid_only", "There are no unused paid lessons, so nothing is refundable.", subscription_id=s["subscription_id"], unused_paid=0)
    est = round(per * lb["unused_paid"], 2) if per else None
    return _v(True, "refund_unused_paid_only",
              f"{lb['unused_paid']} unused paid lessons could be considered for a refund; a human specialist makes the final decision. Refunds are never automatic.",
              decision="REVIEW", subscription_id=s["subscription_id"], unused_paid=lb["unused_paid"], reserved=lb["reserved"], estimated_amount_usd=est)
