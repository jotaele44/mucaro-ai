import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, authIssueOf, onAuthIssue } from "./api.js";
import { ChatView } from "./components/ChatView.jsx";
import { NavigationShell } from "./components/NavigationShell.jsx";
import { MobileWorkspacePicker } from "./components/MobileWorkspacePicker.jsx";
import { WorkspaceTabs } from "./components/WorkspaceTabs.jsx";
import { TasksDrawer } from "./components/TasksDrawer.jsx";
import { TokenGate } from "./components/TokenGate.jsx";
import { DocumentsWorkspace } from "./components/workspaces/DocumentsWorkspace.jsx";
import { MemoryWorkspace } from "./components/workspaces/MemoryWorkspace.jsx";
import { ProvenanceWorkspace } from "./components/workspaces/ProvenanceWorkspace.jsx";
import { SettingsWorkspace } from "./components/workspaces/SettingsWorkspace.jsx";
import { SourcesWorkspace } from "./components/workspaces/SourcesWorkspace.jsx";
import { addTab, adoptResource, makeTab, removeTab, restoreTabs } from "./navigation.js";
import { effectiveReducedMotion, effectiveTheme, normalizeUIPrefs, readUIPrefs, uiText, writeUIPrefs } from "./uiPreferences.js";

const DEFAULT_OPTIONS={model_provider:"mock",retrieval_strategy:"hybrid",allow_live:false,top_k:5};
const HEALTH_POLL_MS=15000,RESUME_AFTER_AWAY_MS=3000;
const MOBILE_NAV_QUERY="(max-width: 760px), (max-height: 550px) and (pointer: coarse)";
const stored=(key,fallback)=>{try{const raw=localStorage.getItem(key);return raw?JSON.parse(raw):fallback;}catch{return fallback;}};
const persist=(key,value)=>{try{localStorage.setItem(key,JSON.stringify(value));}catch{}};
const locked=(current,reason)=>current.state==="locked"&&current.reason===reason?current:{state:"locked",reason};

