import React, { useCallback, useEffect, useRef, useState } from "react";
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
import { MucaroPet } from "./components/MucaroPet.jsx";
import { MUCARO_BRAND_ICON } from "./brandLogo.js";
import { derivePetRuntime } from "./petRuntime.js";
import { effectiveTheme, normalizeUIPrefs, readUIPrefs, uiText, writeUIPrefs } from "./uiPreferences.js";

const MOBILE_NAV_QUERY = "(max-width: 760px), (max-height: 550px) and (pointer: coarse)";
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
  const [mobileOverlay, setMobileOverlay] = useState(() => window.matchMedia(MOBILE_NAV_QUERY).matches);
  const [tasksOpen, setTasksOpen] = useState(false);
  const [modes, setModes] = useState(null);
  const [options, setOptions] = useState(() => ({ ...DEFAULT_OPTIONS, ...stored("mucaro.options", {}) }));
  const [message, setMessage] = useState("");
  const [health, setHealth] = useState({ state: "checking" });
  const [ready, setReady] = useState(false);
  const [uiPrefs, setUiPrefs] = useState(readUIPrefs);
  const [systemDark, setSystemDark] = useState(() => window.matchMedia("(prefers-color-scheme: dark)").matches);
  const [petPanelOpen, setPetPanelOpen] = useState(false);
  const [petObservation, setPetObservation] = useState(null);
  const observationsRef = useRef(new Map());
  // An in-flight send may finish after the user has opened/closed another tab.
  // Refs prevent a stale async ChatView callback from overwriting newer navigation.
  const tabsRef = useRef(tabs);
  const selectedRef = useRef(activeTabKey);
  const tasksFromMobileRef = useRef(false);
  tabsRef.current = tabs;
  selectedRef.current = activeTabKey;

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

  // Match the CSS overlay breakpoint, including short coarse-pointer landscape screens.
  useEffect(() => {
    const query = window.matchMedia(MOBILE_NAV_QUERY);
    const sync = () => {
      setMobileOverlay(query.matches);
      if (!query.matches) setNavOpen(false);
    };
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
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

  useEffect(() => {
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setSystemDark(query.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    writeUIPrefs(uiPrefs);
    document.documentElement.dataset.mucaroTheme = effectiveTheme(uiPrefs.theme, systemDark);
    document.documentElement.lang = uiPrefs.language;
    document.querySelector('link[rel="icon"]')?.setAttribute("href", MUCARO_BRAND_ICON);
  }, [uiPrefs, systemDark]);

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

  function showTasks() {
    tasksFromMobileRef.current = mobileOverlay && navOpen;
    setTasksOpen(true);
    setNavOpen(false);
  }

  function closeTasks() {
    setTasksOpen(false);
    if (tasksFromMobileRef.current) {
      tasksFromMobileRef.current = false;
      window.requestAnimationFrame(() => document.querySelector(".mobile-bar .icon-button")?.focus());
    }
  }

  const recordRuntime = useCallback((tabKey, observation) => {
    if (!tabsRef.current.some((tab) => tab.key === tabKey)) return;
    observationsRef.current.set(tabKey, observation);
    if (selectedRef.current === tabKey) setPetObservation(observation);
  }, []);

  function toggleLanguage() {
    setUiPrefs((previous) => normalizeUIPrefs({ ...previous, language: previous.language === "es" ? "en" : "es" }));
  }

  function commitNavigation(nextTabs, nextKey) {
    tabsRef.current = nextTabs;
    selectedRef.current = nextKey;
    setTabs(nextTabs);
    setActiveTabKey(nextKey);
    const active = nextTabs.find((tab) => tab.key === nextKey);
    setPetObservation(active?.view === "chat" ? observationsRef.current.get(nextKey) || null : null);
  }

  function activateTab(key) {
    if (!tabsRef.current.some((tab) => tab.key === key)) return;
    selectedRef.current = key;
    setActiveTabKey(key);
    const active = tabsRef.current.find((tab) => tab.key === key);
    setPetObservation(active?.view === "chat" ? observationsRef.current.get(key) || null : null);
    focusTabAfterMobileNavigation(key);
    setNavOpen(false);
  }

  function focusTabAfterMobileNavigation(key) {
    if (!mobileOverlay || !navOpen) return;
    window.requestAnimationFrame(() => {
      const selected = tabsRef.current.find((tab) => tab.key === key);
      if (selected) document.getElementById("tab-" + selected.mountId)?.focus();
    });
  }

  function openWorkspaceTab(tab) {
    const outcome = addTab(tabsRef.current, tab);
    if (outcome.rejected) {
      announce("Ten tabs are open. Close a tab before opening another.");
      return;
    }
    commitNavigation(outcome.tabs, outcome.key);
    focusTabAfterMobileNavigation(outcome.key);
    setNavOpen(false);
  }

  function selectWorkspace(id) {
    if (id === "chat") {
      const currentTabs = tabsRef.current;
      const recent = currentTabs.find((tab) => tab.key === selectedRef.current && tab.view === "chat")
        || currentTabs.slice().reverse().find((tab) => tab.view === "chat");
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
    observationsRef.current.delete(key);
    const outcome = removeTab(tabsRef.current, key, selectedRef.current);
    commitNavigation(outcome.tabs, outcome.key);
  }

  function conversationTransition(key, id) {
    const currentTabs = tabsRef.current;
    if (!currentTabs.some((tab) => tab.key === key)) {
      // Delayed result for an intentionally closed tab: preserve server data but no UI resurrection.
      loadConversations();
      return;
    }
    if (id == null) {
      observationsRef.current.delete(key);
      const remaining = currentTabs.filter((tab) => tab.key !== key);
      const fresh = remaining.find((tab) => tab.key === "chat:new") || makeTab("chat");
      const nextTabs = remaining.some((tab) => tab.key === fresh.key) ? remaining : [...remaining, fresh];
      commitNavigation(nextTabs, selectedRef.current === key ? fresh.key : selectedRef.current);
      return;
    }
    const outcome = adoptConversation(currentTabs, key, id);
    if (!outcome) return announce("Unable to bind the new conversation to its tab.");
    if (observationsRef.current.has(key)) {
      observationsRef.current.set(outcome.key, observationsRef.current.get(key));
      observationsRef.current.delete(key);
    }
    commitNavigation(outcome.tabs, selectedRef.current === key ? outcome.key : selectedRef.current);
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
        onTasks={showTasks}
        onCloseMobile={() => setNavOpen(false)}
        inactive={mobileOverlay && !navOpen}
        locale={uiPrefs.language}
        onToggleLanguage={toggleLanguage}
        onPetOpen={() => setPetPanelOpen(true)}
        petState={derivePetRuntime(health.state, petObservation).state}
      />
      <button type="button" className="context-edge-handle"
        aria-label={uiText(uiPrefs.language, contextOpen ? "collapse" : "expand")}
        aria-expanded={contextOpen} aria-controls="navigation-shell"
        onClick={() => setContextOpen(!contextOpen)}><span aria-hidden="true">☰</span></button>
      <div className="nav-scrim" onClick={() => setNavOpen(false)} aria-hidden="true" />
      <main id="main" className="main" tabIndex={-1} inert={mobileOverlay && navOpen}>
        <div className="mobile-bar">
          <button type="button" className="icon-button" aria-label="Open navigation" aria-controls="navigation-shell" aria-expanded={navOpen} onClick={() => setNavOpen(true)}>☰</button>
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
          onSelect={activateTab}
          onClose={closeWorkspaceTab}
          locale={uiPrefs.language}
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
                    onOpenTasks={showTasks}
                    announce={announce}
                    modes={modes}
                    defaultOptions={options}
                    onRuntimeEvent={(observation) => recordRuntime(tab.key, observation)}
                  />
                )}
                {tab.view === "sources" && <SourcesWorkspace announce={announce} />}
                {tab.view === "memory" && <MemoryWorkspace announce={announce} />}
                {tab.view === "provenance" && <ProvenanceWorkspace />}
                {tab.view === "settings" && <SettingsWorkspace options={options} onOptionsChange={setOptions}
                    uiPrefs={uiPrefs} onUIPrefsChange={(next) => setUiPrefs(normalizeUIPrefs(next))}
                    announce={announce} />}
              </div>
            ))}
          </div>
        )}
      </main>
      {tasksOpen && <TasksDrawer onClose={closeTasks} />}
      <MucaroPet prefs={uiPrefs.pet}
        onChange={(pet) => setUiPrefs((old) => normalizeUIPrefs({ ...old, pet }))}
        runtime={derivePetRuntime(health.state, petObservation)}
        locale={uiPrefs.language}
        open={petPanelOpen}
        onOpenChange={setPetPanelOpen}
      />
      <div className="sr-only" role="status" aria-live="polite">{message}</div>
    </div>
  );
}
