import React, { useEffect, useMemo, useState } from "react";
import { api } from "../../api.js";
import { useEdgeSwipeBack } from "../../motion.js";
import { uiText } from "../../uiPreferences.js";
import { CleartextNotice } from "../CleartextNotice.jsx";
import { Sheet } from "../Sheet.jsx";

const rows=[
  ["personalization","Personalization","Tune response style, instructions and suggestions"],
  ["appearance","Appearance","Theme, density, accent and motion"],
  ["general","General","Language and startup workspace"],
  ["notifications","Notifications","Run completion, errors and approvals"],
  ["voice","Voice","Read-aloud availability and rate"],
  ["memory","Memory","Memory state and export"],
  ["integrations","Integrations","Local tools and connected capabilities"],
  ["models","Models & retrieval","Answer provider and retrieval strategy"],
  ["security","Security","Local access and authentication"],
  ["storage","Storage","Local database and content counts"],
  ["data","Data controls","Export and retention controls"],
  ["diagnostics","Diagnostics","Runtime and offline capability report"],
  ["help","Help","Keyboard, mobile and workflow help"],
  ["about","About","Version and build information"],
];

function Row({id,title,description,onClick,value}){return <button type="button" className="settings-row" onClick={()=>onClick(id)}><span className={"settings-row-icon settings-icon-"+id} aria-hidden="true"/><span className="settings-row-copy"><strong>{title}</strong><span>{description}</span></span>{value&&<span className="settings-row-value">{value}</span>}<span aria-hidden="true">›</span></button>;}
function Toggle({label,checked,onChange}){return <label className="settings-toggle"><span>{label}</span><input type="checkbox" checked={checked} onChange={(e)=>onChange(e.target.checked)}/></label>;}

