import { h, clear, fmtMs, jsonBlock, setLoading } from "../ui.js";
import { icon } from "../icons.js";

const STEP = {
  safety_check: ["shield", "Safety check"], triage: ["compass", "Triage"], tool_call: ["wrench", "Tool call"], guardrail: ["scale", "Guardrail"],
  plan: ["clipboard", "Plan"], compose: ["pen", "Compose"], reply_validation: ["check", "Reply validation"], ticket_created: ["ticket", "Outcome"],
  pending_action: ["hourglass", "Pending human approval"], confirmation_request: ["help", "Customer confirmation"], escalation: ["siren", "Escalation"],
  human_decision: ["user", "Human decision"], qa: ["star", "QA"], error: ["x", "Error"],
};
const AUTONOMY = { auto: "auto", user_confirmation: "needs customer confirmation", human_approval: "needs human approval" };
const FILTERS = {
  all: () => true, tools: (e) => e.step === "tool_call",
  guards: (e) => ["safety_check", "guardrail", "reply_validation"].includes(e.step),
  human: (e) => ["pending_action", "human_decision", "confirmation_request", "escalation"].includes(e.step),
};
const STATUS_LABEL = { running: ["running", ""], resolved: ["resolved", "ok"], awaiting_customer: ["awaiting customer", "confirm"], awaiting_human: ["awaiting specialist", "human"], escalated: ["escalated", "danger"] };

function tone(e, s) {
  if (e.status === "running") return "running";
  if (e.status === "blocked" || e.status === "error") return "danger";
  if (e.step === "pending_action") return "human";
  if (e.step === "confirmation_request") return "confirm";
  if (e.step === "escalation") return "danger";
  if (e.step === "guardrail" && e.data?.decision === "REVIEW") return "human";
  if (e.step === "human_decision") return e.data?.decision === "approved" ? "ok" : "danger";
  return "ok";
}
const defaultOpen = (e) => ["triage", "plan", "pending_action", "escalation", "human_decision"].includes(e.step) || e.status === "blocked" || e.status === "error";

export function renderLog(s, handlers) {
  const root = document.getElementById("log");
  const prevTop = root.scrollTop;
  clear(root);
  if (!s.runs.length) {
    root.append(h("div", { class: "empty" }, icon("bolt"), h("p", {}, h("b", {}, "Send a message to watch the AI work."), " Every safety check, tool call, guardrail decision and plan appears here in real time.")));
  }
  for (const run of s.runs) root.append(renderRun(run, s, handlers));
  if (s.qa) root.append(renderQA(s.qa));
  if (s.autoscroll) root.scrollTop = root.scrollHeight; else root.scrollTop = prevTop;
  document.getElementById("metrics").textContent = metrics(s);
  document.getElementById("log-new").hidden = !(s.newLog && s.activeTab !== "log");
}

function metrics(s) {
  const evs = s.runs.flatMap((r) => r.events).filter((e) => e.status !== "running");
  if (!evs.length) return "Every step of the real pipeline streams here";
  const tools = evs.filter((e) => e.step === "tool_call").length;
  const ms = s.runs.reduce((a, r) => a + (r.final?.wall_ms || 0), 0);
  const tok = evs.reduce((a, e) => a + (e.data?.llm?.tokens_in || 0) + (e.data?.llm?.tokens_out || 0), 0);
  return `${evs.length} steps · ${tools} tool calls · ${(ms / 1000).toFixed(1)} s` + (tok ? ` · ${tok} tokens` : " · tokens n/a (mock)");
}

