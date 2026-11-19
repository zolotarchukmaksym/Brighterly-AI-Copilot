"""Stream event factory. One JSON object per line (NDJSON)."""
from __future__ import annotations
import time, uuid
from datetime import datetime, timezone
from ..config import settings


def new_event_id() -> str:
    return "EV-" + uuid.uuid4().hex[:8]


def make(run_id: str, step: str, status: str, title: str, summary: str = "", data: dict | None = None,
         duration_ms: int | None = None, autonomy: str | None = None, event_id: str | None = None, type_: str = "step") -> dict:
    return {"event_id": event_id or new_event_id(), "type": type_, "run_id": run_id, "step": step, "status": status, "title": title,
            "summary": summary, "data": data or {}, "duration_ms": duration_ms, "autonomy": autonomy,
            "prompt_version": settings.prompt_version, "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds")}
