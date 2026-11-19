"""Deterministic safety pre-filter and prompt-injection detector. Runs BEFORE any LLM call."""
from __future__ import annotations
import re

_SAFETY = {
    "secrecy": r"(keep|keeping) (it|this|that|things?) (a )?secret|our secret|(not|n'?t|never) (to )?tell (your|my|our|his|her|their|the|us|anyone|mom|dad|parents)|don'?t tell (your|my|our) (parents|mom|dad)|told me not to tell",
    "outside_platform": r"another app|other app|outside (of )?(the )?(brighterly|platform|class|lesson|website)|whatsapp|snapchat|telegram|instagram|discord|tiktok|personal (phone )?(number|email)|(chat|talk|message|text|contact)\w* (me |us |her |him )?(on|via|through) (a |an |another |other |different )",
    "outside_link": r"(sent|gave|shared|posted)\b.{0,30}\b(link|url)\b.{0,40}(outside|external|another|website)|link to an? (outside|external)",
    "inappropriate": r"inappropriate|sexual|nude|naked|touch(ed|ing)|(asked|wants?) (for )?(a )?(photo|picture|pic)s?\b",
    "fear_unsafe": r"(don'?t|do not|doesn'?t|does not) (feel|make me feel) (safe|comfortable)|feel unsafe|unsafe|(scared|afraid|frightened|uncomfortable) (of|around|with|by|when) (my|the|his|her|our) (tutor|teacher)|(tutor|teacher)\b.{0,40}\b(scares?|frightens?|uncomfortable)|something (doesn'?t|does not) feel right|worried about the way a (tutor|teacher)",
    "self_harm": r"kill myself|hurt myself|self.?harm|want to die|suicid|end my life|cut myself",
}
_IMMEDIATE = {"self_harm"}

_INJECT = re.compile(
    r"ignore (all |any |the |your |previous |prior |above |these )*(rules|instructions|polic(y|ies)|guidelines|restrictions|limits)|disregard (all |your |the |previous )*(rules|instructions|polic)|"
    r"(reveal|show|print|repeat).{0,20}(system|hidden|initial) (prompt|instructions)|system prompt|developer mode|jailbreak|you are now\b|pretend (that )?you|act as (an? )?(admin|agent|manager|supervisor)|"
    r"override (the )?(policy|rules|limit)|bypass (the )?(policy|rules|approval|check)|as an? (admin|supervisor|support agent)|new instructions:", re.I)


def prefilter(text: str) -> dict:
    hits = [k for k, pat in _SAFETY.items() if re.search(pat, text, re.I)]
    return {"flag": bool(hits), "matches": hits, "immediate_danger": any(h in _IMMEDIATE for h in hits)}


def injection(text: str) -> dict:
    m = _INJECT.search(text)
    return {"detected": bool(m), "match": m.group(0)[:60] if m else None}


HOLDING_PARENT = ("Thank you for telling us. We take this very seriously. I've passed your message to our Trust & Safety team right away, "
                  "and a specialist will contact you shortly. You don't need to do anything else right now.")
HOLDING_STUDENT = ("Thank you for telling us. That was the right thing to do. A specialist from our team will contact you shortly. "
                   "If you ever feel unsafe, please talk to a parent or another adult you trust right away.")
HOLDING_DANGER = (" If anyone is in immediate danger, please contact your local emergency services or a trusted adult right now.")


def holding_message(account_type: str, immediate: bool) -> str:
    base = HOLDING_STUDENT if account_type == "student" else HOLDING_PARENT
    return base + (HOLDING_DANGER if immediate else "")
