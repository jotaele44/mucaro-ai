// Same-origin by default: the packaged app and `mucaro serve` host the UI and API together.
// For `npm run dev`, vite.config.js proxies API paths to the local backend.
const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const TOKEN_STORAGE_KEY = "mucaro.operatorToken"; // opt-in "remember on this device"
const SESSION_TOKEN_KEY = "mucaro.sessionToken"; // default for a typed token: this browser tab only
const LAUNCH_TOKEN_KEY = "mucaro.launchToken";

// A request that gets no answer must end: mobile networks hand off, sleep and half-open without any error.
const REQUEST_TIMEOUT_MS = 30000;
const HEALTH_TIMEOUT_MS = 5000;
const UPLOAD_TIMEOUT_MS = 120000;
// The server sends a keep-alive comment every 10 s; a stream that is silent for 25 s is dead.
const STREAM_IDLE_MS = 25000;

// Merely reading window.sessionStorage can throw when storage is blocked, so the access stays inside the try.
function readStorage(kind, key) {
  try {
    return window[kind].getItem(key) || "";
  } catch {
    return "";
  }
}

function takeLaunchToken() {
  // The desktop shell opens `/#token=<per-launch token>`. The fragment is never sent to the server;
  // it is removed from the address at once and kept only for this window's session (reloads keep working,
  // the next launch uses a new port, so a new origin and a new token). A phone link carries the same fragment.
  const match = /(?:^#|&)token=([^&]+)/.exec(window.location.hash || "");
  if (match) {
    let token = "";
    try {
      token = decodeURIComponent(match[1]);
    } catch {
      // A malformed %-escape must not stop the app from loading; the user can type the token instead.
    }
    if (token) {
      try {
        window.sessionStorage.setItem(LAUNCH_TOKEN_KEY, token);
      } catch {
        // Storage may be blocked; the in-memory token still works until reload.
      }
    }
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
    return token;
  }
  return readStorage("sessionStorage", LAUNCH_TOKEN_KEY);
}

let launchManaged = false;

function readStoredToken() {
  if (typeof window === "undefined") return "";
  const launch = takeLaunchToken();
  launchManaged = Boolean(launch);
  if (launch) return launch;
  const injected = window.__MUCARO_BOOTSTRAP__?.token;
  if (injected) return injected;
  return readStorage("sessionStorage", SESSION_TOKEN_KEY) || readStorage("localStorage", TOKEN_STORAGE_KEY);
}

let operatorToken = readStoredToken();

export class ApiError extends Error {
  constructor(status, message, category, payload, httpStatus) {
    super(message);
    this.status = status;
    this.category = category;
    this.payload = payload;
    this.httpStatus = httpStatus; // the response's status code; `status` can be a named code from the body instead
  }
}

// What the service says when it was reached from another device but was not started with a token (backend/app/auth.py).
const REMOTE_ACCESS_STATUS = "ERROR_REMOTE_ACCESS_REQUIRES_TOKEN";

/**
 * Why the service refused this person, or null when the error is not about access.
 * "required": no token is stored. "rejected": a token is stored and was refused (typed wrong, or the computer
 * restarted Mucaro and made a new one). "disabled": the computer was not set up for use from another device.
 */
export function authIssueOf(err) {
  if (!(err instanceof ApiError)) return null;
  if (err.httpStatus === 401) return operatorToken ? "rejected" : "required";
  if (err.status === REMOTE_ACCESS_STATUS) return "disabled";
  return null;
}

// Any call can discover that the token stopped working; the app listens once instead of every caller handling it.
const authListeners = new Set();
export function onAuthIssue(listener) {
  authListeners.add(listener);
  return () => authListeners.delete(listener);
}

function authHeaders(extra = {}) {
  const headers = new Headers(extra);
  if (operatorToken && !headers.has("X-Mucaro-Token")) headers.set("X-Mucaro-Token", operatorToken);
  // Lets the desktop shell confirm that its window is running the app (see backend/app/desktop_shell.py).
  headers.set("X-Mucaro-Client", "mucaro-gui");
  return headers;
}

/** Turn a non-2xx response into an ApiError. The body is read once, as text: JSON for API errors, plain text for a 500 or a proxy. */
async function errorFrom(res) {
  const text = (await res.text().catch(() => "")).trim();
  let payload = null;
  try {
    payload = JSON.parse(text);
  } catch {
    /* not JSON: a plain-text 500 or a proxy page */
  }
  if (!payload || typeof payload !== "object") payload = { detail: text.slice(0, 300) || res.statusText || `HTTP ${res.status}` };
  const detail = "detail" in payload ? payload.detail : payload;
  let status = String(res.status);
  let message;
  if (Array.isArray(detail)) {
    message = detail.map((item) => item?.msg || String(item)).join("; ") || status;
  } else if (detail && typeof detail === "object") {
    status = detail.status || status;
    message = detail.detail || status;
  } else {
    message = String(detail);
  }
  const category =
    res.status === 401 || res.status === 403 ? "PERMISSION_DENIED" : res.status === 404 ? "SOURCE_UNAVAILABLE" : res.status >= 500 ? "INTERNAL_FAILURE" : undefined;
  const error = new ApiError(status, message, category, payload, res.status);
  const issue = authIssueOf(error);
  if (issue) for (const listener of authListeners) listener(issue);
  return error;
}

// `as: "blob"` returns the body as a Blob (a file download) instead of parsing JSON.
async function request(path, options = {}) {
  const { timeoutMs = REQUEST_TIMEOUT_MS, as = "json", ...init } = options;
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const unreachable = (err) =>
    timedOut
      ? new ApiError("ERROR_REQUEST_TIMEOUT", "Mucaro's local service did not answer in time.", "OFFLINE", { cause: String(err) })
      : new ApiError("ERROR_BACKEND_UNREACHABLE", "Mucaro's local service is not reachable.", "OFFLINE", { cause: String(err) });
  try {
    let res;
    try {
      res = await fetch(`${API_BASE}${path}`, { ...init, headers: authHeaders(init.headers), signal: controller.signal });
    } catch (err) {
      throw unreachable(err);
    }
    if (!res.ok) throw await errorFrom(res);
    try {
      return as === "blob" ? await res.blob() : await res.json();
    } catch (err) {
      // The connection died mid-body, or a proxy answered with something that is not JSON.
      throw timedOut ? unreachable(err) : new ApiError("ERROR_BAD_RESPONSE", "The service sent an incomplete answer.", "OFFLINE", { cause: String(err) });
    }
  } finally {
    clearTimeout(timer);
  }
}

const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

/**
 * Follow a run's server-sent events with fetch (EventSource cannot send the auth header).
 * Resumes from `after`; calls onEvent for each event. Resolves with the closing event on `end`/`timeout`, and with
 * null when the server closed the stream without one. Rejects when the connection fails or goes quiet for idleMs.
 */
export async function streamRunEvents(runId, after, onEvent, signal, { idleMs = STREAM_IDLE_MS } = {}) {
  const inner = new AbortController();
  const forwardAbort = () => inner.abort();
  if (signal?.aborted) inner.abort();
  else signal?.addEventListener("abort", forwardAbort, { once: true });
  let idle = false;
  let timer = null;
  const arm = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      idle = true;
      inner.abort();
    }, idleMs);
  };
  try {
    arm();
    const res = await fetch(`${API_BASE}/work-runs/${runId}/events/stream?after=${after}`, { headers: authHeaders(), signal: inner.signal });
    if (!res.ok) throw await errorFrom(res);
    if (!res.body) throw new ApiError(String(res.status), "Event stream unavailable.", "INTERNAL_FAILURE");
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      arm(); // any bytes, including the keep-alive comment, prove the connection is alive
      if (done) return null;
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
        let parsed;
        try {
          parsed = JSON.parse(data.join("\n"));
        } catch {
          continue; // one malformed event must not end the stream
        }
        if (type === "end" || type === "timeout") return parsed;
        onEvent(parsed);
      }
    }
  } catch (err) {
    if (idle) throw new ApiError("ERROR_STREAM_IDLE", "The live connection went quiet.", "OFFLINE", { cause: String(err) });
    throw err;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", forwardAbort);
  }
}

