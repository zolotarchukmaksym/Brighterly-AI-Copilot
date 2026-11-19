"""All 'now' lookups go through here so the demo is reproducible (DEMO_NOW)."""
from __future__ import annotations
from datetime import datetime, timezone
from .config import settings


def parse_ts(s: str) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def now() -> datetime:
    return parse_ts(settings.demo_now)


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def now_iso() -> str:
    return iso(now())
