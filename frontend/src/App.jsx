import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, authIssueOf, onAuthIssue } from "./api.js";
import { ChatView } from "./components/ChatView.jsx";
import { TasksDrawer } from "./components/TasksDrawer.jsx";
import { TokenGate } from "./components/TokenGate.jsx";
import { MemoryWorkspace } from "./components/workspaces/MemoryWorkspace.jsx";
import { ProvenanceWorkspace } from "./components/workspaces/ProvenanceWorkspace.jsx";
import { SettingsWorkspace } from "./components/workspaces/SettingsWorkspace.jsx";
import { SourcesWorkspace } from "./components/workspaces/SourcesWorkspace.jsx";

const WORKSPACES = [
  { id: "chat", label: "Chat" },
  { id: "sources", label: "Sources" },
  { id: "memory", label: "Memory" },
  { id: "provenance", label: "Provenance" },
  { id: "settings", label: "Settings" },
];
const DEFAULT_OPTIONS = { model_provider: "mock", retrieval_strategy: "hybrid", allow_live: false, top_k: 5 };
// Foreground liveness check. A phone that was away this long has lost its live streams without any error.
const HEALTH_POLL_MS = 15000;
const RESUME_AFTER_AWAY_MS = 3000;

function stored(key, fallback) {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function persist(key, value) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable */
  }
}

// Same object when nothing changed, so repeated refusals cause no re-render.
const locked = (current, reason) => (current.state === "locked" && current.reason === reason ? current : { state: "locked", reason });