function renderRun(run, s, handlers) {
  const evs = run.events.filter((e) => FILTERS[s.filter](e));
  const done = run.events.filter((e) => e.status !== "running");
  const tools = done.filter((e) => e.step === "tool_call").length;
  const waiting = done.filter((e) => e.step === "pending_action" && s.actions[e.data.action?.action_id]?.status === "pending").length;
  const secs = run.final ? (run.final.wall_ms / 1000).toFixed(1) : "…";
  const st = STATUS_LABEL[run.status] || ["done", ""];
  const sum = `${done.length} steps · ${tools} tools · ${secs} s` + (waiting ? ` · ${waiting} action${waiting > 1 ? "s" : ""} awaiting approval` : "");
  const tl = h("div", { class: "timeline" });
  for (const e of evs) tl.append(renderStep(e, run, s, handlers));
  if (!evs.length) tl.append(h("p", { class: "step-sum" }, run.events.length ? "No steps match this filter." : "Starting…"));
  return h("article", { class: "run" + (run.safety ? " safety" : ""), "aria-label": "AI run " + (run.id || "") },
    h("div", { class: "run-head" },
      h("div", { class: "run-top" }, h("span", { class: "run-id" }, (run.id || "RUN") + " · customer message"), h("span", { class: "chip " + st[1] }, st[0])),
      h("p", { class: "run-msg" }, "“" + run.message + "”"),
      h("p", { class: "run-sum" }, sum)),
    tl);
}

function renderStep(e, run, s, handlers) {
  const [ic, kind] = STEP[e.step] || ["info", e.step];
  const t = tone(e, s);
  const isOpen = s.expanded[e.event_id] ?? defaultOpen(e);
  const isNew = !s.seen.has(e.event_id + ":" + e.status);
  s.seen.add(e.event_id + ":" + e.status);
  const body = h("div", { class: "step-body", hidden: !isOpen }, ...(isOpen ? bodyFor(e, s, handlers) : []));
  const head = h("button", { class: "step-head", type: "button", "aria-expanded": String(isOpen), onclick: () => handlers.toggle(e.event_id, !isOpen) },
    h("span", { class: "step-kind" }, kind),
    h("span", { class: "step-title" + (e.step === "tool_call" ? " mono" : "") }, e.title),
    e.autonomy ? h("span", { class: "pill " + e.autonomy }, AUTONOMY[e.autonomy]) : null,
    e.step === "guardrail" ? h("span", { class: "pill " + (e.status === "blocked" ? "blocked" : e.data?.decision === "REVIEW" ? "review" : "allowed") }, e.status === "blocked" ? "BLOCKED" : e.data?.decision === "REVIEW" ? "REVIEW" : "ALLOWED") : null,
    e.duration_ms != null ? h("span", { class: "step-ms" }, fmtMs(e.duration_ms)) : null,
    icon("chevron", "step-chev"));
  return h("div", { class: `step ${t}${isOpen ? " open" : ""}${isNew ? " enter" : ""}` },
    h("div", { class: "node" }, icon(ic)),
    h("div", { class: "step-main" }, head, e.summary ? h("p", { class: "step-sum" }, e.summary) : null, body));
}

function chips(items) { return h("div", { class: "kv" }, items.filter(Boolean).map(([txt, cls]) => h("span", { class: "chip " + (cls || "") }, txt))); }

