# Demo script (2 to 3 minutes)

Before recording: `cd backend && python tasks.py dev`, open http://localhost:8000, press **Reset demo**. Use the `mock` provider for a deterministic take (or `gemma` after one dry run so the cache is warm).

| Time | On screen | Say |
|---|---|---|
| 0:00 | Whole page: chat left, dark AI log right | "This is a support copilot for a tutoring platform. On the left is what the customer sees. On the right is what the AI is actually doing, live." |
| 0:15 | Scenarios, "1. Real duplicate charge", press Send | "A parent says they were charged twice. Watch the right side." |
| 0:25 | Log streams: safety check, triage with the confidence bar | "First a code-level safety check, then triage: intent, tone, urgency. Confidence 0.94 is above 0.85, so the AI is allowed to act." |
| 0:40 | Tool calls, open `get_payment_history` (highlighted rows) | "It reads the payments. The second charge came 4 minutes after the first and added no lesson credits. Code, not the model, decides this is a duplicate." |
| 0:55 | Guardrail row "REVIEW: duplicate_refund_human_approval", then Pending human approval card | "Refunds are never automatic. The AI creates a request for a human with the evidence attached." |
| 1:10 | Chat reply | "The customer gets an honest answer: request submitted, nothing refunded yet. The reply is validated so it can't promise money." |
| 1:20 | Click **Approve** in the log | "Now I'm the support specialist. Approve." Customer chat shows the specialist's message. |
| 1:35 | Scenarios, "2. False alarm" | "Same words, different data: two regular monthly renewals. No ticket, and the log says why." |
| 1:50 | Scenarios, "3. Late cancellation" | "The lesson starts in one hour. The 6-hour rule is enforced in code, so the guardrail shows BLOCKED and the AI explains kindly." |
| 2:05 | Scenarios, "5. Child-safety report" | "A safety report never goes to the AI resolver. Safety gate triggered, critical ticket to Trust and Safety, calm fixed reply." |
| 2:25 | Scenarios, "7. Injection attempt" | "Trying to talk the AI out of its rules is blocked and audited." |
| 2:40 | **End conversation & run QA** | "Every conversation gets a QA score with explanations. Fail-safe by default: any model error escalates to a human." |

Backup if something stalls: `python tasks.py demo` prints the same scenarios in the terminal.
