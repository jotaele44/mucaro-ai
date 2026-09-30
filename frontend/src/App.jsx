import React, { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import { ChatView } from "./components/ChatView.jsx";
import { NavigationShell } from "./components/NavigationShell.jsx";
import { WorkspaceTabs } from "./components/WorkspaceTabs.jsx";
import { addTab, adoptConversation, makeTab, removeTab, restoreTabs } from "./navigation.js";
import { TasksDrawer } from "./components/TasksDrawer.jsx";
import { MemoryWorkspace } from "./components/workspaces/MemoryWorkspace.jsx";
import { ProvenanceWorkspace } from "./components/workspaces/ProvenanceWorkspace.jsx";
import { SettingsWorkspace } from "./components/workspaces/SettingsWorkspace.jsx";
import { SourcesWorkspace } from "./components/workspaces/SourcesWorkspace.jsx";

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
  const [tabs, setTabs] = useState(() => restoreTabs(stored("mucaro.nav.tabs.v1", null), stored("mucaro.activeConversation", null)));
  const [activeTabKey, setActiveTabKey] = useState(() => {
    const saved = stored("mucaro.nav.active.v1", null);
    return tabs.some((tab) => tab.key === saved) ? saved : tabs[0].key;
  });
  const [contextOpen, setContextOpen] = useState(() => stored("mucaro.nav.contextOpen.v1", true) !== false);
  const [conversations, setConversations] = useState([]);
  const [filter, setFilter] = useState("");
  const [navOpen, setNavOpen] = useState(false);
  const [tasksOpen, setTasksOpen] = useState(false);
  const [modes, setModes] = useState(null);
  const [options, setOptions] = useState(() => ({ ...DEFAULT_OPTIONS, ...stored("mucaro.options", {}) }));
  const [message, setMessage] = useState("");
  const [health, setHealth] = useState({ state: "checking" });
  const [ready, setReady] = useState(false);

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
      setReady(true);
      const [modePayload] = await Promise.all([api.modes(), loadConversations()]);
      setModes(modePayload);
    } catch (err) {
      setHealth({ state: "error", error: err });
    }
  }, [loadConversations]);

  useEffect(() => {
    checkHealth();
  }, [checkHealth]);

  // Watchdog: a local service that stops mid-session is reported instead of leaving requests hanging.
  useEffect(() => {
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
  }, []);

  // An open phone drawer must not leave the workspace inert after a resize to desktop.
  useEffect(() => {
    const desktop = window.matchMedia("(min-width: 761px) and (min-height: 551px), (min-width: 761px) and (pointer: fine)");
    const restore = () => { if (desktop.matches) setNavOpen(false); };
    desktop.addEventListener("change", restore);
    return () => desktop.removeEventListener("change", restore);
  }, []);

  // Mobile navigation is dismissible without a pointer. Restore focus to its opener.
  useEffect(() => {
    if (!navOpen) return undefined;
    document.querySelector(".rail-brand")?.focus();
    function dismiss(event) {
      if (event.key === "Escape") {
        setNavOpen(false);
        window.requestAnimationFrame(() => document.querySelector(".mobile-bar .icon-button")?.focus());
      }
    }
    document.addEventListener("keydown", dismiss);
    return () => document.removeEventListener("keydown", dismiss);
  }, [navOpen]);

  useEffect(() => persist("mucaro.options", options), [options]);
  useEffect(() => persist("mucaro.nav.tabs.v1", tabs), [tabs]);
  useEffect(() => persist("mucaro.nav.active.v1", activeTabKey), [activeTabKey]);
  useEffect(() => persist("mucaro.nav.contextOpen.v1", contextOpen), [contextOpen]);

  const activeTab = tabs.find((tab) => tab.key === activeTabKey) || tabs[0];
  const view = activeTab.view;
  const activeId = view === "chat" ? activeTab.conversationId : null;
  useEffect(() => {
    if (activeTab.view === "chat") persist("mucaro.activeConversation", activeTab.conversationId);
  }, [activeTab.key, activeTab.view, activeTab.conversationId]);

  function openWorkspaceTab(tab) {
    const outcome = addTab(tabs, tab);
    if (outcome.rejected) {
      announce("Ten tabs are open. Close a tab before opening another.");
      return;
    }
    setTabs(outcome.tabs);
    setActiveTabKey(outcome.key);
    setNavOpen(false);
  }

  function selectWorkspace(id) {
    if (id === "chat") {
      const recent = activeTab.view === "chat" ? activeTab : tabs.slice().reverse().find((tab) => tab.view === "chat");
      openWorkspaceTab(recent || makeTab("chat"));
    } else {
      openWorkspaceTab(makeTab(id));
    }
  }

  function openConversation(id) {
    const tab = makeTab("chat", id);
    if (!tab) return announce("Invalid conversation identifier.");
    openWorkspaceTab(tab);
  }

  function closeWorkspaceTab(key) {
    const outcome = removeTab(tabs, key, activeTabKey);
    setTabs(outcome.tabs);
    setActiveTabKey(outcome.key);
  }

  function conversationTransition(key, id) {
    if (id == null) {
      // A deleted conversation cannot remain in the tab model or restore on restart.
      const remaining = tabs.filter((tab) => tab.key !== key);
      const fresh = remaining.find((tab) => tab.key === "chat:new") || makeTab("chat");
      setTabs(remaining.some((tab) => tab.key === fresh.key) ? remaining : [...remaining, fresh]);
      setActiveTabKey(fresh.key);
      return;
    }
    const outcome = adoptConversation(tabs, key, id);
    if (!outcome) return announce("Unable to bind the new conversation to its tab.");
    setTabs(outcome.tabs);
    setActiveTabKey(outcome.key);
    loadConversations();
  }
  return (
    <div className={"app " + (navOpen ? "nav-open " : "") + (contextOpen ? "context-open" : "context-closed")}>
      <a className="skip-link" href="#main">Skip to content</a>
      <NavigationShell
        view={view}
        contextOpen={contextOpen}
        setContextOpen={setContextOpen}
        conversations={conversations}
        activeId={activeId}
        filter={filter}
        setFilter={setFilter}
        onSelect={selectWorkspace}
        onConversation={openConversation}
        onTasks={() => { setTasksOpen(true); setNavOpen(false); }}
        onCloseMobile={() => setNavOpen(false)}
      />
      <div className="nav-scrim" onClick={() => setNavOpen(false)} aria-hidden="true" />
      <main id="main" className="main" tabIndex={-1} inert={navOpen}>
        <div className="mobile-bar">
          <button type="button" className="icon-button" aria-label="Open navigation" aria-expanded={navOpen} onClick={() => setNavOpen(true)}>☰</button>
          <span className="brand-inline">Mucaro</span>
        </div>
        {health.state === "error" && (
          <div className="error-card banner" role="alert">
            <strong>Mucaro's local service is not reachable.</strong> Check the local service and retry.
            <button type="button" className="secondary-button" onClick={checkHealth}>Retry</button>
          </div>
        )}
        {health.state === "checking" && !ready && <p className="muted center pad">Starting…</p>}
        <WorkspaceTabs
          tabs={tabs}
          activeKey={activeTabKey}
          conversations={conversations}
          onSelect={(key) => { setActiveTabKey(key); setNavOpen(false); }}
          onClose={closeWorkspaceTab}
        />
        {ready && (
          <div className="workspace-panels">
            {tabs.map((tab) => (
              <div
                key={tab.mountId}
                id={"panel-" + tab.mountId}
                role="tabpanel"
                aria-labelledby={"tab-" + tab.mountId}
                className="workspace-pane"
                data-tab-key={tab.key}
                hidden={tab.key !== activeTabKey}
              >
                {tab.view === "chat" && (
                  <ChatView
                    conversationId={tab.conversationId}
                    onConversationCreated={(id) => conversationTransition(tab.key, id)}
                    onConversationsChanged={loadConversations}
                    onOpenTasks={() => setTasksOpen(true)}
                    announce={announce}
                    modes={modes}
                    defaultOptions={options}
                  />
                )}
                {tab.view === "sources" && <SourcesWorkspace announce={announce} />}
                {tab.view === "memory" && <MemoryWorkspace announce={announce} />}
                {tab.view === "provenance" && <ProvenanceWorkspace />}
                {tab.view === "settings" && <SettingsWorkspace options={options} onOptionsChange={setOptions} announce={announce} />}
              </div>
            ))}
          </div>
        )}
      </main>
      {tasksOpen && <TasksDrawer onClose={() => setTasksOpen(false)} />}
      <div className="sr-only" role="status" aria-live="polite">{message}</div>
    </div>
  );
}
