# Decisions (one line each)

- **Scenario users differ from the brief for scenarios 3 and 4.** Relative to `DEMO_NOW` the dataset's `reschedule_eligible` users (for example BR-USER-004) have their next lesson 1 hour away and the `reschedule_late` users (for example BR-USER-005) have lessons 8 days away, so the labels are effectively swapped. Policy is computed from real data, so "Late cancellation" uses BR-USER-004 (lesson in 1 h, genuinely blocked) and "Reschedule" uses BR-USER-019 (lesson in 8 days, genuinely allowed). Customer BR-USER-005 still works: the assistant correctly says free cancellation is allowed.
- **Tutor no-show data is partly inconsistent.** BR-USER-021/051/081 are labelled `tutor_no_show` but their lesson is `completed` with the tutor present; the assistant says it cannot find a no-show instead of crediting. This is intentional grounding, not a bug.
- **Late cancellation never auto-creates a ticket.** It explains the 6-hour rule and offers a specialist exception review; a ticket is created only after the customer says yes (follow-up turn).
- **False alarm duplicate charges are blocked in code** (`create_ticket`/`escalate_to_human` return `BLOCKED: No duplicate found: ticket not needed`).
- **Duplicate detection rule:** same subscription and amount within 48 h (or `duplicate_of_payment_id`), second charge without credits, versus charges roughly 30 days apart with credits each.
- **Reschedule proposes one concrete slot** (earliest top-of-hour 16:00 to 19:00 in the tutor's timezone, nearest to the original hour, never within 6 h) and lists alternatives; the customer confirms with a button.
- **Pause months** are parsed from the message ("2 months"), default 1 ("a few weeks").
- **Mid confidence (0.6 to 0.85) and suspected injection** run in `read_only` mode: write tools are not offered to the model and are blocked in the executor.
- **Fail-safe handoff in code:** if the plan says a human is needed but nothing was created, `escalate_to_human(general_review)` is called by the orchestrator.
- **Mock provider simulates latency** (`MOCK_LATENCY_MS`, default 450) so the live log is watchable and recordings look natural. Set 0 for instant runs.
- **MockLLM lives in `llm_mock.py`** (imported by `llm.py`) to keep files readable.
- **Prompts are drafts** in `app/prompts/*.md` (about 30 lines each) to be replaced later; `PROMPT_VERSION` is included in events and audit.
- **Session model:** itsdangerous signed tokens; `/api/session/agent` is open in the demo. The UI Reset button authenticates with the specialist token (the `ADMIN_TOKEN` header also works).
- **No python-dotenv:** a 10-line `.env` reader in `config.py` avoids a dependency.
- **Gemma structured output:** try `response_json_schema`, fall back to JSON mime only, then plain text with the schema in the prompt; `system_instruction` falls back to a prefixed user message on a 400.
- **IDs:** `TKT-`, `ACT-`, `AUD-`, `CONV-`, `CNF-` plus a short uuid fragment.
- **Time labels** in slots use the tutor's timezone abbreviation (PST/PDT) computed with `zoneinfo`.
