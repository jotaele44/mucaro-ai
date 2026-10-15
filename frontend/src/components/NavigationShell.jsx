import React from "react";
import { ROUTES } from "../navigation.js";
import { MUCARO_BRAND_ICON } from "../brandLogo.js";
import { uiText } from "../uiPreferences.js";

const PATHS = {
  chat:"M4 5h16v12H9l-5 4V5z",
  sources:"M7 3h7l5 5v13H7V3zm7 0v5h5M10 12h6M10 16h6",
  documents:"M6 3h9l3 3v15H6V3zm9 0v4h4M9 11h6M9 15h6",
  memory:"M12 5c-2-3-7-1-7 3-3 1-2 5 0 6-1 4 4 6 7 3 3 3 8 1 7-3 2-2 2-5 0-6 1-4-2-6-6-5M12 5v12M9 9l3 2m3-2-3 2",
  provenance:"M12 3l9 4v5c0 5-4 8-9 10-5-2-9-5-9-10V7l9-4zM8 12l3 3 5-6",
  settings:"M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zm0-5v3m0 12v3M3 12h3m12 0h3M5.6 5.6l2.1 2.1m8.6 8.6 2.1 2.1m0-12.8-2.1 2.1m-8.6 8.6-2.1 2.1",
  tasks:"M4 6h16M4 12h16M4 18h16M7 4v4M7 10v4M7 16v4",
};
function Icon({type}) {
  return <svg className="rail-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={PATHS[type]||PATHS.chat}/></svg>;
}
function Row({selected,onClick,title,meta,pinned=false}) {
  return <button type="button" className="context-row" aria-current={selected?"true":undefined} onClick={onClick}>
    <span className="context-row-main">{pinned && <span aria-hidden="true" className="pin-mark">•</span>}<span className="context-row-title">{title}</span></span>
    {meta && <span className="muted small">{meta}</span>}
  </button>;
}

export function NavigationShell({
  view, conversations, documents, activeResourceId, filter, setFilter,
  pinnedConversationIds=[], onTogglePin, onSelectRoute, onOpenConversation, onOpenDocument,
  onNewDocument, onTasks, onSettings, contextOpen, locale="en", inactive=false,
}) {
  const route=ROUTES.find((row)=>row.id===view)||ROUTES[0];
  const q=filter.trim().toLowerCase();
  const visibleConversations=conversations.filter((row)=>!q||String(row.title||"").toLowerCase().includes(q));
  const pinned=visibleConversations.filter((row)=>pinnedConversationIds.includes(row.conversation_id));
  const recent=visibleConversations.filter((row)=>!pinnedConversationIds.includes(row.conversation_id));
  const visibleDocuments=documents.filter((row)=>!q||String(row.title||"").toLowerCase().includes(q));
  function jump(target){const node=document.getElementById(target);if(node){node.scrollIntoView({behavior:"smooth",block:"start"});node.setAttribute("tabindex","-1");requestAnimationFrame(()=>node.focus({preventScroll:true}));}}
  return (
    <div id="navigation-shell" className="navigation-shell" inert={inactive}>
      <nav className="primary-rail" aria-label="Primary">
        <button type="button" className="rail-brand" aria-label="Mucaro home" onClick={()=>onSelectRoute("chat")}>
          <img className="brand-owl" src={MUCARO_BRAND_ICON} alt="" width="40" height="40" draggable="false"/>
        </button>
        <ul className="rail-pages">
          {ROUTES.filter((item)=>item.id!=="settings").map((item)=><li key={item.id}><button type="button" className="rail-button"
            aria-current={view===item.id?"page":undefined} aria-label={uiText(locale,item.id)} title={uiText(locale,item.id)}
            onClick={()=>onSelectRoute(item.id)}><Icon type={item.icon}/></button></li>)}
        </ul>
        <div className="rail-spacer"/>
        <button type="button" className="rail-button" aria-current={view==="settings"?"page":undefined} aria-label={uiText(locale,"settings")} title={uiText(locale,"settings")} onClick={onSettings}><Icon type="settings"/></button>
        <button type="button" className="rail-button" aria-label={uiText(locale,"tasks")} title={uiText(locale,"tasks")} onClick={onTasks}><Icon type="tasks"/></button>
      </nav>
      <aside className={"context-sidebar "+(contextOpen?"":"context-sidebar-collapsed")} aria-label={uiText(locale,route.id)+" navigation"}>
        <header className="context-header">
          <strong>{uiText(locale,route.id)}</strong>
        </header>
        {(view==="chat"||view==="documents") && <label className="context-search">
          <span className="sr-only">{uiText(locale,"search")}</span>
          <input type="search" value={filter} onChange={(e)=>setFilter(e.target.value)} placeholder={uiText(locale,"search")}/>
        </label>}
        {view==="chat" ? <>
          <button type="button" className="primary-button wide" onClick={()=>onOpenConversation(null)}>{uiText(locale,"newChat")}</button>
          {pinned.length>0 && <section className="context-list-section"><h3>{uiText(locale,"pinned")}</h3>
            <div className="context-list">{pinned.map((row)=><Row key={row.conversation_id} selected={row.conversation_id===activeResourceId}
              onClick={()=>onOpenConversation(row.conversation_id)} title={row.title} meta={row.turn_count+" turns"} pinned/> )}</div>
          </section>}
          <section className="context-list-section context-list-grow"><h3>{uiText(locale,"recent")}</h3>
            <div className="context-list">{recent.map((row)=><div key={row.conversation_id} className="context-row-wrap">
              <Row selected={row.conversation_id===activeResourceId} onClick={()=>onOpenConversation(row.conversation_id)} title={row.title} meta={row.turn_count+" turns"}/>
              <button type="button" className="pin-toggle" aria-label={(pinnedConversationIds.includes(row.conversation_id)?"Unpin ":"Pin ")+row.title}
                onClick={()=>onTogglePin(row.conversation_id)}>☆</button>
            </div>)}</div>
          </section>
        </> : view==="documents" ? <>
          <button type="button" className="primary-button wide" onClick={onNewDocument}>New document</button>
          <section className="context-list-section context-list-grow"><h3>{uiText(locale,"recent")}</h3>
            <div className="context-list">{visibleDocuments.map((row)=><Row key={row.document_id} selected={row.document_id===activeResourceId}
              onClick={()=>onOpenDocument(row.document_id)} title={row.title} meta={"r"+row.revision}/>)}</div>
          </section>
        </> : <nav className="context-sections" aria-label={uiText(locale,route.id)+" sections"}><ul>
          {route.context.map((item)=><li key={item.target||item.action}><button type="button" onClick={()=>item.action==="new-document"?onNewDocument():jump(item.target)}>{item.label}</button></li>)}
        </ul></nav>}
      </aside>
    </div>
  );
}