export function SettingsWorkspace({options,onOptionsChange,uiPrefs,onUIPrefsChange,announce,embedded=false,idPrefix=""}){
  const settingsHeadingId=idPrefix+"settings-title";
    const [page,setPage]=useState("home"),[models,setModels]=useState([]),[manifest,setManifest]=useState(null),[config,setConfig]=useState(null),[memory,setMemory]=useState(null),[documents,setDocuments]=useState([]),[sources,setSources]=useState([]);
  const [token,setToken]=useState(api.getOperatorToken()),[error,setError]=useState(null),[instructionsOpen,setInstructionsOpen]=useState(false),[instructions,setInstructions]=useState(uiPrefs?.personalization?.customInstructions||"");
  useEffect(()=>{Promise.all([api.models(),api.offlineManifest(),api.config(),api.memory(),api.documents(),api.sources()]).then(([m,o,c,mem,docs,src])=>{setModels(m.providers||[]);setManifest(o);setConfig(c);setMemory(mem);setDocuments(docs.documents||[]);setSources(src.sources||src||[]);}).catch(setError);},[]);
  useEffect(()=>setInstructions(uiPrefs?.personalization?.customInstructions||""),[uiPrefs?.personalization?.customInstructions]);
  const back=()=>setPage("home");
  const swipe=useEdgeSwipeBack({enabled:page!=="home",onCommit:back});
  const patch=(changes)=>onUIPrefsChange?.({...uiPrefs,...changes});
  const patchPersonal=(changes)=>patch({personalization:{...uiPrefs.personalization,...changes}});
  const title=page==="home"?"Settings":rows.find((r)=>r[0]===page)?.[1]||"Settings";
  const offlineCounts=useMemo(()=>manifest?{
    local:(manifest.capabilities||[]).filter((x)=>x.state_now==="AVAILABLE_LOCALLY").length,
    remote:(manifest.capabilities||[]).filter((x)=>x.state_now==="REQUIRES_REMOTE_SERVICE").length,
    missing:(manifest.capabilities||[]).filter((x)=>x.state_now==="LOCAL_DEPENDENCY_MISSING").length,
  }:null,[manifest]);

  function slider(label,key){return <label className="settings-slider"><span>{label}<output>{Math.round(uiPrefs.personalization[key]*100)}</output></span><input type="range" min="0" max="1" step="0.05" value={uiPrefs.personalization[key]} onChange={(e)=>patchPersonal({[key]:Number(e.target.value)})}/></label>;}
  function HomeView({ inert = false } = {}) {
    const handler = inert ? () => {} : setPage;
    return <div className="settings-groups" aria-hidden={inert || undefined}>
      <section className="settings-group">{rows.slice(0,5).map((r)=><Row key={r[0]} id={r[0]} title={r[1]} description={r[2]} onClick={handler} value={r[0]==="appearance"?uiPrefs.theme:r[0]==="general"?uiPrefs.language.toUpperCase():null}/>)}</section>
      <section className="settings-group">{rows.slice(5,10).map((r)=><Row key={r[0]} id={r[0]} title={r[1]} description={r[2]} onClick={handler}/>)}</section>
      <section className="settings-group">{rows.slice(10).map((r)=><Row key={r[0]} id={r[0]} title={r[1]} description={r[2]} onClick={handler}/>)}</section>
    </div>;
  }

  function Page(){
    if(page==="home") return <HomeView/>;
    if(page==="appearance") return <div className="settings-page-body">
      <fieldset className="settings-card"><legend>Theme</legend><div className="segmented">{["auto","light","dark"].map((x)=><button key={x} type="button" aria-pressed={uiPrefs.theme===x} onClick={()=>patch({theme:x})}>{x==="auto"?"System":x[0].toUpperCase()+x.slice(1)}</button>)}</div></fieldset>
      <fieldset className="settings-card"><legend>Density</legend><div className="segmented">{["compact","comfortable","roomy"].map((x)=><button key={x} type="button" aria-pressed={uiPrefs.density===x} onClick={()=>patch({density:x})}>{x}</button>)}</div></fieldset>
      <fieldset className="settings-card"><legend>Accent</legend><div className="segmented">{["teal","blue","violet"].map((x)=><button key={x} type="button" aria-pressed={uiPrefs.accent===x} onClick={()=>patch({accent:x})}>{x}</button>)}</div></fieldset>
      <fieldset className="settings-card"><legend>Motion</legend><div className="segmented">{["system","full","reduced"].map((x)=><button key={x} type="button" aria-pressed={uiPrefs.motion===x} onClick={()=>patch({motion:x})}>{x}</button>)}</div></fieldset>
    </div>;
    if(page==="personalization") return <div className="settings-page-body">
      <fieldset className="settings-card"><legend>Base style</legend><div className="segmented">{["balanced","concise","detailed","analytical"].map((x)=><button key={x} type="button" aria-pressed={uiPrefs.personalization.style===x} onClick={()=>patchPersonal({style:x})}>{x}</button>)}</div></fieldset>
      <div className="settings-card">{slider("Warmth","warmth")}{slider("Concision","concision")}{slider("Enthusiasm","enthusiasm")}{slider("Emoji level","emoji")}</div>
      <button type="button" className="settings-card settings-action-card" onClick={()=>setInstructionsOpen(true)}><span><strong>Custom instructions</strong><small>{uiPrefs.personalization.customInstructions?"Configured":"Not set"}</small></span><span>›</span></button>
      <div className="settings-card"><Toggle label="Quick responses" checked={uiPrefs.personalization.quickResponses} onChange={(value)=>patchPersonal({quickResponses:value})}/><Toggle label="Suggested follow-ups" checked={uiPrefs.personalization.suggestedFollowups} onChange={(value)=>patchPersonal({suggestedFollowups:value})}/></div>
    </div>;
    if(page==="general") return <div className="settings-page-body"><fieldset className="settings-card"><legend>Language</legend><div className="segmented"><button type="button" aria-pressed={uiPrefs.language==="en"} onClick={()=>patch({language:"en"})}>English</button><button type="button" aria-pressed={uiPrefs.language==="es"} onClick={()=>patch({language:"es"})}>Español</button></div></fieldset><label className="field settings-card"><span>Startup workspace</span><select value={uiPrefs.general.startRoute} onChange={(e)=>patch({general:{...uiPrefs.general,startRoute:e.target.value}})}>{["chat","sources","documents","memory","provenance","settings"].map((x)=><option key={x} value={x}>{uiText(uiPrefs.language,x)}</option>)}</select></label></div>;
    if(page==="notifications") return <div className="settings-page-body"><div className="settings-card"><Toggle label="Run completed" checked={uiPrefs.notifications.runComplete} onChange={(v)=>patch({notifications:{...uiPrefs.notifications,runComplete:v}})}/><Toggle label="Errors" checked={uiPrefs.notifications.errors} onChange={(v)=>patch({notifications:{...uiPrefs.notifications,errors:v}})}/><Toggle label="Approval required" checked={uiPrefs.notifications.approvals} onChange={(v)=>patch({notifications:{...uiPrefs.notifications,approvals:v}})}/></div></div>;
    if(page==="voice") return <div className="settings-page-body"><div className="settings-card"><Toggle label="Enable read aloud" checked={uiPrefs.voice.enabled} onChange={(v)=>patch({voice:{...uiPrefs.voice,enabled:v}})}/><label className="settings-slider"><span>Speech rate <output>{uiPrefs.voice.rate.toFixed(1)}×</output></span><input type="range" min="0.5" max="2" step="0.1" value={uiPrefs.voice.rate} onChange={(e)=>patch({voice:{...uiPrefs.voice,rate:Number(e.target.value)}})}/></label><p className="muted small">{typeof window!=="undefined"&&"speechSynthesis" in window?"This device supports speech output.":"Speech output is unavailable in this runtime."}</p></div></div>;
    if(page==="memory") return <div className="settings-page-body"><div className="settings-card"><p>{memory?.items?.length||memory?.memories?.length||0} memory records visible to this client.</p><button type="button" className="secondary-button" onClick={async()=>{const data=await api.exportMemory();await navigator.clipboard?.writeText(JSON.stringify(data,null,2));announce?.("Memory export copied.");}}>Copy memory export</button></div></div>;
    if(page==="integrations") return <div className="settings-page-body"><div className="settings-card"><p>Local integrations are capability-backed rather than provider-branded.</p>{(manifest?.capabilities||[]).slice(0,20).map((x)=><div key={x.capability} className="settings-capability"><strong>{x.capability}</strong><span>{x.state_now}</span></div>)}</div></div>;
    if(page==="models") return <div className="settings-page-body"><fieldset className="settings-card"><legend>Answer model</legend>{models.map((model)=><label key={model.name} className="option"><input type="radio" name="model" checked={(options.model_provider||"mock")===model.name} onChange={()=>onOptionsChange({...options,model_provider:model.name,allow_live:model.locality==="remote"?options.allow_live:false})}/><span><strong>{model.name}</strong><small className="block muted">{model.description}</small></span></label>)}<Toggle label="Allow remote model when selected" checked={Boolean(options.allow_live)} onChange={(v)=>onOptionsChange({...options,allow_live:v})}/></fieldset><label className="field settings-card"><span>Retrieval strategy</span><select value={options.retrieval_strategy} onChange={(e)=>onOptionsChange({...options,retrieval_strategy:e.target.value})}>{["hybrid","bm25","persistent","semantic","lexical","vector"].map((x)=><option key={x}>{x}</option>)}</select></label></div>;
    if(page==="security") return <div className="settings-page-body"><div className="settings-card"><CleartextNotice/>{api.tokenIsLaunchManaged()?<p className="muted small">This app launch manages its token automatically.</p>:<form onSubmit={(e)=>{e.preventDefault();api.setOperatorToken(token,{remember:true});announce?.("Token saved for this device.");}}><label className="field"><span>Local access token</span><input type="password" value={token} autoComplete="off" onChange={(e)=>setToken(e.target.value)}/></label><button type="submit" className="primary-button">Save token</button></form>}<p className="muted small">{config?.auth?.auth_required?"Authentication is required.":"Authentication is not required for this local service."}</p></div></div>;
    if(page==="storage") return <div className="settings-page-body"><div className="settings-card"><dl className="settings-dl"><div><dt>Sources</dt><dd>{sources.length}</dd></div><div><dt>Documents</dt><dd>{documents.length}</dd></div><div><dt>Data directory</dt><dd>{config?.data_dir||"Hidden on remote clients"}</dd></div></dl></div></div>;
    if(page==="data") return <div className="settings-page-body"><div className="settings-card"><p>Mucaro stores source, document and provenance records locally. Destructive deletion remains within the owning workspace and retains its explicit confirmation gate.</p><button type="button" className="secondary-button" onClick={async()=>{const data=await api.exportMemory();const blob=new Blob([JSON.stringify(data,null,2)],{type:"application/json"});const url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download="mucaro-memory-export.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}}>Export memory JSON</button></div></div>;
    if(page==="diagnostics") return <div className="settings-page-body"><div className="settings-card"><dl className="settings-dl"><div><dt>Offline local</dt><dd>{offlineCounts?.local??"…"}</dd></div><div><dt>Needs network</dt><dd>{offlineCounts?.remote??"…"}</dd></div><div><dt>Missing dependency</dt><dd>{offlineCounts?.missing??"…"}</dd></div><div><dt>User agent</dt><dd>{navigator.userAgent}</dd></div></dl></div></div>;
    if(page==="help") return <div className="settings-page-body"><div className="settings-card"><h3>Navigation</h3><p>Use the rail on desktop or the menu drawer on a phone. Swipe from the left edge to return from a settings subpage.</p><h3>Documents</h3><p>Documents support tabs, comments, checklists, revisions, sharing and direct editing.</p></div></div>;
    return <div className="settings-page-body"><div className="settings-card"><p><strong>Mucaro AI</strong></p><p className="muted">Local-first research workspace.</p><p>Frontend build version 0.1.23</p></div></div>;
  }

  const swiping=page!=="home"&&swipe.progress>0;
  return <section className={"workspace settings-stack settings-page-"+page+(swiping?" settings-swiping":"")} aria-labelledby={settingsHeadingId} {...swipe.bind}>
    {page!=="home"&&<div className="settings-backdrop-page" inert={true}
      style={{transform:"translate3d("+(-24+swipe.progress*24)+"%,0,0)",opacity:0.72+swipe.progress*0.28}}><HomeView inert/></div>}
    <div className="settings-foreground-page"
      style={page!=="home"?{transform:"translate3d("+(swipe.progress*100)+"%,0,0)",boxShadow:swipe.progress?"-12px 0 28px rgba(0,0,0,"+(0.16*(1-swipe.progress))+")":undefined}:undefined}>
      {(!embedded||page!=="home")&&<header className="settings-stack-header">{page!=="home"&&<button type="button" className="icon-button" aria-label="Back to Settings" onClick={back}>‹</button>}<h1 id={settingsHeadingId}>{title}</h1></header>}
      {error&&<p className="error-card" role="alert">{error.status||"ERROR"}: {error.message}</p>}
      <Page/>
    </div>
    {instructionsOpen&&<Sheet title="Custom instructions" side="bottom" size="large" onClose={()=>{setInstructions(uiPrefs.personalization.customInstructions);setInstructionsOpen(false);}}><label className="custom-instructions-editor"><span className="sr-only">Custom instructions</span><textarea data-autofocus value={instructions} maxLength={12000} onChange={(e)=>setInstructions(e.target.value)}/><span className="character-count">{instructions.length.toLocaleString()} / 12,000</span></label><div className="button-row"><button type="button" className="secondary-button" onClick={()=>setInstructionsOpen(false)}>Cancel</button><button type="button" className="primary-button" onClick={()=>{patchPersonal({customInstructions:instructions});setInstructionsOpen(false);announce?.("Custom instructions saved.");}}>Save</button></div></Sheet>}
  </section>;
}
