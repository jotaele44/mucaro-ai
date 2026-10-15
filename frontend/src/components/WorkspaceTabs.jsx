import React from "react";
import { ROUTES } from "../navigation.js";
import { uiText } from "../uiPreferences.js";

export function WorkspaceTabs({ tabs, activeKey, conversations, documents, onSelect, onClose, locale = "en" }) {
  function title(tab) {
    if (tab.view === "chat") {
      if (!tab.resourceId) return uiText(locale, "newChat");
      return conversations.find((row) => row.conversation_id === tab.resourceId)?.title || "Conversation";
    }
    if (tab.view === "documents") {
      if (!tab.resourceId) return uiText(locale, "documents");
      return documents.find((row) => row.document_id === tab.resourceId)?.title || "Document";
    }
    return uiText(locale, tab.view);
  }
  return (
    <div className="workspace-tabs" role="toolbar" aria-label="Open workspace tabs">
      {tabs.map((tab) => (
        <div className="workspace-tab-entry" key={tab.mountId}>
          <button
            type="button"
            className="workspace-tab"
            id={"tab-" + tab.mountId}
            aria-controls={"panel-" + tab.mountId}
            aria-pressed={tab.key === activeKey}
            tabIndex={tab.key === activeKey ? 0 : -1}
            title={title(tab)}
            onClick={() => onSelect(tab.key)}
            onKeyDown={(event) => {
              if (!["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
              event.preventDefault();
              const index=tabs.findIndex((item)=>item.key===tab.key);
              const next=event.key==="Home"?0:event.key==="End"?tabs.length-1:(index+(event.key==="ArrowRight"?1:-1)+tabs.length)%tabs.length;
              onSelect(tabs[next].key);
              requestAnimationFrame(()=>document.getElementById("tab-"+tabs[next].mountId)?.focus());
            }}
          >{title(tab)}</button>
          <button type="button" className="close-tab" aria-label={"Close "+title(tab)+" tab"}
            onClick={() => { onClose(tab.key); requestAnimationFrame(()=>document.querySelector('.workspace-tab[aria-pressed="true"]')?.focus()); }}>×</button>
        </div>
      ))}
    </div>
  );
}
