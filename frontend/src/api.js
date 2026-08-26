const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, options);
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
  health: () => request("/health"),
  plan: (payload) => request("/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  approve: (payload) => request("/approve", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  sources: () => request("/sources"),
  chunks: () => request("/chunks"),
  ask: (payload) => request("/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  directoryPermissions: () => request("/permissions/directories"),
  checkDirectoryPermission: (payload) => request("/permissions/directories/check", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  grantDirectoryPermission: (payload) => request("/permissions/directories/grant", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  revokeDirectoryPermission: (payload) => request("/permissions/directories/revoke", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  ingestPath: (payload) => request("/ingest-path", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  ingest: (file) => {
    const form = new FormData();
    form.append("upload", file);
    return request("/ingest", { method: "POST", body: form });
  },
};
