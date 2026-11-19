"""Plays the demo scenarios against a running server and prints what happened (for rehearsals).
Usage: python tasks.py demo [--base http://localhost:8000] [--only dup,safety] [--no-reset]
"""
import argparse, json, sys, urllib.request, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.scenarios import SCENARIOS
from app.config import settings

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://localhost:" + os.environ.get("PORT", "8000"))
ap.add_argument("--only", default="")
ap.add_argument("--no-reset", action="store_true")
args = ap.parse_args()
BASE = args.base.rstrip("/")


def post(path, body=None, token=None, headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(), headers=h, method="POST")
    return urllib.request.urlopen(req)


def stream(token, message, cid=None):
    evs = []
    with post("/api/chat/stream", {"message": message, **({"conversation_id": cid} if cid else {})}, token) as r:
        for line in r:
            if line.strip():
                evs.append(json.loads(line))
    return evs


ICON = {"safety_check": "🛡", "triage": "🧭", "tool_call": "🔧", "guardrail": "⚖", "plan": "📋", "compose": "✍", "reply_validation": "✔",
        "ticket_created": "🎫", "pending_action": "⏳", "confirmation_request": "❓", "escalation": "🚨", "human_decision": "👤", "qa": "⭐", "error": "❌"}


def show(evs):
    for e in evs:
        if e["type"] == "final":
            print(f"   ➜ FINAL [{e['data']['status']}] {e['data']['reply']}")
        elif e["status"] != "running":
            print(f"   {ICON.get(e['step'], '·')} {e['step']:<18}{e['status']:<8} {e['title']} | {e['summary'][:110]}")


if not args.no_reset:
    post("/api/admin/reset", headers={"X-Admin-Token": settings.admin_token}).read()
agent = json.load(post("/api/session/agent"))["token"]
only = set(filter(None, args.only.split(",")))
for sc in SCENARIOS:
    if only and sc["id"] not in only:
        continue
    print(f"\n=== {sc['label']}  [{sc['user_id']}]  expect: {sc['expect']}")
    print(f"   CUSTOMER: {sc['message']}")
    tok = json.load(post("/api/session", {"user_id": sc["user_id"]}))["token"]
    evs = stream(tok, sc["message"])
    show(evs)
    final = next(e for e in evs if e["type"] == "final")["data"]
    cid = final["conversation_id"]
    for card in final["cards"]:
        if card["type"] == "confirmation":
            r = json.load(post("/api/chat/confirm", {"conversation_id": cid, "confirmation_token": card["token"]}, tok))
            print(f"   ✅ customer confirmed → {r['message']}")
        if card.get("action_id"):
            r = json.load(post(f"/api/actions/{card['action_id']}/approve", token=agent))
            show(r["events"])
            print(f"   👤 specialist → customer: {r['customer_message']['text']}")
    qa = json.load(post(f"/api/conversations/{cid}/close", token=tok))["qa"]
    print(f"   ⭐ QA overall {qa['overall']}/5  (empathy {qa['empathy']}, facts {qa['factual_accuracy']}, policy {qa['policy_compliance']}, resolution {qa['resolution']}, safety {qa['safety']})")
