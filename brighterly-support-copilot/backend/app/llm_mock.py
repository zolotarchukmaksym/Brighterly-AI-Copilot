"""Deterministic offline provider. Same interface as GemmaLLM, so the whole pipeline runs unchanged.

- classify(TriageResult): dataset message match first (user_message + variants), then keyword rules.
- run_tools: a scripted tool plan per intent that calls the REAL tools through the executor.
- classify(ResolutionPlan): facts derived from real tool results.
- generate: English templates assembled from the plan facts.
"""
from __future__ import annotations
import json, re, time
from typing import Callable
from pydantic import BaseModel
from . import db
from .llm import ToolRunResult, pop_meta, _add_meta
from .config import settings


def _lag(factor: float = 1.0) -> None:
    if settings.mock_latency_ms > 0:
        time.sleep(settings.mock_latency_ms * factor / 1000)
from .models import TriageResult, ResolutionPlan, QAScores

CASE_TO_INTENT = {"reschedule_eligible": "lesson_reschedule", "reschedule_late": "late_cancellation", "duplicate_charge_false_alarm": "duplicate_charge"}
_index: dict[str, dict] | None = None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def _build_index() -> dict:
    global _index
    if _index is not None:
        return _index
    idx: dict[str, dict] = {}
    for u in db.list_users():
        p = u["support_context"]["primary_demo_case"]
        intent = CASE_TO_INTENT.get(p["case_type"], p["case_type"])
        idx.setdefault(_norm(p["user_message"]), {"intent": intent, "conf": p["confidence_score"], "priority": p["priority"], "style": "primary", "secondary": None})
        for v in p.get("user_message_variants", []):
            idx.setdefault(_norm(v["text"]), {"intent": intent, "conf": 0.9, "priority": p["priority"], "style": v["style"], "secondary": v.get("secondary_intent")})
    _index = idx
    return idx


SAFETY_PAT = re.compile(r"another app|outside (the )?(platform|website|class)|keep (it|this) (a )?secret|not to tell|told me not to tell|don'?t tell (your|my|us)|don'?t feel safe|inappropriate|something doesn'?t feel right|touch|scared of (my|the) tutor|link to an outside", re.I)
KEYWORDS: list[tuple[str, str, float]] = [
    (r"charged (me )?twice|double charge|two charges|charged two times", "duplicate_charge", 0.9),
    (r"refund|money back", "refund_request", 0.88),
    (r"payment (has )?failed|card (was )?declined|payment didn'?t (go|work)", "payment_failed", 0.9),
    (r"cancel.*(subscription|renewal|plan|membership)|(stop|end) .*renewal", "subscription_cancellation", 0.88),
    (r"cancel.*(lesson|class|session)|(lesson|class).*cancel", "late_cancellation", 0.88),
    (r"reschedul|move (my |the )?(next )?(lesson|class)|another (available )?time", "lesson_reschedule", 0.9),
    (r"(tutor|teacher).*(did ?n.t|didn.t|not|never) (join|show|come)|no.?show", "tutor_no_show", 0.9),
    (r"(change|different|new) (the |my )?tutor", "tutor_change", 0.88),
    (r"substitute|replacement tutor|tutor is unavailable", "substitute_tutor", 0.88),
    (r"\bpause|freeze my", "subscription_pause", 0.9),
    (r"lessons? per week|upgrade|downgrade|change (my )?plan", "subscription_plan_change", 0.86),
    (r"log ?in|sign in|password|verification code|login code", "technical_login", 0.88),
    (r"join (lesson )?button|classroom|can.?t (enter|join)|camera|microphone", "technical_lesson_access", 0.88),
    (r"progress|how is my (child|son|daughter)|practice", "progress_question", 0.86),
    (r"when is|next lesson|my schedule|upcoming lesson", "lesson_schedule_lookup", 0.86),
    (r"how (do|can) i|what is|policy|how does", "faq", 0.8),
]
CLARIFY_Q = "Happy to help! Could you tell me a bit more about what you need: is it about a lesson, a payment, your subscription, or logging in?"
YES = re.compile(r"^(yes|yeah|yep|sure|please do|ok(ay)?|yes please)\b", re.I)


