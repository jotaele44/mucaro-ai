import React, { useState } from "react";
import { ROUTES } from "../navigation.js";
import { uiText } from "../uiPreferences.js";
import { Sheet } from "./Sheet.jsx";

export function MobileWorkspacePicker({
  tabs, activeKey, conversations, documents, locale="en",
  onSelect, onCloseTab, onNewChat, onNewDocument,
}) {
  const [open,setOpen]=useState(false);
  const active=tabs.find((tab)=>tab.key===activeKey)||tabs[0];
  function title(tab){
    if(tab.view==="chat"){
      if(!tab.resourceId)return uiText(locale,"newChat");
      return conversations.find((row)=>row.conversation_id===tab.resourceId)?.title||"Conversation";
    }
    if(tab.view==="documents"){
      if(!tab.resourceId)return uiText(locale,"documents");
      return documents.find((row)=>row.document_id===tab.resourceId)?.title||"Document";
    }
    return uiText(locale,tab.view);
  }
  return <>
    <button type="button" className="mobile-workspace-picker-button" aria-haspopup="dialog" aria-expanded={open} onClick={()=>setOpen(true)}>
      <span>{title(active)}</span><span aria-hidden="true">⌄</span>
    </button>
    {open&&<Sheet title="Open workspaces" side="bottom" size="medium" onClose={()=>setOpen(false)}>
      <div className="mobile-workspace-list">
        {tabs.map((tab)=><div key={tab.mountId} className="mobile-workspace-row">
          <button type="button" className="mobile-workspace-select" aria-current={tab.key===activeKey?"true":undefined}
            onClick={()=>{onSelect(tab.key);setOpen(false);}}>
            <span className="mobile-workspace-kind">{ROUTES.find((row)=>row.id===tab.view)?.label||tab.view}</span>
            <strong>{title(tab)}</strong>
          </button>
          <button type="button" className="mobile-workspace-close" aria-label={"Close "+title(tab)} onClick={()=>onCloseTab(tab.key)}>×</button>
        </div>)}
      </div>
      <div className="mobile-workspace-new">
        <button type="button" className="secondary-button" onClick={()=>{onNewChat();setOpen(false);}}>＋ {uiText(locale,"newChat")}</button>
        <button type="button" className="secondary-button" onClick={()=>{onNewDocument();setOpen(false);}}>＋ New document</button>
      </div>
    </Sheet>}
  </>;
}
