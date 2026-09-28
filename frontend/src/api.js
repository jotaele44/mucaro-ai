// The built UI is served by the backend itself, so production requests stay same-origin.
// The Vite dev server runs on its own port and talks to the default local backend.
const API_BASE = import.meta.env.VITE_API_BASE ?? (import.meta.env.DEV ? "http://127.0.0.1:8000" : "");
const TOKEN_STORAGE_KEY = "mucaro.operatorToken";

let operatorToken = typeof window !== "undefined" ? window.localStorage.getItem(TOKEN_STORAGE_KEY) || "" : "";

async function request(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (operatorToken && !headers.has("Authorization") && !headers.has("X-Mucaro-Token")) {
    headers.set("X-Mucaro-Token", operatorToken);
  }
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!res.ok) {
    let payload;
    try {
      payload = await res.json();
    } catch {
      payload = { detail: await res.text() };
    }
    const detail = payload.detail || payload;
    const status = typeof detail === "object" ? detail.status || res.status : res.status;
    const message = typeof detail === "object" ? detail.detail || JSON.stringify(detail) : detail;
    const err = new Error(`${status}: ${message}`);
    err.status = status;
    err.payload = payload;
    throw err;
  }
  return res.json();
}

export const api = {
  baseUrl: API_BASE,
  displayUrl: API_BASE || (typeof window !== "undefined" ? window.location.origin : ""),
  getOperatorToken: () => operatorToken,
  setOperatorToken: (token) => {
    operatorToken = token.trim();
    if (typeof window !== "undefined") {
      if (operatorToken) window.localStorage.setItem(TOKEN_STORAGE_KEY, operatorToken);
      else window.localStorage.removeItem(TOKEN_STORAGE_KEY);
    }
  },
  health: () => request("/health"),
  // Only available when the app was started by the Mucaro AI.app / Mucaro AI.exe launcher (config.desktop.quit_supported).
  quit: () => request("/shutdown", { method: "POST", headers: { "X-Mucaro-Action": "quit" } }),
  plan: (payload) => request("/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  approve: (payload) => request("/approve", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  sources: () => request("/sources"),
  reindexSources: () => request("/sources/reindex", { method: "POST" }),
  deleteSource: (sourceId) => request(`/sources/${sourceId}`, { method: "DELETE" }),
  chunks: () => request("/chunks"),
  ask: (payload) => request("/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  conversations: () => request("/conversations"),
  createConversation: (payload) => request("/conversations", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  conversation: (conversationId) => request(`/conversations/${conversationId}`),
  renameConversation: (conversationId, payload) => request(`/conversations/${conversationId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  deleteConversation: (conversationId) => request(`/conversations/${conversationId}`, { method: "DELETE" }),
  askConversation: (conversationId, payload) => request(`/conversations/${conversationId}/ask`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  runSummary: () => request("/runs/summary"),
  memory: () => request("/memory"),
  remember: (payload) => request("/memory", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  updateMemory: (memoryId, payload) => request(`/memory/${memoryId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  deleteMemory: (memoryId) => request(`/memory/${memoryId}`, { method: "DELETE" }),
  exportMemory: () => request("/memory/export"),
  directoryPermissions: () => request("/permissions/directories"),
  checkDirectoryPermission: (payload) => request("/permissions/directories/check", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  grantDirectoryPermission: (payload) => request("/permissions/directories/grant", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  revokeDirectoryPermission: (payload) => request("/permissions/directories/revoke", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  ingestPath: (payload) => request("/ingest-path", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  config: () => request("/config"),
  workspaces: () => request("/workspaces"),
  saveWorkspace: (payload) => request("/workspaces", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  ingest: (file) => {
    const form = new FormData();
    form.append("upload", file);
    return request("/ingest", { method: "POST", body: form });
  },
};