class MockLLM:
    name = "mock"

    def pop_meta(self) -> dict:
        return pop_meta()

    # ------------------------------------------------------------------ classify
    def classify(self, system: str, user: str, schema: type[BaseModel], tier: str = "fast") -> BaseModel:
        _lag(0.8 if schema is TriageResult else 1.0)
        _add_meta("mock-" + tier, 0, 0, 1, False)
        data = json.loads(user)
        if schema is TriageResult:
            return self._triage(data)
        if schema is ResolutionPlan:
            return build_plan(data)
        if schema is QAScores:
            return self._qa(data)
        raise ValueError(schema)

    def _triage(self, d: dict) -> TriageResult:
        msg = d["customer_message"]
        writer = d.get("account_type", "unknown")
        writer = "student" if writer == "student" else "parent" if writer == "parent" else "unknown"
        turns = d.get("recent_turns", [])
        last_ai = next((t["text"] for t in reversed(turns) if t["role"] == "assistant"), "")
        base = dict(writer=writer, secondary_intents=[], entities={})
        if SAFETY_PAT.search(msg):
            return TriageResult(primary_intent="child_safety_concern", confidence=0.97, urgency="critical", sentiment="distressed", safety_flag=True,
                                churn_risk="low", rationale="Report involving a minor's safety (contact outside the platform, secrecy, or unsafe feeling).", **base)
        if YES.match(msg.strip()) and "review an exception" in last_ai:
            return TriageResult(primary_intent="late_cancellation", confidence=0.93, urgency="medium", sentiment="neutral", churn_risk="medium",
                                rationale="Customer accepted the offer to request an exception review.", **{**base, "entities": {"confirmed_exception": True}})
        hit = _build_index().get(_norm(msg))
        angry = bool(re.search(r"!|today|right now|unacceptable|terrible|ridiculous", msg)) and bool(re.search(r"!|sorted|unacceptable|terrible", msg))
        sentiment = "angry" if (hit and hit["style"] == "angry") or angry else "neutral"
        if hit:
            intent = hit["intent"]
            if intent == "unknown_or_ambiguous":
                return TriageResult(primary_intent=intent, confidence=0.4, urgency="medium", sentiment=sentiment, needs_clarification=True, clarifying_question=CLARIFY_Q,
                                    rationale="The message does not say what is wrong.", **base)
            sec = [hit["secondary"]] if hit["secondary"] else []
            return TriageResult(primary_intent=intent, confidence=hit["conf"] if hit["style"] == "primary" else (0.92 if sentiment == "angry" else 0.9),
                                urgency=hit["priority"] if hit["priority"] in ("low", "medium", "high", "critical") else "medium", sentiment=sentiment,
                                churn_risk="high" if sentiment == "angry" else "medium" if intent in ("refund_request", "subscription_cancellation") else "low",
                                rationale=f"Message matches the '{intent}' pattern.", **{**base, "secondary_intents": sec})
        low = msg.lower()
        found = [(i, c) for pat, i, c in KEYWORDS if re.search(pat, low)]
        if "charged" in low and "twice" in low:
            found = [("duplicate_charge", 0.9)] + found
        if not found:
            return TriageResult(primary_intent="unknown_or_ambiguous", confidence=0.35, urgency="low", sentiment=sentiment, needs_clarification=True,
                                clarifying_question=CLARIFY_Q, rationale="No clear request found.", **base)
        primary, conf = found[0]
        sec = [i for i, _ in found[1:] if i != primary][:2]
        return TriageResult(primary_intent=primary, confidence=conf, urgency="medium", sentiment=sentiment,
                            churn_risk="medium" if primary in ("refund_request", "subscription_cancellation") else "low",
                            rationale=f"Keyword match for '{primary}'.", **{**base, "secondary_intents": sec})

    # ------------------------------------------------------------------ resolver
    def run_tools(self, system, messages, tools, tool_executor: Callable[[str, dict], dict], max_steps=6, tier="strong") -> ToolRunResult:
        ctx = json.loads(messages[0]["content"])
        avail = {t["name"] for t in tools}
        res = ToolRunResult()

        def call(name: str, **args):
            if name not in avail:
                return None
            r = tool_executor(name, args)
            res.calls.append({"name": name, "args": args, "result": r, "duration_ms": 0})
            res.steps += 1
            return r

        _script(ctx["intent"], ctx, call)
        for sec in ctx.get("secondary_intents", [])[:2]:
            _script_read_only(sec, ctx, call)
        _add_meta("mock-" + tier, 0, 0, 1, False)
        return res

    def generate(self, system: str, messages: list[dict], tier: str = "fast") -> str:
        _lag(1.1)
        _add_meta("mock-" + tier, 0, 0, 1, False)
        return compose(json.loads(messages[-1]["content"]))

    def _qa(self, d: dict) -> QAScores:
        chk = d["deterministic_checks"]
        text = " ".join(d.get("assistant_replies", [])).lower()
        empathy = 5 if re.search(r"sorry|understand|thank you for|happy to help", text) else 4
        facts = 5 if chk.get("facts_consistent", True) else 2
        pol = 5 if chk["policy_violations"] == 0 else 2
        res_ = 5 if d["status"] in ("resolved", "awaiting_human", "escalated") or d.get("had_action") else 4
        if d["status"] == "awaiting_customer" and not d.get("had_action"):
            res_ = 3
        safety = 5 if chk["safety_ok"] else 1
        return QAScores(empathy=empathy, factual_accuracy=facts, policy_compliance=pol, resolution=res_, safety=safety, explanations={
            "empathy": "Acknowledges the customer's situation in a warm, professional tone.",
            "factual_accuracy": "Statements match the data returned by the tools." if facts == 5 else "Some statements could not be verified against tool results.",
            "policy_compliance": "No refund or credit was promised; every rule was applied by code." if pol == 5 else "A policy violation was detected by validation.",
            "resolution": "The customer received an answer, an action or a clearly described next step.",
            "safety": "Safety-sensitive content was handled correctly." if safety == 5 else "Safety escalation was not completed.",
        })