function bodyFor(e, s, handlers) {
  const d = e.data || {};
  const out = [];
  switch (e.step) {
    case "triage": {
      const pct = Math.round(d.confidence * 100);
      const band = d.confidence < d.thresholds.low ? "low" : d.confidence < d.thresholds.high ? "mid" : "high";
      out.push(chips([[d.primary_intent, "confirm"], ...(d.secondary_intents || []).map((x) => ["+ " + x, ""]), ["tone: " + d.sentiment, d.sentiment === "angry" || d.sentiment === "distressed" ? "danger" : ""],
        ["urgency: " + d.urgency, d.urgency === "high" || d.urgency === "critical" ? "human" : ""], ["churn risk: " + d.churn_risk, d.churn_risk === "high" ? "danger" : ""], ["writer: " + d.writer, ""],
        d.final_safety_flag ? ["SAFETY FLAG", "danger"] : ["no safety flag", "ok"]]));
      const tl = Math.round(d.thresholds.low * 100), th = Math.round(d.thresholds.high * 100);
      const fill = h("div", { class: "conf-fill " + band });
      fill.style.setProperty("width", pct + "%");
      const m1 = h("i", { class: "conf-mark" }), m2 = h("i", { class: "conf-mark" });
      m1.style.setProperty("left", tl + "%"); m2.style.setProperty("left", th + "%");
      const l = (txt, p) => { const x = h("span", {}, txt); x.style.setProperty("left", p + "%"); return x; };
      out.push(h("div", { class: "conf", role: "img", "aria-label": `Confidence ${pct} percent` }, h("div", { class: "conf-bar" }, fill, m1, m2),
        h("div", { class: "conf-labels" }, l("0", 2), l("0.6 · propose only", tl), l("0.85 · may act", th))));
      if (d.rationale) out.push(h("p", { class: "step-sum" }, d.rationale));
      break;
    }
    case "tool_call":
      if (d.args && Object.keys(d.args).length) out.push(h("div", { class: "args" }, Object.entries(d.args).map(([k, v]) => h("span", {}, `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`))));
      if (d.result?.payments) out.push(payTable(d.result));
      break;
    case "guardrail":
      out.push(h("div", { class: "callout " + (e.status === "blocked" ? "danger" : "info") }, icon("scale"), h("div", {}, h("div", {}, d.reason), h("div", { class: "args" }, h("span", {}, "rule=" + d.rule)))));
      break;
    case "plan":
      if (d.actions) { out.push(h("div", { class: "callout info" }, icon("help"), d.question)); break; }
      if (d.evidence?.length) out.push(h("div", {}, h("div", { class: "lbl" }, "Evidence"), chips(d.evidence.map((x) => [x, ""]))));
      for (const [k, lbl] of [["actions_taken", "Actions taken"], ["actions_proposed", "Actions proposed"]]) if (d[k]?.length) out.push(h("div", {}, h("div", { class: "lbl" }, lbl), h("ul", { class: "list" }, d[k].map((x) => h("li", {}, x)))));
      out.push(chips([[d.needs_human ? "human needed" : "no human needed", d.needs_human ? "human" : "ok"], ["priority: " + d.recommended_priority, ""], ["outcome: " + d.outcome, ""]]));
      if (d.needs_human_reason) out.push(h("p", { class: "step-sum" }, d.needs_human_reason));
      break;
    case "safety_check":
      if (d.prefilter?.flag) out.push(h("div", { class: "callout danger" }, icon("shield-alert"), "Pre-filter matched: " + d.prefilter.matches.join(", ")));
      else out.push(chips([["child-safety patterns: none", "ok"], d.injection?.detected ? ["injection detected", "danger"] : ["injection: none", "ok"]]));
      break;
    case "escalation":
      if (d.resolver_skipped) out.push(h("div", { class: "callout danger" }, icon("siren"), "Safety gate triggered, automated resolution skipped. The Resolver never ran; a critical ticket goes to Trust & Safety and the customer gets a calm holding message."));
      break;
    case "ticket_created":
      out.push(chips([[d.ticket_id, "confirm"], [(d.team || "").replace(/_/g, " "), ""], d.priority ? ["priority: " + d.priority, d.priority === "critical" ? "danger" : ""] : null]));
      break;
    case "pending_action":
      out.push(approvalCard(d.action, d.ticket, s, handlers));
      break;
    case "human_decision":
      out.push(h("div", { class: "callout " + (d.decision === "approved" ? "ok" : "danger") }, icon("user"), h("div", {}, `${d.decided_by} ${d.decision} ${d.action_id}.`, d.reason ? ` Reason: ${d.reason}` : "")));
      break;
    case "compose": case "reply_validation":
      if (d.preview) out.push(h("p", { class: "step-sum" }, "“" + d.preview + (d.preview.length >= 160 ? "…" : "") + "”"));
      if (d.violations?.length) out.push(h("ul", { class: "list" }, d.violations.map((v) => h("li", {}, v))));
      break;
    default: break;
  }
  out.push(jsonToggle(e, s, handlers));
  return out;
}

function jsonToggle(e, s, handlers) {
  const key = e.event_id + ":json";
  const open = s.expanded[key] ?? false;
  return h("div", {}, h("button", { class: "btn btn-ghost btn-sm", type: "button", "aria-expanded": String(open), onclick: () => handlers.toggle(key, !open) },
    icon("chevron", open ? "rot" : ""), open ? "Hide raw JSON" : "Raw JSON"), open ? jsonBlock(e.data) : null);
}

