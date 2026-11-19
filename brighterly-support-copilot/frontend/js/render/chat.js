import { h, clear } from "../ui.js";
import { icon } from "../icons.js";

const STATUS = {
  resolved: ["Resolved", "ok"], awaiting_customer: ["Awaiting your confirmation", "confirm"],
  awaiting_human: ["Awaiting specialist", "human"], escalated: ["Escalated", "danger"],
};

export function renderChat(s, h_) {
  const box = document.getElementById("messages");
  const stick = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  clear(box);

  document.getElementById("cust-name").textContent = s.user ? s.user.name : "Customer chat";
  document.getElementById("cust-sub").textContent = s.user ? `${s.user.account_type === "student" ? "Student" : "Parent"} account · ${s.user.user_id}` : "Choose a customer or scenario";
  document.getElementById("cust-avatar").textContent = s.user ? s.user.name.split(" ").map((p) => p[0]).slice(0, 2).join("") : "?";
  const chip = document.getElementById("convo-status");
  const st = STATUS[s.convoStatus];
  chip.hidden = !st;
  if (st) { chip.textContent = st[0]; chip.className = "chip " + st[1]; }

  if (!s.messages.length) {
    box.append(h("div", { class: "empty" }, icon("bolt"), h("p", {}, h("b", {}, "Start a conversation. "), "Type as the customer, or pick a ready-made case from ", h("b", {}, "Scenarios"), " in the header.")));
  }
  for (const m of s.messages) box.append(renderMessage(m, s, h_));
  if (s.busy) box.append(h("div", { class: "typing", "aria-label": "Assistant is working" }, h("i"), h("i"), h("i")));
  if (stick || s.busy) box.scrollTop = box.scrollHeight;
  document.getElementById("send-btn").disabled = s.busy || !s.user;
  document.getElementById("qa-btn").disabled = !s.conversationId || s.busy || !!s.qa;
}

function renderMessage(m, s, h_) {
  const who = m.role === "me" ? null : m.role === "specialist" ? "Support specialist" : "Brighterly Assistant";
  const wrap = h("div", { class: "msg " + m.role }, who && h("div", { class: "msg-who" }, who), h("div", { class: "bubble" }, m.text));
  for (const c of m.cards || []) wrap.append(renderCard(c, h_));
  return wrap;
}

function renderCard(c, h_) {
  if (c.type === "confirmation") {
    const done = c.state !== "pending";
    const label = { confirmed: "Confirmed", declined: "Declined", expired: "Expired" }[c.state];
    return h("div", { class: "card confirm" + (done ? " done" : ""), role: "group", "aria-label": "Confirmation needed" },
      h("h3", {}, c.summary),
      c.alternatives?.length ? h("p", { class: "alts" }, "Other times: " + c.alternatives.join(" · ")) : null,
      done ? h("div", { class: "row" }, h("span", { class: "chip " + (c.state === "confirmed" ? "ok" : "") }, label || c.state))
        : h("div", { class: "row" },
          h("button", { class: "btn btn-primary btn-sm", type: "button", onclick: (e) => h_.confirm(c, e.currentTarget) }, "Confirm"),
          h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: (e) => h_.decline(c, e.currentTarget) }, "Decline")));
  }
  if (c.type === "banner") return h("div", { class: "card banner" }, icon("shield"), c.text);
  return h("div", { class: "card info" }, icon("info"), h("span", {}, c.text));
}
