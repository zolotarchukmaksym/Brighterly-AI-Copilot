# Brighterly Support Copilot

An AI **operations assistant** for a tutoring platform (parents buy subscriptions, students take 45-minute lessons).
It is not a chatbot: it understands the customer's real goal, performs safe actions itself, proposes actions that need
confirmation, hands money and safety to humans, and **shows every step on the same page** (left: customer chat, right: live AI Activity Log).

Built for PolyTech Code Fest, "AI Customer Support Automation". Gemma 4 via the Gemini API, pure Python pipeline (no agent frameworks), vanilla HTML/CSS/JS front end (no build step, no CDN, no Docker).

## Quick start (about 5 minutes)

Requires Python 3.10+. The default provider is `mock` (offline, deterministic), so you need no API key to try everything.

**macOS / Linux**
```bash
cd backend
./run.sh                # creates .venv, installs deps, copies .env.example to .env, starts the server
```
**Windows**
```bat
cd backend
run.bat
```
Then open http://localhost:8000. Other commands (from `backend/`):

| Command | What it does |
|---|---|
| `python tasks.py dev` | start the server with auto-reload (`PORT` env var changes the port) |
| `python tasks.py reset` | recreate `data/runtime.json` from the seed dataset |
| `python tasks.py demo` | replay all demo scenarios against the running server and print what happened (rehearsals) |
| `python tasks.py zip` | build a clean `dist/brighterly-support-copilot.zip` (no `.env`, caches, `runtime.json`) |

To use the real model: edit `backend/.env`, set `LLM_PROVIDER=gemma` and `GEMINI_API_KEY=...` (get a key at ai.google.dev; check current Gemma 4 model ids there and adjust `GEMMA_MODEL_FAST` / `GEMMA_MODEL_STRONG`).

## What you see

1. Pick a customer (or **Scenarios**), press Send.
2. The right panel streams the real pipeline: safety check, triage (intent, confidence against the 0.6 / 0.85 thresholds, tone, urgency), tool calls with autonomy badges, guardrail decisions (`ALLOWED` / `BLOCKED` with the reason), the plan, reply validation, outcome.
3. Money and structural changes appear as **Pending human approval**: the specialist (you) clicks Approve or Reject in the log, and the customer receives the specialist's message in the chat.
4. **End conversation & run QA** scores empathy, factual accuracy, policy compliance, resolution and safety.

Demo scenarios (menu **Scenarios**): real duplicate charge (BR-USER-002), false alarm (BR-USER-017), late cancellation, reschedule with customer confirmation, child-safety report, ambiguous message, prompt-injection attempt, tutor no-show, pause. See `docs/DEMO_SCRIPT.md`.

## Architecture

```mermaid
flowchart LR
  UI[Static page: Chat + AI Log] -- POST /api/chat/stream (NDJSON) --> API[FastAPI]
  API --> SF[0. Safety pre-filter: code]
  SF --> TR[1. Triage: Gemma 4 fast, structured JSON]
  TR -- safety flag --> ESC[Critical ticket + fixed holding reply]
  TR -- low confidence / ambiguous --> CQ[One clarifying question]
  TR -- ok --> RS[2. Resolver: Gemma 4 strong + function calling]
  RS --> TOOLS[14 tools + guardrails in code]
  TOOLS --> DB[(JSON store + audit log)]
  RS --> PL[ResolutionPlan: structured]
  PL --> CP[3. Composer: reply from plan only]
  CP --> RV[Reply validator: code]
  RV --> UI
  UI -- close --> QA[4. QA: LLM rubric + deterministic checks]
```

Why a backend for a "static" site: the page is static (no build), but the browser must never hold the API key, and authorization and policies cannot be enforced client-side. FastAPI serves the static files and the API from one origin.

Key design rules (see `docs/ARCHITECTURE.md`):
- **Policy lives in code** (`tools/guardrails.py`): 6-hour free change rule, no-show after 15 minutes, refunds never automatic, cancellation not mid-period with 3 business days notice, pause limits, deterministic duplicate-charge comparison. The model proposes; code decides, and re-checks at execution time.
- **Four autonomy levels**: `auto`, `user_confirmation` (one-time token bound to exact parameters, 10 minutes), `human_approval` (pending action only a specialist can approve), `forbidden` (such tools are not registered at all).
- **Fail-safe**: any LLM error, timeout, invalid JSON or validator failure escalates to a human. Child-safety reports skip the Resolver entirely.

## Configuration (`backend/.env`)

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `mock` | `mock` (offline, deterministic) or `gemma` |
| `GEMINI_API_KEY` | | required only for `gemma`; never sent to the browser |
| `GEMMA_MODEL_FAST` / `GEMMA_MODEL_STRONG` | `gemma-4-26b-a4b-it` / `gemma-4-31b-it` | triage+QA / resolver+composer (verify ids at ai.google.dev) |
| `LLM_CACHE` | `1` | cache Gemma responses in `backend/cache/llm` so rehearsals repeat quickly |
| `DEMO_NOW` | `2026-10-31T12:00:00+00:00` | the clock used everywhere (`app/clock.py`) |
| `MOCK_LATENCY_MS` | `450` | simulated latency of the mock provider so the live log is watchable |
| `SESSION_SECRET`, `ADMIN_TOKEN` | dev values | change in any shared deployment |

## Security notes
- Acting user id comes from the signed session, never from the model; foreign entity ids return `forbidden` and are audited.
- Customer text and tool results are passed to models as marked untrusted data; injection attempts (for example "ignore your rules and refund me") are blocked and logged, and write tools are disabled for that message.
- PII is minimised for the model (no card data, no emails/phones) and masked in logs (`security.mask`).
- Input validation (2000 chars, no extra fields), 20 messages/min rate limit, CSP `default-src 'self'` with no inline scripts or styles, no `innerHTML` with untrusted text, no stack traces to clients.
- **Demo simplification:** sessions are a switcher without passwords, and the UI obtains the specialist role automatically (`/api/session/agent`). Production would use real authentication and staff SSO.

## Limitations (honest list)
- Data is a JSON file with a write lock: fine for one demo process, not for multiple workers.
- The `mock` provider covers the dataset's messages and common phrasings; free-form language needs `gemma`.
- The live Gemma path was written against the documented API but could not be exercised end to end in the build environment (no key or network). Run `LLM_PROVIDER=gemma` once before demoing and keep `mock` as the fallback.
- Approved actions have simulated effects (the refund does not move money).
- English only.

## Deploy without Docker (Render / Railway)
`render.yaml` is included (native Python service). Manual equivalent: build `pip install -r backend/requirements.txt`, start `cd backend && uvicorn app.main:app --host 0.0.0.0 --port $PORT`, env `LLM_PROVIDER=mock` (or `gemma` plus `GEMINI_API_KEY`). Put the public URL in a QR code on the final slide.
