import { icon } from "./icons.js";

/** DOM builder. Text is always inserted as text nodes (never innerHTML), so untrusted strings stay inert. */
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "text") el.textContent = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  append(el, children);
  return el;
}
function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}
export const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); };

export function toast(message, kind = "info") {
  const root = document.getElementById("toasts");
  const t = h("div", { class: "toast" + (kind === "error" ? " error" : "") }, message);
  root.append(t);
  setTimeout(() => t.remove(), 4200);
}

export const fmtMs = (ms) => (ms == null ? "" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);

/** Tiny JSON highlighter that builds spans with textContent (no HTML parsing). */
export function jsonBlock(data) {
  const text = JSON.stringify(data, null, 2) ?? "";
  const pre = h("pre", {});
  const re = /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) pre.append(text.slice(last, m.index));
    const cls = m[1] ? (m[2] ? "k" : "s") : m[3] ? "b" : "n";
    pre.append(h("span", { class: cls }, m[1] ? m[1] : m[0]));
    if (m[2]) pre.append(m[2]);
    last = re.lastIndex;
  }
  pre.append(text.slice(last));
  const btn = h("button", { class: "btn btn-ghost btn-sm copy", type: "button", "aria-label": "Copy JSON", onclick: async (e) => {
    try { await navigator.clipboard.writeText(text); toast("JSON copied"); } catch { toast("Copy is not available here", "error"); }
    e.currentTarget.blur();
  } }, icon("copy"), "Copy");
  return h("div", { class: "json" }, btn, pre);
}

export function setLoading(btn, on) { btn.classList.toggle("loading", on); btn.disabled = on; }
