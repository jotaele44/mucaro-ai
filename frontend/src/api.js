// Same-origin by default: the packaged app and `mucaro serve` host the UI and API together.
// For `npm run dev`, vite.config.js proxies API paths to the local backend.
const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const TOKEN_STORAGE_KEY = "mucaro.operatorToken";

const LAUNCH_TOKEN_KEY = "mucaro.launchToken";

function takeLaunchToken() {
  // The desktop shell opens `/#token=<per-launch token>`. The fragment is never sent to the server;
  // it is removed from the address at once and kept only for this window's session (reloads keep working,
  // the next launch uses a new port, so a new origin and a new token).
  const match = /(?:^#|&)token=([^&]+)/.exec(window.location.hash || "");
  if (match) {
    const token = decodeURIComponent(match[1]);
    try {
      window.sessionStorage.setItem(LAUNCH_TOKEN_KEY, token);
    } catch {
      // Storage may be blocked; the in-memory token still works until reload.
    }
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
    return token;
  }
  try {
    return window.sessionStorage.getItem(LAUNCH_TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

let launchManaged = false;

function readStoredToken() {
  if (typeof window === "undefined") return "";
  const launch = takeLaunchToken();
  launchManaged = Boolean(launch);
  if (launch) return launch;
  const injected = window.__MUCARO_BOOTSTRAP__?.token;
  if (injected) return injected;
  try {
    return window.localStorage.getItem(TOKEN_STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

let operatorToken = readStoredToken();

export class ApiError extends Error {
  constructor(status, message, category, payload) {
    super(message);
    this.status = status;
    this.category = category;
    this.payload = payload;
  }
}

function authHeaders(extra = {}) {
  const headers = new Headers(extra);
  if (operatorToken && !headers.has("X-Mucaro-Token")) headers.set("X-Mucaro-Token", operatorToken);
  // Lets the desktop shell confirm that its window is running the app (see backend/app/desktop_shell.py).
  headers.set("X-Mucaro-Client", "mucaro-gui");
  return headers;
}

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...options, headers: authHeaders(options.headers) });
  } catch (err) {
    throw new ApiError("ERROR_BACKEND_UNREACHABLE", "Mucaro's local service is not reachable.", "OFFLINE", { cause: String(err) });
  }
  if (!res.ok) {
    let payload;
    try {
      payload = await res.json();
    } catch {
      payload = { detail: await res.text() };
    }
    const detail = payload.detail ?? payload;
    const status = typeof detail === "object" ? detail.status || String(res.status) : String(res.status);
    const message = typeof detail === "object" ? detail.detail || status : String(detail);
    const category = res.status === 401 || res.status === 403 ? "PERMISSION_DENIED" : res.status === 404 ? "SOURCE_UNAVAILABLE" : undefined;
    throw new ApiError(status, message, category, payload);
  }
  return res.json();
}

const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

/**
 * Follow a run's server-sent events with fetch (EventSource cannot send the auth header).
 * Resumes from `after`; calls onEvent for each event and resolves on `end`.
 */
export async function streamRunEvents(runId, after, onEvent, signal) {
  const res = await fetch(`${API_BASE}/work-runs/${runId}/events/stream?after=${after}`, { headers: authHeaders(), signal });
  if (!res.ok || !res.body) throw new ApiError(String(res.status), "Event stream unavailable.", "INTERNAL_FAILURE");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    let boundary;
    while ((boundary = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      let type = "message";
      const data = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) type = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (!data.length) continue;
      const parsed = JSON.parse(data.join("\n"));
      if (type === "end" || type === "timeout") return parsed;
      onEvent(parsed);
    }
  }
}

export const api = {
  getOperatorToken: () => operatorToken,
  tokenIsLaunchManaged: () => launchManaged,
  setOperatorToken: (token) => {
    operatorToken = token.trim();
    try {
      if (operatorToken) window.localStorage.setItem(TOKEN_STORAGE_KEY, operatorToken);
      else window.localStorage.removeItem(TOKEN_STORAGE_KEY);
    } catch {
      /* storage unavailable: token stays in memory for this session */
    }
  },
  health: () => request("/health"),
  config: () => request("/config"),
  models: () => request("/models"),
  offlineManifest: () => request("/offline-manifest"),
  modes: () => request("/work-runs/modes"),

  conversations: () => request("/conversations"),
  createConversation: (title) => request("/conversations", json("POST", { title })),
  conversation: (id) => request(`/conversations/${id}`),
  renameConversation: (id, title) => request(`/conversations/${id}`, json("PATCH", { title })),
  deleteConversation: (id) => request(`/conversations/${id}`, { method: "DELETE" }),
  conversationRuns: (id) => request(`/conversations/${id}/runs?limit=100`),

  sendMessage: (conversationId, payload) => request(`/conversations/${conversationId}/messages`, json("POST", payload)),
  runEvents: (runId, after = 0) => request(`/work-runs/${runId}/events?after=${after}`),
  workRuns: (beforeSeq) => request(`/work-runs?limit=20${beforeSeq ? `&before_seq=${beforeSeq}` : ""}`),
  stopRun: (runId) => request(`/work-runs/${runId}/stop`, { method: "POST" }),
  editQueuedRun: (runId, prompt, expectedVersion) => request(`/work-runs/${runId}`, json("PATCH", { prompt, expected_version: expectedVersion })),

  approveTool: (callId, argumentsSha256) => request(`/tools/requests/${callId}/approve`, json("POST", { arguments_sha256: argumentsSha256 })),
  denyTool: (callId, argumentsSha256) => request(`/tools/requests/${callId}/deny`, json("POST", { arguments_sha256: argumentsSha256 })),
  toolSummary: () => request("/tools/summary"),
  toolCalls: () => request("/tools/requests"),
  resolveInteraction: (id, payload) => request(`/interactions/${id}/resolve`, json("POST", payload)),

  sources: () => request("/sources"),
  source: (id) => request(`/sources/${id}`),
  deleteSource: (id) => request(`/sources/${id}`, { method: "DELETE" }),
  reindexSources: () => request("/sources/reindex", { method: "POST" }),
  ingest: (file) => {
    const form = new FormData();
    form.append("upload", file);
    return request("/ingest", { method: "POST", body: form });
  },
  directoryPermissions: () => request("/permissions/directories"),
  checkDirectoryPermission: (payload) => request("/permissions/directories/check", json("POST", payload)),
  grantDirectoryPermission: (payload) => request("/permissions/directories/grant", json("POST", payload)),
  revokeDirectoryPermission: (payload) => request("/permissions/directories/revoke", json("POST", payload)),
  ingestPath: (payload) => request("/ingest-path", json("POST", payload)),

  memory: () => request("/memory"),
  remember: (content, kind) => request("/memory", json("POST", { content, kind })),
  updateMemory: (id, enabled) => request(`/memory/${id}`, json("PATCH", { enabled })),
  deleteMemory: (id) => request(`/memory/${id}`, { method: "DELETE" }),
  exportMemory: () => request("/memory/export"),
};
