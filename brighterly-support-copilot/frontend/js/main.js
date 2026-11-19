import { api, ApiError } from "./api.js";
import { streamChat } from "./stream.js";
import { state, subscribe, update, notify, addMessage, startRun, applyEvent, applyLooseEvents, resetConversation, setRunStatus } from "./state.js";
import { renderChat } from "./render/chat.js";
import { renderLog } from "./render/log.js";
import { h, clear, toast, setLoading } from "./ui.js";

const $ = (id) => document.getElementById(id);
const SCENARIO_LABEL = (c) => c.replace(/_/g, " ");

const chatHandlers = {
  confirm: (card, btn) => decide(card, btn, true),
  decline: (card, btn) => decide(card, btn, false),
};
const logHandlers = {
  toggle: (id, open) => update((s) => { s.expanded[id] = open; }),
  approve: (id, btn) => agentDecision(id, btn, () => api.approve(state.agentToken, id)),
  reject: (id, reason, btn) => agentDecision(id, btn, () => api.reject(state.agentToken, id, reason)),
};

subscribe((s) => { renderChat(s, chatHandlers); renderLog(s, logHandlers); });

function errText(e) {
  if (e instanceof ApiError) return { 429: "Too many messages. Please wait a moment.", 409: "This conversation is closed.", 401: "Session expired. Please reselect the customer." }[e.status] || "Something went wrong. Please try again.";
  return "Connection problem. Please try again.";
}

async function selectUser(userId, message) {
  const u = state.users.find((x) => x.user_id === userId);
  if (!u) return;
  try {
    const sess = await api.session(userId);
    resetConversation();
    update((s) => { s.user = u; s.userToken = sess.token; });
    $("customer-select").value = userId;
    const input = $("chat-input");
    input.value = message ?? "";
    autoGrow();
    input.focus();
  } catch (e) { toast(errText(e), "error"); }
}

async function send() {
  const input = $("chat-input");
  const text = input.value.trim();
  if (!text || state.busy || !state.user) return;
  input.value = ""; autoGrow();
  addMessage("me", text);
  const run = startRun(text);
  update((s) => { s.busy = true; });
  try {
    await streamChat(state.userToken, { message: text, ...(state.conversationId ? { conversation_id: state.conversationId } : {}) }, (e) => update((s) => applyEvent(e, run)));
  } catch (e) {
    toast(errText(e), "error");
    update((s) => { run.status = "escalated"; addMessage("ai", "Sorry, something went wrong on our side. Please try again in a moment."); });
  } finally {
    update((s) => { s.busy = false; if (run.status === "running") run.status = "escalated"; });
  }
}

async function decide(card, btn, accept) {
  setLoading(btn, true);
  try {
    const body = { conversation_id: state.conversationId, confirmation_token: card.token };
    const r = accept ? await api.confirm(state.userToken, body) : await api.decline(state.userToken, body);
    update((s) => { card.state = accept ? "confirmed" : "declined"; addMessage("ai", r.message); applyLooseEvents(r.events); s.convoStatus = r.conversation_status; setRunStatus(r.events[0]?.run_id, r.conversation_status); });
  } catch (e) {
    setLoading(btn, false);
    toast(e.code === "expired" ? "That confirmation expired." : errText(e), "error");
    if (e instanceof ApiError && e.status !== 500) update((s) => { card.state = "expired"; });
  }
}

async function agentDecision(actionId, btn, call) {
  setLoading(btn, true);
  try {
    const r = await call();
    if (!r.ok) { setLoading(btn, false); applyLooseEvents(r.events); notify(); toast("Blocked by guardrail at execution time.", "error"); return; }
    update((s) => {
      applyLooseEvents(r.events);
      if (s.actions[actionId]) s.actions[actionId].status = r.status;
      addMessage("specialist", r.customer_message.text);
      s.convoStatus = r.conversation_status || s.convoStatus;
      setRunStatus(r.events[0]?.run_id, r.conversation_status);
    });
    toast(r.status === "approved" ? "Approved. Customer notified in chat." : "Rejected. Customer notified in chat.");
  } catch (e) { setLoading(btn, false); toast(errText(e), "error"); }
}

async function closeConversation() {
  if (!state.conversationId) return;
  const btn = $("qa-btn");
  setLoading(btn, true);
  try {
    const r = await api.close(state.userToken, state.conversationId);
    update((s) => { s.qa = r.qa; s.newLog = true; });
    toast("QA review complete.");
  } catch (e) { toast(errText(e), "error"); }
  finally { setLoading(btn, false); notify(); }
}

async function resetDemo() {
  const btn = $("reset-btn");
  setLoading(btn, true);
  try {
    await api.reset(state.agentToken);
    const uid = state.user?.user_id;
    resetConversation();
    update(() => {});
    if (uid) await selectUser(uid, "");
    toast("Demo data reset.");
  } catch (e) { toast(errText(e), "error"); }
  finally { setLoading(btn, false); }
}

