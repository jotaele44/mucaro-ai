import React from "react";
import { ROUTES } from "../navigation.js";

// Dependency-free SVGs keep navigation available in the offline desktop bundle.
const PATHS = {
  chat: "M4 5h16v12H9l-5 4V5z",
  sources: "M7 3h7l5 5v13H7V3zm7 0v5h5M10 12h6M10 16h6",
  memory: "M12 5c-2-3-7-1-7 3-3 1-2 5 0 6-1 4 4 6 7 3 3 3 8 1 7-3 2-2 2-5 0-6 1-4-2-6-6-5M12 5v12M9 9l3 2m3-2-3 2",
  provenance: "M12 3l9 4v5c0 5-4 8-9 10-5-2-9-5-9-10V7l9-4zM8 12l3 3 5-6",
  settings: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zm0-5v3m0 12v3M3 12h3m12 0h3M5.6 5.6l2.1 2.1m8.6 8.6 2.1 2.1m0-12.8-2.1 2.1m-8.6 8.6-2.1 2.1",
  tasks: "M4 6h16M4 12h16M4 18h16M7 4v4M7 10v4M7 16v4",
  toggle: "M3 5h18M3 12h18M3 19h18",
};
function Icon({ type }) {
  return <svg className="rail-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" focusable="false" aria-hidden="true"><path d={PATHS[type] || PATHS.chat} /></svg>;
}
export function NavigationShell({ view, contextOpen, setContextOpen, conversations, activeId, filter, setFilter, onSelect, onConversation, onTasks, onCloseMobile, inactive = false }) {
  const route = ROUTES.find((row) => row.id === view) || ROUTES[0];
  const visible = conversations.filter((row) => String(row.title || "").toLowerCase().includes(filter.trim().toLowerCase()));

  function jump(target) {
    const element = document.getElementById(target);
    if (!element) return;
    element.scrollIntoView({ block: "start", behavior: "auto" });
    element.setAttribute("tabindex", "-1");
    onCloseMobile();
    window.requestAnimationFrame(() => element.focus({ preventScroll: true }));
  }
  return (
    <div id="navigation-shell" className="sidebar" inert={inactive}>
      <nav className="primary-rail" aria-label="Primary">
        <button type="button" className="rail-brand" aria-label="Home — Chat" title="Mucaro home" onClick={() => onSelect("chat")}><span aria-hidden="true">M</span></button>
        <ul className="rail-pages">
          {ROUTES.map((item) => (
            <li key={item.id}>
              <button type="button" className="rail-button" aria-label={item.label} title={item.label} aria-current={view === item.id ? "page" : undefined} onClick={() => onSelect(item.id)}>
                <Icon type={item.icon} />
              </button>
            </li>
          ))}
        </ul>
        <div className="rail-spacer" />
        <button type="button" className="rail-button" aria-label="Background tasks" title="Background tasks" onClick={onTasks}><Icon type="tasks" /></button>
        <button type="button" className="rail-button context-toggle" aria-label={contextOpen ? "Collapse contextual sidebar" : "Expand contextual sidebar"} aria-expanded={contextOpen} title={contextOpen ? "Collapse sidebar" : "Expand sidebar"} onClick={() => setContextOpen(!contextOpen)}><Icon type="toggle" /></button>
      </nav>

      <aside className="context-sidebar" aria-label={route.label + " navigation"}>
        <header className="context-header">
          <strong>{route.label}</strong>
        </header>
        {view === "chat" ? (
          <>
            <button type="button" className="primary-button wide" onClick={() => onConversation(null)}>New chat</button>
            <label className="field compact">
              <span className="sr-only">Filter conversations</span>
              <input type="search" placeholder="Filter conversations" value={filter} onChange={(event) => setFilter(event.target.value)} />
            </label>
            <ul className="conversation-list" aria-label="Conversations">
              {visible.map((row) => (
                <li key={row.conversation_id}>
                  <button type="button" aria-current={row.conversation_id === activeId ? "true" : undefined} onClick={() => onConversation(row.conversation_id)}>
                    <span className="conversation-title">{row.title}</span>
                    <span className="muted small">{row.turn_count} turns</span>
                  </button>
                </li>
              ))}
              {!visible.length && <li className="muted small pad">{conversations.length ? "No matches." : "No conversations yet."}</li>}
            </ul>
          </>
        ) : (
          <nav aria-label={route.label + " sections"} className="context-sections">
            <ul>
              {route.context.map((item) => <li key={item.target}><button type="button" onClick={() => jump(item.target)}>{item.label}</button></li>)}
            </ul>
          </nav>
        )}
      </aside>
    </div>
  );
}
