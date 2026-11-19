"""Single JSON-file data layer. No other module touches the JSON files."""
from __future__ import annotations
import json, os, shutil, threading, uuid, copy
from typing import Any
from .config import settings
from . import clock

RUNTIME_COLLECTIONS = ["tickets", "pending_actions", "conversations", "audit_log", "qa_scores", "confirmations"]
_lock = threading.RLock()
_state: dict[str, Any] | None = None
_user_index: dict[str, dict] = {}


def _seed_src():
    return settings.data_dir / "brighterly_support_dataset_v1.1.json"


def _paths():
    return settings.data_dir / "seed.json", settings.data_dir / "runtime.json"


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6].upper()}"


def _write(state: dict) -> None:
    _, rt = _paths()
    tmp = rt.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, rt)


def _index() -> None:
    _user_index.clear()
    for u in _state["users"]:
        _user_index[u["user_id"]] = u


def init() -> None:
    """Load runtime.json, creating it from the seed when missing."""
    global _state
    with _lock:
        seed, rt = _paths()
        if not seed.exists():
            shutil.copyfile(_seed_src(), seed)
        if not rt.exists():
            reset()
            return
        _state = json.loads(rt.read_text(encoding="utf-8"))
        for c in RUNTIME_COLLECTIONS:
            _state.setdefault(c, {} if c in ("conversations", "pending_actions", "confirmations") else [])
        _index()


def reset() -> None:
    global _state
    with _lock:
        seed, _ = _paths()
        if not seed.exists():
            shutil.copyfile(_seed_src(), seed)
        _state = json.loads(seed.read_text(encoding="utf-8"))
        for c in RUNTIME_COLLECTIONS:
            _state[c] = {} if c in ("conversations", "pending_actions", "confirmations") else []
        _index()
        _write(_state)


def save() -> None:
    with _lock:
        _write(_state)


def state() -> dict:
    if _state is None:
        init()
    return _state


# ---- seed-level reads -------------------------------------------------
def meta() -> dict:
    s = state()
    return {k: s[k] for k in ("dataset_metadata", "business_rules", "supported_intents", "safety_policy", "tool_catalog")}


def get_user(user_id: str) -> dict | None:
    state()
    return _user_index.get(user_id)


def list_users() -> list[dict]:
    return state()["users"]


# ---- runtime collections ---------------------------------------------
def add_ticket(t: dict) -> dict:
    with _lock:
        state()["tickets"].append(t)
        save()
    return t


def tickets() -> list[dict]:
    return state()["tickets"]


def put_pending(a: dict) -> dict:
    with _lock:
        state()["pending_actions"][a["action_id"]] = a
        save()
    return a


def get_pending(action_id: str) -> dict | None:
    return state()["pending_actions"].get(action_id)


def pending_actions() -> list[dict]:
    return list(state()["pending_actions"].values())


def put_confirmation(c: dict) -> dict:
    with _lock:
        state()["confirmations"][c["token"]] = c
        save()
    return c


def get_confirmation(token: str) -> dict | None:
    return state()["confirmations"].get(token)


def get_conversation(cid: str) -> dict | None:
    return state()["conversations"].get(cid)


def put_conversation(c: dict) -> None:
    with _lock:
        state()["conversations"][c["conversation_id"]] = c
        save()


def add_qa(q: dict) -> None:
    with _lock:
        state()["qa_scores"].append(q)
        save()


def audit(who: str, what: str, entity: str | None = None, params: dict | None = None, result: Any = None,
          confidence: float | None = None, user_id: str | None = None) -> dict:
    from .security import mask_obj
    rec = {
        "audit_id": new_id("AUD"), "who": who, "what": what, "entity": entity,
        "user_id": user_id, "params": mask_obj(params or {}), "result": mask_obj(result),
        "confidence": confidence, "prompt_version": settings.prompt_version, "timestamp": clock.now_iso(),
    }
    with _lock:
        state()["audit_log"].append(rec)
        save()
    return rec


def audit_log() -> list[dict]:
    return state()["audit_log"]


def mutate(fn) -> Any:
    """Run fn(state) under the lock then persist."""
    with _lock:
        r = fn(state())
        save()
        return r


def snapshot_user(user_id: str) -> dict | None:
    u = get_user(user_id)
    return copy.deepcopy(u) if u else None
