export class ApiError extends Error {
  constructor(status, code) { super(code || `HTTP ${status}`); this.status = status; this.code = code; }
}

async function request(path, { method = "POST", body, token, headers } = {}) {
  const res = await fetch(path, {
    method,
    headers: { ...(body !== undefined ? { "Content-Type": "application/json" } : {}), ...(token ? { Authorization: `Bearer ${token}` } : {}), ...headers },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let code = "";
    try { code = (await res.json()).error?.code ?? ""; } catch { /* non-JSON error body */ }
    throw new ApiError(res.status, code);
  }
  return res.json();
}

export const api = {
  config: () => request("/api/admin/config", { method: "GET" }),
  users: () => request("/api/demo/users", { method: "GET" }),
  scenarios: () => request("/api/demo/scenarios", { method: "GET" }),
  session: (user_id) => request("/api/session", { body: { user_id } }),
  agentSession: () => request("/api/session/agent", { body: {} }),
  confirm: (token, body) => request("/api/chat/confirm", { token, body }),
  decline: (token, body) => request("/api/chat/decline", { token, body }),
  approve: (token, id) => request(`/api/actions/${id}/approve`, { token, body: {} }),
  reject: (token, id, reason) => request(`/api/actions/${id}/reject`, { token, body: { reason } }),
  close: (token, cid) => request(`/api/conversations/${cid}/close`, { token, body: {} }),
  reset: (token) => request("/api/admin/reset", { token, body: {} }),
};
