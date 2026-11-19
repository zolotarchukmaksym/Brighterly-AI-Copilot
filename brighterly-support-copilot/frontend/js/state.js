/** Minimal pub/sub store. Render modules subscribe; nothing is global except this module's exports. */
const listeners = new Set();
let queued = false;

export const state = {
  config: null, users: [], scenarios: [],
  user: null, userToken: null, agentToken: null,
  conversationId: null, convoStatus: null,
  messages: [],          // {id, role: me|ai|specialist, text, cards[]}
  runs: [],              // {id, message, events[], final, status, safety, startedAt}
  actions: {},           // action_id -> action
  expanded: {},          // event_id -> bool (user override)
  seen: new Set(),       // event ids already animated
  qa: null,
  busy: false, filter: "all", autoscroll: true, activeTab: "chat", newLog: false,
};

export function subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); }
export function notify() {
  if (queued) return;
  queued = true;
  requestAnimationFrame(() => { queued = false; listeners.forEach((fn) => fn(state)); });
}
export function update(fn) { fn(state); notify(); }

let msgSeq = 0;
export const addMessage = (role, text, cards = []) => { state.messages.push({ id: ++msgSeq, role, text, cards }); };

export function resetConversation() {
  Object.assign(state, { conversationId: null, convoStatus: null, messages: [], runs: [], actions: {}, expanded: {}, qa: null, busy: false });
  state.seen = new Set();
}

export function startRun(message) {
  const run = { id: null, message, events: [], final: null, status: "running", safety: false, startedAt: performance.now() };
  state.runs.push(run);
  return run;
}

const runFor = (id) => state.runs.find((r) => r.id === id) ?? null;

/** Reduce one pipeline event into state. Events with a known event_id replace the earlier (running) one. */
export function applyEvent(e, currentRun) {
  let run = e.run_id ? runFor(e.run_id) : null;
  if (!run && currentRun) { currentRun.id = e.run_id; run = currentRun; }
  if (!run) return;
  if (state.activeTab !== "log") state.newLog = true;
  if (e.type === "final") {
    run.final = e.data;
    run.status = e.data.status;
    state.conversationId = e.data.conversation_id;
    state.convoStatus = e.data.status;
    addMessage("ai", e.data.reply, (e.data.cards || []).map((c) => ({ ...c, state: c.state || "pending" })));
    return;
  }
  const i = run.events.findIndex((x) => x.event_id === e.event_id);
  if (i >= 0) run.events[i] = e; else run.events.push(e);
  if (e.step === "safety_check" && e.status === "blocked") run.safety = true;
  if (e.step === "escalation" && e.data?.resolver_skipped) run.safety = true;
  if (e.step === "pending_action" && e.data?.action) state.actions[e.data.action.action_id] = { ...e.data.action };
}

/** Events returned by non-streaming endpoints (confirm / approve / reject). */
export function applyLooseEvents(events) {
  for (const e of events || []) {
    const run = runFor(e.run_id) || state.runs[state.runs.length - 1];
    if (!run) continue;
    const i = run.events.findIndex((x) => x.event_id === e.event_id);
    if (i < 0) run.events.push(e);
    if (e.step === "human_decision" && e.data?.action_id && state.actions[e.data.action_id]) {
      state.actions[e.data.action_id].status = e.data.decision;
      state.actions[e.data.action_id].reason = e.data.reason;
    }
  }
}

export function setRunStatus(runId, status) {
  const run = runFor(runId);
  if (run && status) run.status = status;
}
