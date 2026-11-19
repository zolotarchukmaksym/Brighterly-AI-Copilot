"""Code-level reply checks. The model cannot promise money or contradict policy."""
from __future__ import annotations
import re

_PROMISES = [
    r"\b(we|i)(\s+have|'ve|\s+will|'ll|\s+are going to)?\s+(refund|credit)(ed)?\b(?!\s+review)",
    r"\byou(\s+will|'ll)\s+(get|receive|be given)\s+(a |your |the )?(full\s+)?(refund|credit|money back)",
    r"\brefund\s+(has been|was|is being|is)\s+(issued|processed|approved|sent|guaranteed)",
    r"\bguarantee[sd]?\b.{0,30}\b(refund|credit)",
    r"\b(has|have)\s+been\s+(refunded|credited)\b",
    r"\bwill be (refunded|credited)\b",
]
_NEG = re.compile(r"\b(nothing|not|n't|no|never|isn't|aren't|yet|until|unless|if|once)\b", re.I)
_DONE_CLAIMS = [
    (r"\b(i|we)('ve| have)\s+(cancel+ed|paused|changed)\s+your\s+(subscription|plan)", "claims a subscription change was done"),
    (r"\byour\s+(subscription|plan)\s+(has been|is now)\s+(cancel+ed|paused|changed)", "claims a subscription change was done"),
    (r"\b(i|we)('ve| have)\s+(changed|replaced)\s+your\s+tutor", "claims a tutor change was done"),
]


def _sentences(t: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", t) if s.strip()]


def validate(reply: str, own_email: str | None, own_user_id: str, tool_calls: list[dict], confirmed_done: bool = False) -> list[str]:
    v: list[str] = []
    for s in _sentences(reply):
        for pat in _PROMISES:
            if re.search(pat, s, re.I) and not _NEG.search(s):
                v.append(f"promise_of_money: '{s[:80]}'")
                break
        for pat, why in _DONE_CLAIMS:
            if re.search(pat, s, re.I):
                v.append(f"false_action_claim: {why}")
    # policy contradiction: a blocked free-change verdict must not be described as free
    blocked_free = any((c.get("result") or {}).get("guardrail", {}).get("rule") == "free_change_over_6h" and not c["result"]["guardrail"].get("allowed") for c in tool_calls if isinstance(c.get("result"), dict))
    if blocked_free:
        for s in _sentences(reply):
            if re.search(r"(cancel|reschedul|mov)\w*.{0,50}(for free|free of charge|without (any )?(charge|penalty|fee))", s, re.I) and not re.search(r"\b(can'?t|cannot|not|unable|isn'?t|only|requires?|but)\b", s, re.I):
                v.append(f"policy_contradiction: '{s[:80]}'")
    # PII: foreign emails / user ids
    for e in re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", reply):
        if not own_email or e.lower() != own_email.lower():
            v.append("foreign_pii: email")
    for uid in re.findall(r"BR-USER-\d+", reply):
        if uid != own_user_id:
            v.append("foreign_pii: user id")
    return v


def safe_template(facts: list[str], violations: list[str]) -> str:
    bad = {x.split("'")[1] for x in violations if "'" in x}
    keep = [f for f in facts if not any(b[:60] in f for b in bad)]
    return " ".join(keep) if keep else ("I've passed your request to our team. A specialist will review it and follow up here. "
                                         "Nothing has been refunded, credited or changed yet.")