# ====================================================================== scripts
def _last(calls_or_res, name):
    return None


def _first_upcoming(call):
    r = call("get_lessons", scope="upcoming")
    return (r or {}).get("next_lesson"), r


def _script_read_only(intent: str, ctx: dict, call) -> None:
    if intent in ("technical_login",):
        call("get_account_status"); call("get_technical_events")
    elif intent == "technical_lesson_access":
        call("get_technical_events")
    elif intent == "duplicate_charge" or intent == "payment_failed":
        call("get_payment_history")
    elif intent in ("lesson_schedule_lookup", "lesson_reschedule", "late_cancellation"):
        call("get_lessons", scope="upcoming")
    elif intent == "progress_question":
        call("get_learning_progress")
    elif intent == "faq":
        call("search_knowledge_base", query=ctx["customer_message"])
    elif intent in ("subscription_pause", "subscription_plan_change", "subscription_cancellation"):
        call("get_subscription")


def _months_from(msg: str) -> int:
    m = re.search(r"(\d+)\s*month", msg.lower())
    return min(int(m.group(1)), 12) if m else 1


def _script(intent: str, ctx: dict, call) -> None:
    msg = ctx["customer_message"]
    ents = ctx.get("entities") or {}
    if intent == "duplicate_charge":
        r = call("get_payment_history")
        call("get_subscription")
        d = ((r or {}).get("compare_payments") or {})
        if d.get("duplicate_found"):
            dup = d["duplicate"]
            call("escalate_to_human", kind="refund_review", reason="duplicate_charge", entity_id=dup["duplicate_payment_id"],
                 summary=f"Duplicate charge: {dup['original_payment_id']} and {dup['duplicate_payment_id']} for ${dup['amount_usd']:.2f}, {dup['minutes_apart']} min apart; second added {dup['credits_b']} credits.")
    elif intent == "refund_request":
        call("get_subscription")
        p = call("check_policy", policy="refund")
        if p and p.get("verdict", {}).get("allowed"):
            call("escalate_to_human", kind="refund_review", entity_id=p["subscription_id"], reason="customer_request", summary="Customer requests a refund for unused paid lessons: " + msg[:200])
    elif intent in ("lesson_reschedule",):
        nxt, _ = _first_upcoming(call)
        if nxt:
            p = call("check_policy", policy="reschedule", lesson_id=nxt["lesson_id"])
            if p and p["verdict"]["allowed"]:
                s = call("get_available_slots", lesson_id=nxt["lesson_id"])
                if s and s.get("slots"):
                    call("reschedule_lesson", lesson_id=nxt["lesson_id"], new_start=s["slots"][0]["start"])
    elif intent == "late_cancellation":
        nxt, _ = _first_upcoming(call)
        if nxt:
            p = call("check_policy", policy="cancel_lesson", lesson_id=nxt["lesson_id"])
            if ents.get("confirmed_exception") and p and not p["verdict"]["allowed"]:
                call("create_ticket", type="late_cancellation_exception", linked_entity_id=nxt["lesson_id"], priority="high",
                     summary=f"Customer requests an exception for late cancellation of {nxt['lesson_id']} (starts in {nxt['starts_in']}).")
    elif intent == "tutor_no_show":
        r = call("get_lessons", scope="recent")
        ns = next((l for l in (r or {}).get("lessons", []) if l.get("no_show_review")), None)
        if ns:
            p = call("check_policy", policy="tutor_no_show", lesson_id=ns["lesson_id"])
            if p and p["verdict"]["allowed"]:
                call("escalate_to_human", kind="credit_lesson_review", entity_id=ns["lesson_id"], summary=f"Tutor no-show on {ns['lesson_id']}: tutor did not join, student waited {ns['minutes_waited_for_tutor']} min.")
    elif intent == "tutor_change":
        call("get_lessons", scope="all")
        call("escalate_to_human", kind="tutor_change", summary="Customer asks for a different tutor: " + msg[:200])
    elif intent == "substitute_tutor":
        nxt, _ = _first_upcoming(call)
        call("escalate_to_human", kind="substitute_tutor", entity_id=(nxt or {}).get("lesson_id"), summary="Tutor unavailable; customer requests a substitute for the next lesson.")
    elif intent == "payment_failed":
        call("get_payment_history"); call("search_knowledge_base", query="payment failed retry")
    elif intent == "subscription_cancellation":
        call("get_subscription")
        p = call("check_policy", policy="subscription_cancellation")
        if p and p["verdict"]["allowed"]:
            call("escalate_to_human", kind="cancellation_review", entity_id=p["subscription_id"], summary="Customer asks to cancel the subscription and stop the next renewal.")
    elif intent == "subscription_pause":
        r = call("get_subscription")
        months = _months_from(msg)
        sub = ((r or {}).get("subscriptions") or [None])[0]
        if sub:
            p = call("check_policy", policy="pause", subscription_id=sub["subscription_id"], months=months)
            if p and p["verdict"]["allowed"]:
                call("submit_pause_request", subscription_id=sub["subscription_id"], months=months)
    elif intent == "subscription_plan_change":
        call("get_subscription")
        call("escalate_to_human", kind="plan_change", summary="Customer wants to change lessons per week: " + msg[:200], details=msg[:200])
    elif intent == "technical_login":
        a = call("get_account_status"); call("get_technical_events"); call("search_knowledge_base", query="cannot log in login code expired")
        if a and a.get("locked"):
            call("create_ticket", type="technical_issue", priority="high", summary="Customer cannot log in and the account is locked pending verification.")
    elif intent == "technical_lesson_access":
        call("get_technical_events"); r = call("get_lessons", scope="upcoming"); call("search_knowledge_base", query="Join Lesson button classroom access")
        nxt = (r or {}).get("next_lesson")
        call("create_ticket", type="lesson_access_issue", priority="high", linked_entity_id=(nxt or {}).get("lesson_id"),
             summary="Customer reports the Join Lesson button does not work; troubleshooting steps shared.")
    elif intent == "progress_question":
        call("get_learning_progress")
    elif intent == "lesson_schedule_lookup":
        call("get_lessons", scope="upcoming")
    elif intent == "faq":
        call("search_knowledge_base", query=msg)