export const api = {
  getOperatorToken: () => operatorToken,
  tokenIsLaunchManaged: () => launchManaged,
  // `remember` keeps the token on this device; otherwise it lives only as long as this browser tab. A typed token
  // replaces a link's token too, so a stale one cannot come back on reload.
  setOperatorToken: (token, { remember = false } = {}) => {
    operatorToken = token.trim();
    launchManaged = false;
    try {
      window.sessionStorage.removeItem(LAUNCH_TOKEN_KEY);
      window.sessionStorage.removeItem(SESSION_TOKEN_KEY);
      window.localStorage.removeItem(TOKEN_STORAGE_KEY);
      if (operatorToken) {
        if (remember) window.localStorage.setItem(TOKEN_STORAGE_KEY, operatorToken);
        else window.sessionStorage.setItem(SESSION_TOKEN_KEY, operatorToken);
      }
    } catch {
      /* storage unavailable: token stays in memory for this session */
    }
  },
  health: () => request("/health", { timeoutMs: HEALTH_TIMEOUT_MS }),
  config: () => request("/config"),
  models: () => request("/models"),
  offlineManifest: () => request("/offline-manifest"),
  modes: () => request("/work-runs/modes"),
  preferences: () => request("/preferences"),
  updatePreferences: (changes, expectedRevision = null) =>
    request("/preferences", json("PATCH", { changes, expected_revision: expectedRevision })),

  conversations: () => request("/conversations"),
  createConversation: (title) => request("/conversations", json("POST", { title })),
  conversation: (id) => request(`/conversations/${id}`),
  renameConversation: (id, title) => request(`/conversations/${id}`, json("PATCH", { title })),
  deleteConversation: (id) => request(`/conversations/${id}`, { method: "DELETE" }),
  conversationRuns: (id) => request(`/conversations/${id}/runs?limit=100`),

  sendMessage: (conversationId, payload) => request(`/conversations/${conversationId}/messages`, json("POST", payload)),
  editConversationMessage: (conversationId, messageKey, content, expectedRevision) =>
    request(
      `/conversations/${conversationId}/messages/${encodeURIComponent(messageKey)}`,
      json("PATCH", { content, expected_revision: expectedRevision }),
    ),
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
    return request("/ingest", { method: "POST", body: form, timeoutMs: UPLOAD_TIMEOUT_MS });
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
  // One of the files saved with an answer: kind is "markdown", "json" or "csv".
  exportFile: (runId, kind) => request(`/exports/${encodeURIComponent(runId)}/files/${encodeURIComponent(kind)}`, { as: "blob" }),
};
