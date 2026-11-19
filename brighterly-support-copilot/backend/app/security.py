"""Masking, signed demo sessions and a tiny in-memory rate limiter."""
from __future__ import annotations
import re, time, threading
from collections import defaultdict, deque
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from .config import settings

_EMAIL = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_PHONE = re.compile(r"(?<!\w)(\+?\d[\d\s().-]{7,}\d)(?!\w)")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def mask(text: str) -> str:
    if not isinstance(text, str):
        return text
    text = _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", text)
    text = _CARD.sub("[card-number-masked]", text)
    text = _PHONE.sub(lambda m: "***" + re.sub(r"\D", "", m.group(1))[-2:], text)
    return text


def mask_obj(o):
    if isinstance(o, str):
        return mask(o)
    if isinstance(o, dict):
        return {k: mask_obj(v) for k, v in o.items()}
    if isinstance(o, list):
        return [mask_obj(v) for v in o]
    return o


def mask_email(e: str) -> str:
    return mask(e or "")


def _ser() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="brighterly-demo-session")


def make_token(user_id: str | None, role: str) -> str:
    return _ser().dumps({"user_id": user_id, "role": role})


def read_token(token: str, max_age: int = 8 * 3600) -> dict | None:
    try:
        return _ser().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


_hits: dict[str, deque] = defaultdict(deque)
_rl_lock = threading.Lock()


def rate_limited(key: str, limit: int | None = None, window: int = 60) -> bool:
    limit = limit or settings.rate_limit_per_min
    now = time.monotonic()
    with _rl_lock:
        q = _hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return True
        q.append(now)
        return False