function autoGrow() {
  const t = $("chat-input");
  t.style.setProperty("height", "auto");
  t.style.setProperty("height", Math.min(t.scrollHeight, 140) + "px");
}

function buildCustomerSelect() {
  const sel = $("customer-select");
  clear(sel);
  const groups = new Map();
  for (const u of state.users) {
    if (!groups.has(u.scenario)) groups.set(u.scenario, []);
    groups.get(u.scenario).push(u);
  }
  for (const [sc, users] of groups) {
    const og = h("optgroup", { label: SCENARIO_LABEL(sc) });
    for (const u of users) og.append(h("option", { value: u.user_id }, `${u.name} · ${u.account_type} · ${u.user_id.replace("BR-USER-", "#")}`));
    sel.append(og);
  }
}

function buildScenarioMenu() {
  const pop = $("scenario-pop");
  clear(pop);
  for (const sc of state.scenarios) {
    pop.append(h("button", { class: "menu-item", role: "menuitem", type: "button", onclick: () => { closeMenu(); selectUser(sc.user_id, sc.message); } },
      h("b", {}, sc.label), h("small", {}, `${sc.user_name} (${sc.account_type}) · expect: ${sc.expect}`)));
  }
}
function closeMenu() { $("scenario-pop").hidden = true; $("scenario-btn").setAttribute("aria-expanded", "false"); }

function setupSplitter() {
  const split = $("split"), sp = $("splitter");
  const apply = (r) => { r = Math.min(0.62, Math.max(0.3, r)); split.style.setProperty("--left", r + "fr"); split.style.setProperty("--right", 1 - r + "fr"); sp.dataset.r = r; };
  apply(5 / 12);
  sp.addEventListener("pointerdown", (e) => {
    sp.setPointerCapture(e.pointerId); sp.classList.add("drag");
    const move = (ev) => { const b = split.getBoundingClientRect(); apply((ev.clientX - b.left) / b.width); };
    const up = () => { sp.classList.remove("drag"); sp.removeEventListener("pointermove", move); sp.removeEventListener("pointerup", up); };
    sp.addEventListener("pointermove", move); sp.addEventListener("pointerup", up);
  });
  sp.addEventListener("keydown", (e) => { const r = parseFloat(sp.dataset.r); if (e.key === "ArrowLeft") apply(r - 0.02); if (e.key === "ArrowRight") apply(r + 0.02); });
}

function wire() {
  $("composer").addEventListener("submit", (e) => { e.preventDefault(); send(); });
  $("chat-input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
  $("chat-input").addEventListener("input", autoGrow);
  $("customer-select").addEventListener("change", (e) => { const u = state.users.find((x) => x.user_id === e.target.value); selectUser(e.target.value, u?.suggested_message ?? ""); });
  $("scenario-btn").addEventListener("click", () => { const pop = $("scenario-pop"); pop.hidden = !pop.hidden; $("scenario-btn").setAttribute("aria-expanded", String(!pop.hidden)); });
  document.addEventListener("click", (e) => { if (!$("scenario-menu").contains(e.target)) closeMenu(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });
  $("qa-btn").addEventListener("click", closeConversation);
  $("reset-btn").addEventListener("click", resetDemo);
  $("filters").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-f]"); if (!b) return;
    for (const x of $("filters").children) x.setAttribute("aria-pressed", String(x === b));
    update((s) => { s.filter = b.dataset.f; });
  });
  $("autoscroll-btn").addEventListener("click", () => update((s) => {
    s.autoscroll = !s.autoscroll;
    const b = $("autoscroll-btn"); b.setAttribute("aria-pressed", String(s.autoscroll)); b.lastChild.textContent = s.autoscroll ? "Auto-scroll on" : "Auto-scroll paused";
  }));
  document.querySelector(".mobile-tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-tab]"); if (!b) return;
    for (const x of e.currentTarget.children) x.setAttribute("aria-selected", String(x === b));
    $("split").dataset.active = b.dataset.tab;
    update((s) => { s.activeTab = b.dataset.tab; if (b.dataset.tab === "log") s.newLog = false; });
  });
  setupSplitter();
}

async function init() {
  wire();
  try {
    const [config, users, scenarios, agent] = await Promise.all([api.config(), api.users(), api.scenarios(), api.agentSession()]);
    update((s) => { s.config = config; s.users = users.users; s.scenarios = scenarios.scenarios; s.agentToken = agent.token; });
    $("provider-badge").lastChild.textContent = config.provider === "gemma" ? `AI: ${config.model_strong}` : "AI: mock (offline)";
    $("now-badge").textContent = "now " + config.demo_now.replace("T", " ").slice(0, 16) + " UTC";
    buildCustomerSelect(); buildScenarioMenu();
    const first = state.scenarios[0];
    await selectUser(first.user_id, first.message);
  } catch (e) { toast("Could not reach the server. Is it running?", "error"); }
  notify();
}
init();
