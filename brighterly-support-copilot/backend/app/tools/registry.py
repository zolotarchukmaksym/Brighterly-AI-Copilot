"""Tool registry. Forbidden tools (execute_refund, charge_card, delete_payment, ...) are NOT registered at all."""
from __future__ import annotations
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable
from .. import db
from . import read_tools as R, action_tools as A


@dataclass
class ToolContext:
    user_id: str                      # from the signed session, never from the model
    conversation_id: str
    message: str = ""                 # verbatim customer message (for safety tickets)
    confidence: float = 1.0
    injection: bool = False
    emit: Callable[[dict], None] = lambda e: None


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict
    autonomy: str                     # auto | user_confirmation | human_approval
    fn: Callable
    writes: bool = False
    parameters_hint: list = field(default_factory=list)


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


S = lambda d, **kw: {"type": "string", "description": d, **kw}

TOOLS: list[ToolSpec] = [
    ToolSpec("get_account_status", "Get the signed-in customer's account status, lock state, login-failure count and student list (no secrets).", _obj({}), "auto", R.get_account_status),
    ToolSpec("get_subscription", "Get subscriptions: plan, status, billing dates, lesson balance, pause history, cancellation fields and plan options.", _obj({}), "auto", R.get_subscription),
    ToolSpec("get_payment_history", "Get recent payments, default payment method health and a deterministic comparison that tells whether two charges are a real duplicate or normal monthly renewals. Always use this before claiming a duplicate.", _obj({}), "auto", R.get_payment_history),
    ToolSpec("get_lessons", "Get lessons with status, hours until start, tutor, attendance and tutor no-show review status.", _obj({"scope": S("upcoming, recent or all", enum=["upcoming", "recent", "all"]), "lesson_id": S("optional specific lesson id")}), "auto", R.get_lessons),
    ToolSpec("get_learning_progress", "Get learning progress reports: level, strengths, focus areas, attendance, tutor comment.", _obj({}), "auto", R.get_learning_progress),
    ToolSpec("get_technical_events", "Get recent login and classroom technical events with error codes and explanations.", _obj({}), "auto", R.get_technical_events),
    ToolSpec("check_policy", "Deterministic policy verdict with reasons. policy: reschedule, cancel_lesson, pause, subscription_cancellation, refund, tutor_no_show. Never decide policy yourself, always call this.",
             _obj({"policy": S("policy to check", enum=["reschedule", "cancel_lesson", "pause", "subscription_cancellation", "refund", "tutor_no_show"]), "lesson_id": S("lesson id (defaults to next lesson)"), "subscription_id": S("subscription id"), "months": {"type": "integer", "description": "pause length in months"}, "payment_id": S("payment id")}, ["policy"]), "auto", R.check_policy),
    ToolSpec("get_available_slots", "Get deterministic available reschedule slots for a lesson (tutor timezone). Returns ISO start values to pass to reschedule_lesson.", _obj({"lesson_id": S("lesson id (defaults to next lesson)")}), "auto", R.get_available_slots),
    ToolSpec("search_knowledge_base", "Search the help-center FAQ. Returns top 3 snippets with ids.", _obj({"query": S("search query")}, ["query"]), "auto", R.search_knowledge_base),
    ToolSpec("ask_clarifying_question", "Ask the customer exactly one clarifying question. No side effects.", _obj({"question": S("the single question")}, ["question"]), "auto", R.ask_clarifying_question),
    ToolSpec("create_ticket", "Create a support ticket (idempotent per user+type+entity within 24h). Use for issues needing follow-up that are not money/structural changes. Do NOT create duplicate_charge tickets unless get_payment_history confirmed a real duplicate.",
             _obj({"type": S("ticket type", enum=A.TICKET_TYPES), "summary": S("short factual summary"), "priority": S("low, medium, high or critical", enum=["low", "medium", "high", "critical"]), "linked_entity_id": S("related lesson/payment/subscription id")}, ["type", "summary"]), "auto", A.create_ticket, writes=True),
    ToolSpec("reschedule_lesson", "Propose rescheduling a lesson to one of the slots from get_available_slots. The CUSTOMER must confirm in the chat; nothing changes until they do. Only after check_policy allows it.",
             _obj({"lesson_id": S("lesson id"), "new_start": S("ISO start exactly as returned by get_available_slots")}, ["lesson_id", "new_start"]), "user_confirmation", A.reschedule_lesson, writes=True),
    ToolSpec("submit_pause_request", "Propose pausing a subscription. The CUSTOMER must confirm in the chat. Only after check_policy allows it.",
             _obj({"subscription_id": S("subscription id"), "months": {"type": "integer", "description": "1-3"}}, ["subscription_id", "months"]), "user_confirmation", A.submit_pause_request, writes=True),
    ToolSpec("escalate_to_human", "Hand over to a human. Creates a ticket and, for money/structural matters, a pending action that ONLY a support specialist can approve. kind: refund_review (set reason='duplicate_charge' for a verified duplicate), credit_lesson_review (tutor no-show), cancellation_review, tutor_change, plan_change, substitute_tutor, general_review, child_safety (mandatory for any child-safety report).",
             _obj({"kind": S("escalation kind", enum=list(A.KIND_TO_TICKET)), "summary": S("factual summary with evidence"), "entity_id": S("payment/lesson/subscription id"), "reason": S("e.g. duplicate_charge"), "details": S("extra details such as requested lessons per week")}, ["kind", "summary"]), "human_approval", A.escalate_to_human, writes=True),
]
BY_NAME = {t.name: t for t in TOOLS}


def execute(ctx: ToolContext, name: str, args: dict[str, Any]) -> dict:
    spec = BY_NAME.get(name)
    if not spec:
        db.audit("system", "unknown_tool_refused", name, args, user_id=ctx.user_id)
        return {"ok": False, "error": "unknown_tool"}
    allowed = set(inspect.signature(spec.fn).parameters) - {"ctx"}
    clean = {k: v for k, v in (args or {}).items() if k in allowed}   # model-supplied acting_user_id etc. is dropped
    try:
        return spec.fn(ctx, **clean)
    except TypeError as e:
        return {"ok": False, "error": "bad_arguments", "detail": str(e)[:200]}


def declarations() -> list[dict]:
    return [{"name": t.name, "description": t.description, "parameters": t.parameters} for t in TOOLS]