export function App(){
  const [uiPrefs,setUiPrefs]=useState(readUIPrefs);
  const [systemDark,setSystemDark]=useState(()=>matchMedia("(prefers-color-scheme: dark)").matches);
  const [systemReduced,setSystemReduced]=useState(()=>matchMedia("(prefers-reduced-motion: reduce)").matches);
  const [tabs,setTabs]=useState(()=>restoreTabs(stored("mucaro.nav.tabs.v2",null),stored("mucaro.activeConversation",null)));
  const [activeTabKey,setActiveTabKey]=useState(()=>{const saved=stored("mucaro.nav.active.v2",null);return tabs.some((t)=>t.key===saved)?saved:tabs[0].key;});
  const [contextOpen,setContextOpen]=useState(()=>stored("mucaro.nav.contextOpen.v2",true)!==false);
  const [conversations,setConversations]=useState([]),[documents,setDocuments]=useState([]),[filter,setFilter]=useState("");
  const [pinned,setPinned]=useState(()=>stored("mucaro.nav.pinnedConversations.v1",[]));
  const [navOpen,setNavOpen]=useState(false),[mobileOverlay,setMobileOverlay]=useState(()=>matchMedia(MOBILE_NAV_QUERY).matches),[tasksOpen,setTasksOpen]=useState(false);
  const [modes,setModes]=useState(null),[options,setOptions]=useState(()=>({...DEFAULT_OPTIONS,...stored("mucaro.options",{})})),[message,setMessage]=useState("");
  const [health,setHealth]=useState({state:"checking"}),[gateBusy,setGateBusy]=useState(false),[reconnect,setReconnect]=useState(0);
  const healthRef=useRef(health),polling=useRef(false),offline=useRef(false),tabsRef=useRef(tabs),selectedRef=useRef(activeTabKey);
  healthRef.current=health;tabsRef.current=tabs;selectedRef.current=activeTabKey;

  const announce=useCallback((text)=>{setMessage("");setTimeout(()=>setMessage(text),30);},[]);
  const loadConversations=useCallback(async()=>{try{const p=await api.conversations();setConversations(p.conversations||[]);}catch{}},[]);
  const loadDocuments=useCallback(async(next)=>{if(Array.isArray(next)){setDocuments(next);return;}try{const p=await api.documents();setDocuments(p.documents||[]);}catch{}},[]);

  const checkHealth=useCallback(async()=>{
    try{
      await api.health();
      const [modePayload]=await Promise.all([api.modes(),loadConversations(),loadDocuments()]);
      setModes(modePayload);offline.current=false;setHealth((cur)=>cur.state==="ok"&&!cur.offline?cur:{state:"ok"});
    }catch(err){const issue=authIssueOf(err);setHealth((cur)=>issue?locked(cur,issue):{state:"error",error:err});}
  },[loadConversations,loadDocuments]);
  useEffect(()=>{checkHealth();},[checkHealth]);
  useEffect(()=>onAuthIssue((reason)=>setHealth((cur)=>locked(cur,reason))),[]);
  async function submitToken(token,remember){api.setOperatorToken(token,{remember});setGateBusy(true);try{await checkHealth();}finally{setGateBusy(false);}}
  const pollHealth=useCallback(async()=>{if(polling.current)return;polling.current=true;try{await api.health();if(offline.current){offline.current=false;setHealth((cur)=>cur.state==="ok"?{state:"ok"}:cur);loadConversations();loadDocuments();setReconnect((n)=>n+1);}}catch{offline.current=true;setHealth((cur)=>cur.state==="ok"&&!cur.offline?{state:"ok",offline:true}:cur);}finally{polling.current=false;}},[loadConversations,loadDocuments]);
  useEffect(()=>{
    const tick=()=>{if(document.hidden)return;if(healthRef.current.state==="error")checkHealth();else if(healthRef.current.state==="ok")pollHealth();};
    const timer=setInterval(tick,HEALTH_POLL_MS);let away=null;
    const visible=()=>{if(document.hidden){away=Date.now();return;}const long=away!==null&&Date.now()-away>RESUME_AFTER_AWAY_MS;away=null;if(long)setReconnect((n)=>n+1);tick();};
    const online=()=>{setReconnect((n)=>n+1);tick();};const show=(e)=>{if(e.persisted){setReconnect((n)=>n+1);tick();}};
    document.addEventListener("visibilitychange",visible);window.addEventListener("online",online);window.addEventListener("pageshow",show);
    return()=>{clearInterval(timer);document.removeEventListener("visibilitychange",visible);window.removeEventListener("online",online);window.removeEventListener("pageshow",show);};
  },[checkHealth,pollHealth]);
  async function retry(){await checkHealth();setReconnect((n)=>n+1);}

  useEffect(()=>persist("mucaro.options",options),[options]);
  useEffect(()=>persist("mucaro.nav.tabs.v2",tabs),[tabs]);
  useEffect(()=>persist("mucaro.nav.active.v2",activeTabKey),[activeTabKey]);
  useEffect(()=>persist("mucaro.nav.contextOpen.v2",contextOpen),[contextOpen]);
  useEffect(()=>persist("mucaro.nav.pinnedConversations.v1",pinned),[pinned]);
  useEffect(()=>{
    writeUIPrefs(uiPrefs);document.documentElement.lang=uiPrefs.language;
    document.documentElement.dataset.mucaroTheme=effectiveTheme(uiPrefs.theme,systemDark);
    document.documentElement.dataset.mucaroDensity=uiPrefs.density;
    document.documentElement.dataset.mucaroAccent=uiPrefs.accent;
    document.documentElement.dataset.mucaroMotion=effectiveReducedMotion(uiPrefs,systemReduced)?"reduced":"full";
  },[uiPrefs,systemDark,systemReduced]);
  useEffect(()=>{
    const dark=matchMedia("(prefers-color-scheme: dark)"),motion=matchMedia("(prefers-reduced-motion: reduce)");
    const d=()=>setSystemDark(dark.matches),m=()=>setSystemReduced(motion.matches);dark.addEventListener("change",d);motion.addEventListener("change",m);
    return()=>{dark.removeEventListener("change",d);motion.removeEventListener("change",m);};
  },[]);
  useEffect(()=>{
    const query=matchMedia(MOBILE_NAV_QUERY);const sync=()=>{setMobileOverlay(query.matches);if(!query.matches)setNavOpen(false);};query.addEventListener("change",sync);return()=>query.removeEventListener("change",sync);
  },[]);

  const active=tabs.find((t)=>t.key===activeTabKey)||tabs[0],view=active.view,resourceId=active.resourceId;
  function commitNav(next,key){tabsRef.current=next;selectedRef.current=key;setTabs(next);setActiveTabKey(key);}
  function openTab(tab){const out=addTab(tabsRef.current,tab);if(out.rejected){announce("Too many tabs are open. Close one first.");return;}commitNav(out.tabs,out.key);setNavOpen(false);}
  function selectKey(key){if(!tabsRef.current.some((t)=>t.key===key))return;selectedRef.current=key;setActiveTabKey(key);setNavOpen(false);}
  function selectRoute(route){if(route==="chat"){const last=tabsRef.current.slice().reverse().find((t)=>t.view==="chat");openTab(last||makeTab("chat"));}else if(route==="documents"){const last=tabsRef.current.slice().reverse().find((t)=>t.view==="documents");openTab(last||makeTab("documents"));}else openTab(makeTab(route));}
  function openConversation(id){openTab(makeTab("chat",id));}
  function openDocument(id){openTab(makeTab("documents",id));}
  async function newDocument(){const created=await api.createDocument({title:"Untitled document"});await loadDocuments();openDocument(created.document_id);}
  function closeTab(key){const out=removeTab(tabsRef.current,key,selectedRef.current);commitNav(out.tabs,out.key);}
  function adopt(tabKey,viewType,id){
    if(id==null){const out=removeTab(tabsRef.current,tabKey,selectedRef.current);commitNav(out.tabs,out.key);return;}
    const out=adoptResource(tabsRef.current,tabKey,viewType,id);if(!out)return;commitNav(out.tabs,selectedRef.current===tabKey?out.key:selectedRef.current);
  }
  function togglePin(id){setPinned((rows)=>rows.includes(id)?rows.filter((x)=>x!==id):[id,...rows].slice(0,20));}

  return <div className={"app-shell "+(navOpen?"nav-open ":"")+(contextOpen?"context-open":"context-closed")}>
    <a className="skip-link" href="#main">Skip to content</a>
    <NavigationShell view={view} conversations={conversations} documents={documents} activeResourceId={resourceId} filter={filter} setFilter={setFilter}
      pinnedConversationIds={pinned} onTogglePin={togglePin} onSelectRoute={selectRoute} onOpenConversation={openConversation} onOpenDocument={openDocument}
      onNewDocument={newDocument} onTasks={()=>{setTasksOpen(true);setNavOpen(false);}} contextOpen={contextOpen} locale={uiPrefs.language} inactive={mobileOverlay&&!navOpen}/>
    <button type="button" className="context-edge-handle" aria-label={contextOpen?"Collapse contextual sidebar":"Expand contextual sidebar"} aria-expanded={contextOpen} onClick={()=>setContextOpen((x)=>!x)}>☰</button>
    <div className="nav-scrim" onClick={()=>setNavOpen(false)} aria-hidden="true"/>
    <main id="main" className="main" tabIndex={-1} inert={mobileOverlay&&navOpen}>
      <div className="mobile-bar"><button type="button" className="icon-button" aria-controls="navigation-shell" aria-expanded={navOpen} aria-label="Open navigation" onClick={()=>setNavOpen(true)}>☰</button>
        <MobileWorkspacePicker tabs={tabs} activeKey={activeTabKey} conversations={conversations} documents={documents} locale={uiPrefs.language}
          onSelect={selectKey} onCloseTab={closeTab} onNewChat={()=>openConversation(null)} onNewDocument={newDocument}/>
      </div>
      {(health.state==="error"||health.offline)&&<div className="error-card banner" role="alert"><strong>Mucaro's local service is not reachable.</strong> {health.offline?"Reconnecting…":"Your data stays on this computer."}<button type="button" className="secondary-button" onClick={retry}>Retry</button></div>}
      {health.state==="checking"&&<p className="muted center pad">Starting…</p>}
      {health.state==="locked"&&<TokenGate reason={health.reason} busy={gateBusy} onSubmit={submitToken} onRetry={retry}/>}
      {health.state==="ok"&&<>
        <WorkspaceTabs tabs={tabs} activeKey={activeTabKey} conversations={conversations} documents={documents} onSelect={selectKey} onClose={closeTab} locale={uiPrefs.language}/>
        <div className="workspace-panels">{tabs.map((tab)=><div key={tab.mountId} id={"panel-"+tab.mountId} className="workspace-pane" role="region" aria-labelledby={"tab-"+tab.mountId} hidden={tab.key!==activeTabKey}>
          {tab.view==="chat"&&<ChatView reconnect={reconnect} conversationId={tab.resourceId} onConversationCreated={(id)=>adopt(tab.key,"chat",id)} onConversationsChanged={loadConversations} onOpenTasks={()=>setTasksOpen(true)} announce={announce} modes={modes} defaultOptions={options} onOpenDocument={async(title,text,originRef)=>{const doc=await api.createDocument({title:title||"Mucaro answer",origin_type:"answer",origin_ref:originRef||null,initial_text:text||""});await loadDocuments();openDocument(doc.document_id);}}/>}
          {tab.view==="sources"&&<SourcesWorkspace announce={announce}/>}
          {tab.view==="documents"&&<DocumentsWorkspace documentId={tab.resourceId} onDocumentCreated={(id)=>adopt(tab.key,"documents",id)} onDocumentsChanged={loadDocuments} onCloseDocument={()=>adopt(tab.key,"documents",null)} announce={announce}/>}
          {tab.view==="memory"&&<MemoryWorkspace announce={announce}/>}
          {tab.view==="provenance"&&<ProvenanceWorkspace/>}
          {tab.view==="settings"&&<SettingsWorkspace options={options} onOptionsChange={setOptions} uiPrefs={uiPrefs} onUIPrefsChange={(next)=>setUiPrefs(normalizeUIPrefs(next))} announce={announce}/>}
        </div>)}</div>
      </>}
    </main>
    {tasksOpen&&<TasksDrawer onClose={()=>setTasksOpen(false)}/>}
    <div className="sr-only" role="status" aria-live="polite">{message}</div>
  </div>;
}
