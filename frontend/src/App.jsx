import React, { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import { ChatView } from "./components/ChatView.jsx";
import { QuitDialog, StoppedScreen } from "./components/QuitDialog.jsx";
import { TasksDrawer } from "./components/TasksDrawer.jsx";
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

export function App() {
  const [view, setView] = useState("chat");
  const [conversations, setConversations] = useState([]);
  const [activeId, setActiveId] = useState(() => stored("mucaro.activeConversation", null));
  const [filter, setFilter] = useState("");
  const [navOpen, setNavOpen] = useState(false);
  const [tasksOpen, setTasksOpen] = useState(false);
  const [modes, setModes] = useState(null);
  const [options, setOptions] = useState(() => ({ ...DEFAULT_OPTIONS, ...stored("mucaro.options", {}) }));
  const [message, setMessage] = useState("");
  const [health, setHealth] = useState({ state: "checking" });
  // Quit is offered only when a Start Mucaro AI launcher supervises the service (config.desktop.quit_supported).
  const [quitSupported, setQuitSupported] = useState(false);
  const [quitOpen, setQuitOpen] = useState(false);
  const [stopped, setStopped] = useState(false);
  const closeQuit = useCallback(() => setQuitOpen(false), []);
  const openQuit = useCallback(() => {
    setNavOpen(false);
    setQuitOpen(true);
  }, []);

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

  const checkHealth = useCallback(async () => {
    try {
      await api.health();
      setHealth({ state: "ok" });
      const [modePayload, configPayload] = await Promise.all([api.modes(), api.config().catch(() => null), loadConversations()]);
      setModes(modePayload);
      setQuitSupported(Boolean(configPayload?.desktop?.quit_supported));
    } catch (err) {
      setHealth({ state: "error", error: err });
    }
  }, [loadConversations]);

  useEffect(() => {
    checkHealth();
  }, [checkHealth]);

  // Watchdog: a local service that stops mid-session is reported instead of leaving requests hanging.
  useEffect(() => {
    if (stopped) return undefined;
    const timer = window.setInterval(async () => {
      if (document.hidden) return;
      try {
        await api.health();
        setHealth((current) => (current.state === "error" ? current : { state: "ok" }));
      } catch (err) {
        setHealth({ state: "error", error: err });
      }
    }, 4000);
    return () => window.clearInterval(timer);
  }, [stopped]);

  useEffect(() => persist("mucaro.activeConversation", activeId), [activeId]);
  useEffect(() => persist("mucaro.options", options), [options]);

  function openConversation(id) {
    setActiveId(id);
    setView("chat");
    setNavOpen(false);
  }

  const visible = conversations.filter((row) => row.title.toLowerCase().includes(filter.trim().toLowerCase()));
  if (stopped) return <StoppedScreen />;
  return (
    <div className={`app ${navOpen ? "nav-open" : ""}`}>
      <a className="skip-link" href="#main">Skip to content</a>
      <nav className="sidebar" aria-label="Primary">
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
        {quitSupported && (
          <button type="button" className="secondary-button wide" onClick={openQuit}>
            Quit Mucaro AI
          </button>
        )}
      </nav>
      <div className="nav-scrim" onClick={() => setNavOpen(false)} aria-hidden="true" />
      <main id="main" className="main" tabIndex={-1}>
        <div className="mobile-bar">
          <button type="button" className="icon-button" aria-label="Open navigation" aria-expanded={navOpen} onClick={() => setNavOpen(true)}>
            ☰
          </button>
          <span className="brand-inline">Mucaro</span>
        </div>
        {health.state === "error" && (
          <div className="error-card banner" role="alert">
            <strong>Mucaro's local service is not reachable.</strong> Your data is safe on this Mac.
            <button type="button" className="secondary-button" onClick={checkHealth}>Retry</button>
          </div>
        )}
        {health.state === "checking" && <p className="muted center pad">Starting…</p>}
        {health.state === "ok" && view === "chat" && (
          <ChatView
            conversationId={activeId}
            onConversationCreated={(id) => setActiveId(id)}
            onConversationsChanged={loadConversations}
            onOpenTasks={() => setTasksOpen(true)}
            announce={announce}
            modes={modes}
            defaultOptions={options}
          />
        )}
        {health.state === "ok" && view === "sources" && <SourcesWorkspace announce={announce} />}
        {health.state === "ok" && view === "memory" && <MemoryWorkspace announce={announce} />}
        {health.state === "ok" && view === "provenance" && <ProvenanceWorkspace />}
        {health.state === "ok" && view === "settings" && <SettingsWorkspace options={options} onOptionsChange={setOptions} announce={announce} onQuit={quitSupported ? openQuit : null} />}
      </main>
      {tasksOpen && <TasksDrawer onClose={() => setTasksOpen(false)} />}
      {quitOpen && <QuitDialog onClose={closeQuit} onStopped={() => setStopped(true)} />}
      <div className="sr-only" role="status" aria-live="polite">{message}</div>
    </div>
  );
}