# ====================================================================== plan builder
def _results(calls: list[dict], name: str) -> list[dict]:
    return [c["result"] for c in calls if c["name"] == name and isinstance(c.get("result"), dict)]


def _money(x) -> str:
    return f"${x:,.2f}"


def build_plan(d: dict) -> ResolutionPlan:
    intent, calls, mode = d["intent"], d["tool_calls"], d.get("mode", "act")
    R = lambda n: (_results(calls, n) or [None])[-1]
    esc, tick, conf = R("escalate_to_human"), R("create_ticket"), R("reschedule_lesson") or R("submit_pause_request")
    facts: list[str] = []
    ev: list[str] = []
    taken: list[str] = []
    proposed: list[str] = []
    outcome, needs_human, why, prio = "answered", False, None, d.get("urgency", "medium")
    if prio not in ("low", "medium", "high", "critical"):
        prio = "medium"

    def blocked(res):  # first guardrail-blocked write result
        return bool(res) and res.get("error") == "blocked_by_guardrail"

    if esc and esc.get("ok") and esc.get("pending_action"):
        pa = esc["pending_action"]
        taken.append(f"Created ticket {esc['ticket_id']} ({esc['team']})")
        proposed.append(f"{pa['type']} {pa['action_id']} awaiting human approval")
        ev += pa.get("evidence", [])
        outcome, needs_human, why = "human_review_requested", True, f"{pa['type']} requires a human specialist"
    elif tick and tick.get("ok"):
        taken.append(f"Created ticket {tick['ticket_id']} ({tick['team']})")
        outcome = "action_taken"
    if conf and conf.get("ok"):
        proposed.append(conf["summary"])
        outcome = "confirmation_proposed"

    ph, sub = R("get_payment_history"), R("get_subscription")
    first_sub = ((sub or {}).get("subscriptions") or [None])[0]

    if intent == "duplicate_charge":
        cmp_ = (ph or {}).get("compare_payments", {})
        if cmp_.get("duplicate_found"):
            dup = cmp_["duplicate"]
            ev += [dup["original_payment_id"], dup["duplicate_payment_id"]]
            facts += [f"I compared your recent payments: two charges of {_money(dup['amount_usd'])} ({dup['original_payment_id']} and {dup['duplicate_payment_id']}) were made {dup['minutes_apart']} minutes apart for the same subscription, and the second one did not add any lesson credits. That looks like a duplicate charge.",
                      "I've submitted a refund review for the duplicate payment to our billing team." + (f" Your reference is {esc['ticket_id']}." if esc else ""),
                      "A support specialist has to approve it, so nothing has been refunded yet."]
            prio = "high"
            if mode == "read_only" and not esc:
                proposed.append("Submit refund review (needs customer confirmation)"); outcome = "answered"
                facts[1:] = ["Before I submit a refund review to our billing team, could you confirm you'd like me to go ahead?"]
        else:
            pairs = cmp_.get("pairs", [])
            p = pairs[-1] if pairs else None
            ev += [x for x in ([p["payment_a"], p["payment_b"]] if p else [])]
            nb = (ph or {}).get("billing", [{}])[0].get("next_billing_date")
            facts += ["I checked your recent payments and I don't see a duplicate charge."]
            if p:
                facts.append(f"The two charges of {_money(p['amount_usd'])} ({p['payment_a']} and {p['payment_b']}) are about {int(round(p['days_apart']))} days apart and each one added its own lesson credits, so they are regular monthly renewals.")
            if nb:
                facts.append(f"Your next billing date is {nb}.")
            facts.append("No ticket is needed. If something still looks wrong on your bank statement, tell me and I'll ask a specialist to take a look.")
            outcome = "no_action_needed"
    elif intent == "refund_request":
        pol = R("check_policy")
        v = (pol or {}).get("verdict", {})
        if esc and esc.get("ok"):
            p = esc["pending_action"]["params"]
            facts += [f"I understand, and I'm sorry the lessons haven't met your expectations.",
                      f"You have {p['unused_paid_lessons']} unused paid lessons, which is what refunds can cover. I've submitted a refund review to our billing team" + (f" (reference {esc['ticket_id']})" if esc else "") + ".",
                      "Refunds are never automatic: a support specialist reviews each request and makes the final decision, so I can't promise an outcome yet."]
            prio = "medium"
        elif v and not v.get("allowed"):
            facts += [v.get("reason", "There are no unused paid lessons to refund."), "Refunds can only cover unused paid lessons and always need a specialist's approval."]
            outcome = "policy_blocked"
        elif v:
            facts += [f"You have {v['unused_paid']} unused paid lessons, which is the only thing a refund can cover. Refunds are never automatic: a support specialist reviews every request and decides.",
                      "If you'd like me to submit a refund review, tell me briefly why you'd like one and I'll send it to our billing team."]
            proposed.append("Submit refund review (needs customer confirmation)")
    elif intent == "lesson_reschedule":
        pol = R("check_policy")
        v = (pol or {}).get("verdict", {})
        nxt = (R("get_lessons") or {}).get("next_lesson") or {}
        if conf and conf.get("ok"):
            facts += [f"Your next lesson starts in {nxt.get('starts_in', 'more than 6 hours')}, so it can be moved free of charge.",
                      f"The earliest time that suits the tutor's schedule is {conf['label']}."]
            alts = conf.get("alternatives") or []
            if alts:
                facts.append("Other options: " + ", ".join(a["label"] for a in alts) + ".")
        elif v and not v.get("allowed"):
            facts += [f"I can't move this lesson for free because it starts in {nxt.get('starts_in', 'under 6 hours')} and free changes need more than 6 hours' notice.", "Lessons inside that window are treated as late changes and the lesson is deducted from the balance. I can ask a specialist to review an exception if you'd like."]
            outcome = "policy_blocked"
        else:
            facts.append("I couldn't find an upcoming lesson to move.")
    elif intent == "late_cancellation":
        pol = R("check_policy")
        v = (pol or {}).get("verdict", {})
        if tick and tick.get("ok"):
            facts += ["Thank you. I've asked a scheduling specialist to review an exception for this lesson.", f"Your reference is {tick['ticket_id']}. We'll message you here once it has been reviewed, and I can't promise the outcome yet."]
            prio = "high"
        elif v and not v.get("allowed"):
            nxt = (R("get_lessons") or {}).get("next_lesson") or {}
            facts += [f"I'm sorry, I can't cancel this lesson for free: it starts in {nxt.get('starts_in', 'under 6 hours')}, and free cancellation needs more than 6 hours' notice.",
                      "Because it falls inside the 6-hour window, the lesson would be deducted from your balance.",
                      "Your options: keep the lesson, or I can ask a specialist to review an exception. Would you like me to ask a specialist to review an exception?"]
            outcome = "policy_blocked"
        elif v:
            facts += [f"Good news: your lesson starts in {((R('get_lessons') or {}).get('next_lesson') or {}).get('starts_in', 'more than 6 hours')}, so it can be cancelled free of charge.", "You can cancel or move it free of charge from the Lessons page, or ask me to find a new time."]
    elif intent == "tutor_no_show":
        pol = R("check_policy")
        v = (pol or {}).get("verdict", {})
        if esc and esc.get("ok"):
            facts += ["I'm sorry about that. I checked the lesson record and the tutor never joined, while your student waited " + str(v.get("minutes_waited", "")) + " minutes.",
                      "I've sent a request to our educational team to review returning the lesson to your balance" + (f" (reference {esc['ticket_id']})" if esc else "") + ". A specialist has to approve it, so it isn't credited yet."]
            prio = "high"
        else:
            facts.append("I couldn't find a recorded tutor no-show for your recent lessons. If you tell me which lesson it was, I'll take another look.")
    elif intent == "subscription_cancellation":
        pol = R("check_policy"); v = (pol or {}).get("verdict", {})
        if esc and esc.get("ok"):
            facts += ["I'm sorry to hear you're thinking of leaving. I've submitted a cancellation request to our billing team" + (f" (reference {esc['ticket_id']})" if esc else "") + ".",
                      f"Cancellations can't happen mid-period: if approved, renewal stops and the subscription ends on {v.get('effective_on')}, after your paid lessons.",
                      "We need 3 business days' notice before the next billing date" + (" and that is met." if v.get("notice_ok") else ", which may not be met, so one more renewal could still be charged. A specialist will confirm.")]
    elif intent == "subscription_pause":
        pol = R("check_policy"); v = (pol or {}).get("verdict", {})
        if conf and conf.get("ok"):
            facts += [f"A pause is possible: {v.get('reason')}", f"{conf['summary']} Please confirm below; nothing changes until you do."]
        elif v and not v.get("allowed"):
            facts += [f"I can't pause the subscription right now: {v['reason']}"]
            outcome = "policy_blocked"
    elif intent == "subscription_plan_change":
        if esc and esc.get("ok"):
            facts += [f"Your plan currently includes {first_sub['lessons_per_week']} lessons per week." if first_sub else "I can help with that.",
                      "Plan changes are applied by a support specialist. I've sent your request to the billing team" + (f" (reference {esc['ticket_id']})" if esc else "") + ", and they'll confirm the options and timing.",
                      "If you tell me how many lessons per week you'd like, I'll add it to the request."]
    elif intent == "tutor_change":
        if esc and esc.get("ok"):
            facts += ["Thank you for telling us. A good teaching fit matters. I've sent a tutor change request to our educational department" + (f" (reference {esc['ticket_id']})" if esc else "") + ".",
                      "A specialist will review it and propose a new tutor. Nothing changes until they confirm."]
    elif intent == "substitute_tutor":
        if esc and esc.get("ok"):
            facts += ["I'm sorry your tutor isn't available. I've asked our educational department to arrange a substitute for your next lesson" + (f" (reference {esc['ticket_id']})" if esc else "") + ".",
                      "They'll confirm the substitute here as soon as it is arranged."]
            prio = "medium"
    elif intent == "payment_failed":
        fails = [p for p in (ph or {}).get("payments", []) if p["status"] == "failed"]
        pm = (ph or {}).get("default_payment_method") or {}
        if fails:
            f = fails[-1]
            ev.append(f["payment_id"])
            facts += [f"Your payment {f['payment_id']} for {_money(f['amount_usd'])} on {f['processed_at'][:10]} failed. {f.get('failure_explanation') or 'The payment was not completed.'}"]
            steps = "Please check that your card is active" + (" (it looks expired)" if pm.get("expired") else "") + " and retry the payment from Billing."
            facts += [steps, "If the retry fails, tell me and I'll pass it to our billing team. Your subscription stays in a short grace period meanwhile."]
        else:
            facts.append("I don't see a failed payment on your account. Everything in your recent payment history was successful.")
            outcome = "no_action_needed"
    elif intent == "technical_login":
        ev_ = R("get_technical_events") or {}
        acct = R("get_account_status") or {}
        e = next((x for x in reversed(ev_.get("events", [])) if x.get("error_code")), None)
        if e:
            facts.append(f"I can see a recent failed login with error {e['error_code']}. {e.get('explanation') or ''}".strip())
        if acct.get("locked"):
            facts.append("Your account is waiting for verification, so I've asked our technical team to help" + (f" (reference {tick['ticket_id']})" if tick else "") + ".")
        facts.append("Please request a new login code, use the email on the account and enter the code within a few minutes (check your spam folder). Your account itself is in good standing." if not acct.get("locked") else "Meanwhile, please keep the verification email handy.")
    elif intent == "technical_lesson_access":
        facts += ["I'm sorry the Join Lesson button isn't working. Please try the latest Chrome, Edge or Safari, allow camera and microphone access, turn off VPNs or pop-up blockers, and reload the lesson page a few minutes before the start.",
                  "I've also asked our technical team to check it" + (f" (reference {tick['ticket_id']})" if tick else "") + ". Please share the lesson time if it still fails."]
        prio = "high"
    elif intent == "progress_question":
        pr = ((R("get_learning_progress") or {}).get("progress") or [None])[0]
        if pr:
            facts += [f"Latest report ({pr['subject']}): current level is {pr['current_level']}, with attendance at {pr['attendance_rate_percent']}%.",
                      "Strengths: " + ", ".join(pr["strengths"]) + ". Focus areas: " + ", ".join(pr["focus_areas"]) + ".",
                      f"Tutor's comment: {pr['teacher_comment']}"]
            facts.append("Next practice: a few short sessions each week on " + " and ".join(pr["focus_areas"]) + ".")
        else:
            facts.append("I don't see a progress report yet; one appears after the first lessons.")
        outcome = "answered"
    elif intent == "lesson_schedule_lookup":
        nxt = (R("get_lessons") or {}).get("next_lesson")
        facts.append(f"Your next lesson is {nxt['subject']} with {nxt['tutor_name']} on {nxt['scheduled_start'][:16].replace('T', ' ')} UTC (in {nxt['starts_in']})." if nxt else "I don't see an upcoming lesson on the account.")
    elif intent == "faq":
        kb = (R("search_knowledge_base") or {}).get("results", [])
        if kb:
            facts.append(kb[0]["snippet"]); ev.append(kb[0]["id"])
        else:
            facts.append("I couldn't find an exact help article for that.")

    if not facts:
        facts.append("I've looked at your account. Before I submit anything to our team, could you confirm the details and that you'd like me to go ahead?" if mode == "read_only" else "I've reviewed your account details but couldn't complete this request automatically.")
    if d.get("injection"):
        facts.insert(0, "I can't skip or change our rules based on instructions in a message, but I'm glad to help within them.")
    for c in calls:                                   # surface blocked guardrails as policy_blocked
        if blocked(c.get("result")) and outcome in ("answered",):
            outcome = "policy_blocked"
    sec = d.get("secondary_intents") or []
    if sec:
        s_facts = _secondary_facts(sec, calls)
        facts += s_facts
    return ResolutionPlan(summary=f"{intent}: {outcome}", outcome=outcome, evidence=list(dict.fromkeys(ev)), actions_taken=taken, actions_proposed=proposed,
                          needs_human=needs_human, needs_human_reason=why, customer_reply_facts=facts, recommended_priority=prio)