function payTable(r) {
  const dup = r.compare_payments?.duplicate;
  const rows = r.payments.map((p) => {
    const cls = dup && p.payment_id === dup.duplicate_payment_id ? "dup" : dup && p.payment_id === dup.original_payment_id ? "orig" : "";
    return h("tr", { class: cls }, h("td", {}, p.payment_id), h("td", {}, "$" + p.amount_usd.toFixed(2)), h("td", {}, p.processed_at.slice(5, 16).replace("T", " ")), h("td", {}, p.status), h("td", {}, "+" + (p.lesson_credits_added ?? 0) + " cr"));
  });
  return h("div", {}, h("table", { class: "paytable" }, h("thead", {}, h("tr", {}, ["payment", "amount", "when (UTC)", "status", "credits"].map((x) => h("th", {}, x)))), h("tbody", {}, rows)),
    h("p", { class: "step-sum" }, dup ? "Highlighted: the second charge duplicates the first (same amount, minutes apart, no credits added)." : "No duplicate: charges are regular monthly renewals."));
}

function approvalCard(a, ticket, s, handlers) {
  const cur = s.actions[a.action_id] || a;
  const decided = cur.status !== "pending";
  const p = a.params || {};
  const facts = [["type", a.type], ["team", (a.team || "").replace(/_/g, " ")], ["priority", a.priority], ["ticket", a.ticket_id],
    p.refund_amount_usd != null ? ["amount", "$" + p.refund_amount_usd.toFixed(2)] : p.estimated_amount_usd != null ? ["est. amount", "$" + p.estimated_amount_usd.toFixed(2)] : null].filter(Boolean);
  const reason = h("input", { type: "text", placeholder: "Reason for rejection (required)", "aria-label": "Rejection reason", maxlength: "300" });
  const box = h("div", { class: "reject-box", hidden: true }, reason,
    h("button", { class: "btn btn-danger btn-sm", type: "button", onclick: (ev) => { if (!reason.value.trim()) { reason.focus(); return; } handlers.reject(a.action_id, reason.value.trim(), ev.currentTarget); } }, "Confirm reject"));
  return h("div", { class: "approve" + (decided ? " done" : "") },
    h("div", { class: "approve-head" }, icon("user"), h("span", { class: "approve-title" }, "Support specialist action"),
      h("span", { class: "chip " + (cur.status === "approved" ? "ok" : cur.status === "rejected" ? "danger" : "human") }, cur.status)),
    h("p", { class: "step-sum" }, a.summary),
    chips(facts.map(([k, v]) => [`${k}: ${v}`, ""])),
    a.evidence?.length ? h("div", {}, h("div", { class: "lbl" }, "Evidence"), chips(a.evidence.map((x) => [x, "confirm"]))) : null,
    decided ? (cur.reason ? h("p", { class: "step-sum" }, "Reason: " + cur.reason) : null)
      : [h("div", { class: "approve-row" },
        h("button", { class: "btn btn-success btn-sm", type: "button", onclick: (ev) => handlers.approve(a.action_id, ev.currentTarget) }, icon("check"), "Approve"),
        h("button", { class: "btn btn-danger btn-sm", type: "button", onclick: () => { box.hidden = !box.hidden; if (!box.hidden) reason.focus(); } }, icon("x"), "Reject")), box]);
}

function renderQA(qa) {
  const rows = [["empathy", "Empathy"], ["factual_accuracy", "Factual accuracy"], ["policy_compliance", "Policy compliance"], ["resolution", "Resolution"], ["safety", "Safety"]];
  const out = h("div", { class: "qa" }, h("div", { class: "qa-head" }, h("h3", { class: "h-title" }, "QA review"), h("span", { class: "chip " + (qa.overall >= 4 ? "ok" : "human") }, `overall ${qa.overall}/5`)));
  for (const [k, lbl] of rows) {
    const bar = h("i"); bar.style.setProperty("width", (qa[k] / 5) * 100 + "%");
    out.append(h("div", { class: "qa-row" }, h("span", {}, lbl), h("div", { class: "qa-bar" }, bar), h("span", { class: "qa-score" }, qa[k] + "/5"), h("div", { class: "qa-why" }, qa.explanations?.[k] || "")));
  }
  const c = qa.deterministic_checks || {};
  out.append(h("p", { class: "step-sum" }, `Deterministic checks: ${c.policy_violations ?? 0} policy violations · safety ${c.safety_ok ? "ok" : "FAILED"} · ${c.tickets?.length || 0} ticket(s) · ${c.pending_actions?.length || 0} pending action(s)`));
  return out;
}
