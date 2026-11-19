# Architecture

## Request flow
`POST /api/chat/stream` runs the pipeline in a worker thread. Each step pushes an event into a queue and the HTTP response yields it as one NDJSON line, so the UI shows steps while they happen (`app/pipeline/orchestrator.py`, `events.py`).

1. **Safety pre-filter** (`safety.py`): regexes for secrecy, outside-platform contact, inappropriate content, fear, self-harm; plus a prompt-injection detector. No LLM.
2. **Triage** (fast model, structured JSON `TriageResult`). Final safety flag = pre-filter OR triage. If triage is down but the pre-filter flagged, the safety path still runs.
3. **Safety path**: Resolver skipped, `escalate_to_human(kind=child_safety)` creates a `critical` ticket for `trust_and_safety_immediately` with the verbatim message and minimal context; the reply is a fixed calm template (student variant is shorter and adds a trusted-adult line; self-harm adds emergency services).
4. **Clarification path**: confidence < 0.6, `needs_clarification`, or `unknown_or_ambiguous` gives exactly one question and no tools.
5. **Resolver** (strong model, manual function-calling loop, max 6 steps). Mode `act` (confidence >= 0.85, no injection) or `read_only` (write tools are not even declared, and the executor also blocks them). Then a **separate** structured call produces the `ResolutionPlan` (tools and `response_schema` are never combined).
6. **Composer** writes the reply from the plan only; the **reply validator** blocks money promises, false "done" claims, policy contradictions (for example describing a blocked late cancellation as free) and foreign PII. One regeneration, then a safe template built from the plan facts.
7. **QA** at conversation close: LLM rubric plus deterministic checks (validator violations, safety ticket present); deterministic findings cap the scores.

## Layers
- `tools/guardrails.py`: pure functions (`can_reschedule`, `can_pause`, `can_cancel_subscription`, `refund_eligible`, `detect_duplicate_charge`, `tutor_no_show_review`, `owns`).
- `tools/read_tools.py`, `action_tools.py`, `registry.py`: 14 tools; the registry drops model-supplied user ids; forbidden tools are not registered.
- `db.py`: the only module that touches JSON files (RLock, tmp file + `os.replace`). Runtime collections: tickets, pending_actions, confirmations, conversations, audit_log, qa_scores.
- `llm.py`: `GemmaLLM` (google-genai: system instruction, structured output with schema then plain fallback, tools, retries on 429/5xx, concurrency semaphore 2, 25 s timeout, response cache, thinking parts dropped, one JSON retry then `LLMFormatError`) and `MockLLM` (`llm_mock.py`) with the same interface.
- Front end: `state.js` store, `stream.js` NDJSON reader, `render/chat.js`, `render/log.js`; DOM built with `textContent` only.

## Autonomy and the human loop
| Level | Examples | Who acts |
|---|---|---|
| auto | reads, `create_ticket`, safety escalation | AI |
| user_confirmation | `reschedule_lesson`, `submit_pause_request` | customer confirms a one-time token (10 min, bound to exact params); guardrails re-run on confirm |
| human_approval | refund review, lesson credit, cancellation, tutor change, plan change, substitute tutor | specialist approves or rejects in the log; guardrails re-run on approve; effect is simulated in runtime state; the customer gets the specialist's message |
| forbidden | charge card, delete payment, change payment method | nobody; not registered |

Every tool call, gate decision, confirmation and approval is written to `audit_log` (`who`, `what`, `entity`, masked `params`, `result`, `confidence`, `prompt_version`, `timestamp`).