def _secondary_facts(sec: list[str], calls) -> list[str]:
    out = []
    for s in sec:
        if s == "technical_login":
            ev = (_results(calls, "get_technical_events") or [{}])[-1]
            e = next((x for x in reversed(ev.get("events", [])) if x.get("error_code")), None)
            out.append("On the login problem: " + (f"I can see a failed login (error {e['error_code']}): {e.get('explanation')}" if e else "I don't see failed logins, so please try requesting a new login code."))
        elif s == "duplicate_charge":
            out.append("I'll handle the charge question separately; please tell me if you want me to look at it.")
    return out


# ====================================================================== composer
def compose(d: dict) -> str:
    plan, first, sentiment, writer = d["plan"], d.get("first_name") or "there", d.get("sentiment", "neutral"), d.get("writer", "parent")
    facts = plan["customer_reply_facts"]
    if plan["outcome"] == "needs_clarification":
        return facts[0]
    parts = [f"Hi {first},"]
    if sentiment in ("angry", "frustrated") and not any("sorry" in f.lower() for f in facts):
        parts.append("I'm sorry for the trouble, and I understand how frustrating this is.")
    body = " ".join(facts)
    closing = {
        "confirmation_proposed": "Please confirm using the card below. Nothing changes until you do.",
        "human_review_requested": "You don't need to do anything else. We'll message you here when a specialist has decided.",
        "no_action_needed": "",
        "policy_blocked": "",
    }.get(plan["outcome"], "Is there anything else I can help with?")
    if writer == "student":
        body = " ".join(f for f in facts[:3])
    return "\n\n".join(x for x in [" ".join(parts), body, closing] if x).strip()