export function App() {
  const [view, setView] = useState("chat");
  const [conversations, setConversations] = useState([]);
  const [activeId, setActiveId] = useState(() => stored("mucaro.activeConversation", null));
  const [filter, setFilter] = useState("");
  const [navOpen, setNavOpen] = useState(false);
  const [tasksOpen, setTasksOpen] = useState(false);
  const [modes, setModes] = useState(null);
  const [claude, setClaude] = useState(null);
  const [options, setOptions] = useState(() => ({ ...DEFAULT_OPTIONS, ...stored("mucaro.options", {}) }));
  const [message, setMessage] = useState("");
  // state: checking | ok | error | locked. `offline` marks a service that answered before and has gone quiet: the UI
  // stays mounted (open sheets, streams and drafts survive a network blip) and reconciles once the service is back.
  // `locked` is a service that is reachable but wants a token this browser lacks (reason: required | rejected | disabled).
  const [health, setHealth] = useState({ state: "checking" });
  const [gateBusy, setGateBusy] = useState(false);
  const [reconnect, setReconnect] = useState(0);
  const healthRef = useRef(health);
  const polling = useRef(false);
  const offline = useRef(false);

  useEffect(() => {
    healthRef.current = health;
  });

  const announce = useCallback((text) => {
    setMessage("");
    window.setTimeout(() => setMessage(text), 30);
  }, []);

  const loadConversations = useCallback(async () => {
    try {
      const payload = await api.conversations();
      setConversations(payload.conversations || []);
    } catch {
      /* surfaced by the health banner */
    }
  }, []);

  // Startup and Retry: decides between ready and unreachable.
  const checkHealth = useCallback(async () => {
    try {
      await api.health();
      const [modePayload] = await Promise.all([api.modes(), loadConversations()]);
      setModes(modePayload);
      api.claude().then(setClaude).catch(() => setClaude(null));
      offline.current = false;
      setHealth((current) => (current.state === "ok" && !current.offline ? current : { state: "ok" }));
    } catch (err) {
      const issue = authIssueOf(err);
      setHealth((current) => (issue ? locked(current, issue) : { state: "error", error: err }));
    }
  }, [loadConversations]);

  // Any call that finds the token refused (the computer restarted Mucaro and made a new one) sends the person to the gate.
  useEffect(() => onAuthIssue((reason) => setHealth((current) => locked(current, reason))), []);

  async function submitToken(token, remember) {
    api.setOperatorToken(token, { remember });
    setGateBusy(true);
    try {
      await checkHealth();
    } finally {
      setGateBusy(false);
    }
  }

  // Foreground poll once the app is up. It never replaces a state object that did not change, so a healthy
  // service causes no re-render, and a blip is shown as a banner instead of tearing the workspace down.
  const pollHealth = useCallback(async () => {
    if (polling.current) return;
    polling.current = true;
    try {
      await api.health();
      if (offline.current) {
        offline.current = false;
        setHealth((current) => (current.state === "ok" ? { state: "ok" } : current));
        loadConversations();
        setReconnect((count) => count + 1);
      }
    } catch {
      offline.current = true;
      setHealth((current) => (current.state === "ok" && !current.offline ? { state: "ok", offline: true } : current));
    } finally {
      polling.current = false;
    }
  }, [loadConversations]);

  useEffect(() => {
    checkHealth();
  }, [checkHealth]);

  // Watchdog and phone lifecycle. A service that never came up is retried so the app starts by itself; a page that
  // was suspended, restored from the back-forward cache, or regained its network asks the chat to reconnect,
  // because iOS drops sockets without telling the page.
  useEffect(() => {
    const tick = () => {
      if (document.hidden) return;
      if (healthRef.current.state === "error") checkHealth();
      else if (healthRef.current.state === "ok") pollHealth();
    };
    const timer = window.setInterval(tick, HEALTH_POLL_MS);
    let awaySince = null;
    const onVisibility = () => {
      if (document.hidden) {
        awaySince = Date.now();
        return;
      }
      const wasAway = awaySince !== null && Date.now() - awaySince > RESUME_AFTER_AWAY_MS;
      awaySince = null;
      if (wasAway) setReconnect((count) => count + 1);
      tick();
    };
    const onOnline = () => {
      setReconnect((count) => count + 1);
      tick();
    };
    const onPageShow = (event) => {
      if (event.persisted) {
        setReconnect((count) => count + 1);
        tick();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("online", onOnline);
    window.addEventListener("pageshow", onPageShow);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("online", onOnline);
      window.removeEventListener("pageshow", onPageShow);
    };
  }, [checkHealth, pollHealth]);

  async function retry() {
    await checkHealth();
    setReconnect((count) => count + 1);
  }

  useEffect(() => persist("mucaro.activeConversation", activeId), [activeId]);
  useEffect(() => persist("mucaro.options", options), [options]);

  function openConversation(id) {
    setActiveId(id);
    setView("chat");
    setNavOpen(false);
  }

  const visible = conversations.filter((row) => row.title.toLowerCase().includes(filter.trim().toLowerCase()));
  return (
    <div className={`app ${navOpen ? "nav-open" : ""}`}>
      <a className="skip-link" href="#main">Skip to content</a>
      <nav className="sidebar" aria-label="Primary" inert={health.state === "locked"}>
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">M</span>
          <span>Mucaro</span>
        </div>
        <button type="button" className="primary-button wide" onClick={() => openConversation(null)}>
          New chat
        </button>
        <ul className="workspace-nav">
          {WORKSPACES.map((workspace) => (
            <li key={workspace.id}>
              <button
                type="button"
                aria-current={view === workspace.id ? "page" : undefined}
                onClick={() => {
                  setView(workspace.id);
                  setNavOpen(false);
                }}
              >
                {workspace.label}
              </button>
            </li>
          ))}
          <li>
            <button type="button" onClick={() => { setTasksOpen(true); setNavOpen(false); }}>Background tasks</button>
          </li>
        </ul>
        <div className="conversation-section">
          <label className="field compact">
            <span className="sr-only">Filter conversations</span>
            <input type="search" placeholder="Filter conversations" value={filter} onChange={(event) => setFilter(event.target.value)} />
          </label>
          <ul className="conversation-list" aria-label="Conversations">
            {visible.map((row) => (
              <li key={row.conversation_id}>
                <button type="button" aria-current={row.conversation_id === activeId && view === "chat" ? "true" : undefined} onClick={() => openConversation(row.conversation_id)}>
                  <span className="conversation-title">{row.title}</span>
                  <span className="muted small">{row.turn_count} turns</span>
                </button>
              </li>
            ))}
            {!visible.length && <li className="muted small pad">{conversations.length ? "No matches." : "No conversations yet."}</li>}
          </ul>
        </div>
      </nav>
      <div className="nav-scrim" onClick={() => setNavOpen(false)} aria-hidden="true" />
      <main id="main" className="main" tabIndex={-1}>
        <div className="mobile-bar">
          <button type="button" className="icon-button" aria-label="Open navigation" aria-expanded={navOpen} onClick={() => setNavOpen(true)}>
            ☰
          </button>
          <span className="brand-inline">Mucaro</span>
        </div>
        {(health.state === "error" || health.offline) && (
          <div className="error-card banner" role="alert">
            <strong>Mucaro's local service is not reachable.</strong> {health.offline ? "Reconnecting…" : "Your data stays on the computer that runs Mucaro."}
            <button type="button" className="secondary-button" onClick={retry}>Retry</button>
          </div>
        )}
        {health.state === "checking" && <p className="muted center pad">Starting…</p>}
        {health.state === "locked" && <TokenGate reason={health.reason} busy={gateBusy} onSubmit={submitToken} onRetry={retry} />}
        {health.state === "ok" && view === "chat" && (
          <ChatView
            reconnect={reconnect}
            conversationId={activeId}
            onConversationCreated={(id) => setActiveId(id)}
            onConversationsChanged={loadConversations}
            onOpenTasks={() => setTasksOpen(true)}
            announce={announce}
            modes={modes}
            defaultOptions={options}
            claude={claude}
            onOpenSettings={() => setView("settings")}
          />
        )}
        {health.state === "ok" && view === "sources" && <SourcesWorkspace announce={announce} />}
        {health.state === "ok" && view === "memory" && <MemoryWorkspace announce={announce} />}
        {health.state === "ok" && view === "provenance" && <ProvenanceWorkspace />}
        {health.state === "ok" && view === "settings" && <SettingsWorkspace options={options} onOptionsChange={setOptions} announce={announce} claude={claude} onClaudeChange={setClaude} />}
      </main>
      {tasksOpen && <TasksDrawer onClose={() => setTasksOpen(false)} />}
      <div className="sr-only" role="status" aria-live="polite">{message}</div>
    </div>
  );
}
