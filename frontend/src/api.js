const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, options);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${text}`);
  }
  return res.json();
}

export const api = {
  health: () => request("/health"),
  plan: (payload) => request("/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  approve: (payload) => request("/approve", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  sources: () => request("/sources"),
  chunks: () => request("/chunks"),
  ask: (payload) => request("/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
  ingest: (file) => {
    const form = new FormData();
    form.append("upload", file);
    return request("/ingest", { method: "POST", body: form });
  },
};
