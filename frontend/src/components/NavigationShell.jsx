import React from "react";
import { ROUTES } from "../navigation.js";
import { MUCARO_BRAND_ICON } from "../brandLogo.js";
import { uiText, uiFlag } from "../uiPreferences.js";

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
export function NavigationShell({ view, contextOpen, setContextOpen, conversations, activeId, filter, setFilter, onSelect, onConversation, onTasks, onCloseMobile, locale = "en", onToggleLanguage, onPetOpen, petState = "IDLE", inactive = false }) {
  const route = ROUTES.find((row) => row.id === view) || ROUTES[0];
  const visible = conversations.filter((row) => String(row.title || "").toLowerCase().includes(filter.trim().toLowerCase()));
  const sectionKey = {
    "sources-title": "sectionOverview", "sources-import": "sectionImport", "sources-list": "sectionLibrary",
    "memory-title": "sectionOverview", "memory-add": "sectionAdd", "memory-list": "sectionSaved",
    "prov-title": "sectionOverview", "prov-actions": "sectionAudit",
    "settings-title": "sectionOverview", "settings-appearance": "appearance", "settings-model": "sectionModel", "settings-retrieval": "sectionRetrieval", "settings-access": "sectionAccess",
  };

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
        <button type="button" className="rail-brand" aria-label={uiText(locale, "home")} title={uiText(locale, "home")} onClick={() => onSelect("chat")}><img className="brand-owl" src={MUCARO_BRAND_ICON} alt="" draggable="false" width="40" height="40" /></button>
        <ul className="rail-pages">
          {ROUTES.map((item) => (
            <li key={item.id}>
              <button type="button" className="rail-button" aria-label={uiText(locale, item.id)} title={uiText(locale, item.id)} aria-current={view === item.id ? "page" : undefined} onClick={() => onSelect(item.id)}>
                <Icon type={item.icon} />
              </button>
            </li>
          ))}
        </ul>
        <div className="rail-spacer" />
        <button type="button" className="rail-button" aria-label={uiText(locale, "tasks")} title={uiText(locale, "tasks")} onClick={onTasks}><Icon type="tasks" /></button>
        <button type="button" className="rail-button rail-pet-button" aria-label={uiText(locale, "pet")} title={uiText(locale, "pet")} data-pet-state={petState} onClick={onPetOpen}>
          <img className="brand-owl" src={MUCARO_BRAND_ICON} alt="" draggable="false" width="30" height="30" />
        </button>
        <button type="button" className="rail-button rail-locale-button" aria-label={uiText(locale, "langSwitch")} title={uiText(locale, "langSwitch")} onClick={onToggleLanguage}>
          <span aria-hidden="true">{uiFlag(locale)}</span>
        </button>
      </nav>

      <aside className="context-sidebar" aria-label={route.label + " navigation"}>
        <header className="context-header">
          <strong>{uiText(locale, route.id)}</strong>
        </header>
        {view === "chat" ? (
          <>
            <button type="button" className="primary-button wide" onClick={() => onConversation(null)}>{uiText(locale, "newChat")}</button>
            <label className="field compact">
              <span className="sr-only">{uiText(locale, "filter")}</span>
              <input type="search" placeholder={uiText(locale, "filter")} value={filter} onChange={(event) => setFilter(event.target.value)} />
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
              {!visible.length && <li className="muted small pad">{uiText(locale, conversations.length ? "noMatches" : "noChats")}</li>}
            </ul>
          </>
        ) : (
          <nav aria-label={uiText(locale, route.id) + " sections"} className="context-sections">
            <ul>
              {route.context.map((item) => <li key={item.target}><button type="button" onClick={() => jump(item.target)}>{uiText(locale, sectionKey[item.target] || item.label)}</button></li>)}
            </ul>
          </nav>
        )}
      </aside>
    </div>
  );
}
