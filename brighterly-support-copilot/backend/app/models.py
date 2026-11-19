"""Pydantic models: LLM structured outputs and API payloads."""
from __future__ import annotations
from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

Intent = Literal[
    "faq", "lesson_schedule_lookup", "lesson_reschedule", "late_cancellation", "tutor_no_show",
    "tutor_change", "substitute_tutor", "payment_failed", "duplicate_charge", "refund_request",
    "subscription_cancellation", "subscription_pause", "subscription_plan_change", "technical_login",
    "technical_lesson_access", "progress_question", "unknown_or_ambiguous", "child_safety_concern",
]


class TriageResult(BaseModel):
    primary_intent: Intent
    secondary_intents: list[Intent] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    urgency: Literal["low", "medium", "high", "critical"] = "medium"
    sentiment: Literal["positive", "neutral", "frustrated", "angry", "distressed"] = "neutral"
    writer: Literal["parent", "student", "unknown"] = "unknown"
    safety_flag: bool = False
    needs_clarification: bool = False
    clarifying_question: Optional[str] = None
    churn_risk: Literal["low", "medium", "high"] = "low"
    entities: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class ResolutionPlan(BaseModel):
    summary: str
    outcome: Literal[
        "answered", "action_taken", "confirmation_proposed", "human_review_requested", "policy_blocked",
        "no_action_needed", "needs_clarification", "escalated",
    ] = "answered"
    evidence: list[str] = Field(default_factory=list)
    actions_taken: list[str] = Field(default_factory=list)
    actions_proposed: list[str] = Field(default_factory=list)
    needs_human: bool = False
    needs_human_reason: Optional[str] = None
    customer_reply_facts: list[str] = Field(default_factory=list)
    recommended_priority: Literal["low", "medium", "high", "critical"] = "medium"


class QAScores(BaseModel):
    empathy: int = Field(ge=1, le=5)
    factual_accuracy: int = Field(ge=1, le=5)
    policy_compliance: int = Field(ge=1, le=5)
    resolution: int = Field(ge=1, le=5)
    safety: int = Field(ge=1, le=5)
    explanations: dict[str, str] = Field(default_factory=dict)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionRequest(_Strict):
    user_id: str


class ChatRequest(_Strict):
    conversation_id: Optional[str] = None
    message: str = Field(min_length=1, max_length=2000)


class ConfirmRequest(_Strict):
    conversation_id: str
    confirmation_token: str


class RejectRequest(_Strict):
    reason: str = Field(min_length=1, max_length=500)
