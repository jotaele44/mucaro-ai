import React, { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api.js";
import { saveFile, shareFile, shareMessage } from "../../files.js";
import { Sheet } from "../Sheet.jsx";
import { RichBlockEditor } from "../RichBlockEditor.jsx";

const uid=(p)=>p+"_"+(globalThis.crypto?.randomUUID?.()||Math.random().toString(16).slice(2));
const clone=(v)=>JSON.parse(JSON.stringify(v));

function MarkedText({text,marks=[]}){
  const value=String(text||""),bounds=new Set([0,value.length]);
  for(const mark of marks){bounds.add(Math.max(0,Math.min(value.length,mark.start)));bounds.add(Math.max(0,Math.min(value.length,mark.end)));}
  const points=[...bounds].sort((a,b)=>a-b),parts=[];
  for(let i=0;i<points.length-1;i++){
    const start=points[i],end=points[i+1];if(end<=start)continue;
    let node=value.slice(start,end);
    const active=marks.filter((m)=>m.start<=start&&m.end>=end).map((m)=>m.type);
    if(active.includes("strike"))node=<s>{node}</s>;
    if(active.includes("underline"))node=<u>{node}</u>;
    if(active.includes("italic"))node=<em>{node}</em>;
    if(active.includes("bold"))node=<strong>{node}</strong>;
    parts.push(<React.Fragment key={start+"-"+end}>{node}</React.Fragment>);
  }
  return <>{parts}</>;
}

function ViewBlock({block,selected,onSelect,onChecklist,collapsed,onToggle}){
  const cls="document-block document-block-"+block.type+(selected?" selected":"");
  if(block.type==="divider") return <hr className={cls}/>;
  if(block.type==="image") return <figure className={cls} onClick={onSelect}><img src={block.src} alt={block.caption||""}/>{block.caption&&<figcaption>{block.caption}</figcaption>}</figure>;
  if(block.type==="heading"){const Tag="h"+(block.level||2);return <Tag className={cls} onClick={onSelect}><MarkedText text={block.text} marks={block.marks||[]}/></Tag>;}
  if(block.type==="title") return <h1 className={cls} onClick={onSelect}><MarkedText text={block.text} marks={block.marks||[]}/></h1>;
  if(block.type==="code") return <pre className={cls} onClick={onSelect}><code>{block.text}</code></pre>;
  if(block.type==="quote") return <blockquote className={cls} onClick={onSelect}><MarkedText text={block.text} marks={block.marks||[]}/></blockquote>;
  if(block.type==="bullets"||block.type==="ordered"){const Tag=block.type==="ordered"?"ol":"ul";return <Tag className={cls} onClick={onSelect}>{(block.items||[]).map((x,i)=><li key={i}><MarkedText text={x}/></li>)}</Tag>;}
  if(block.type==="checklist") return <ul className={cls+" checklist-block"}>{(block.items||[]).map((item)=><li key={item.item_id}><label><input type="checkbox" checked={item.checked} onChange={()=>onChecklist(item.item_id)}/><span className={item.checked?"checked":""}><MarkedText text={item.text}/></span></label></li>)}</ul>;
  if(block.type==="section") return <section className={cls}><button type="button" className="section-toggle" aria-expanded={!collapsed} onClick={onToggle}><span>{block.title}</span><span aria-hidden="true">{collapsed?"›":"⌄"}</span></button></section>;
  return <p className={cls} onClick={onSelect}><MarkedText text={block.text} marks={block.marks||[]}/></p>;
}

function markdown(doc){
  const fence=String.fromCharCode(96,96,96);
  const lines=["# "+doc.title,""];
  for(const tab of doc.tabs){
    if(doc.tabs.length>1) lines.push("## "+tab.title,"");
    for(const b of tab.blocks){
      if(b.type==="title") lines.push("# "+b.text,"");
      else if(b.type==="heading") lines.push("#".repeat(b.level||2)+" "+b.text,"");
      else if(b.type==="paragraph") lines.push(b.text,"");
      else if(b.type==="code") lines.push(fence,b.text,fence,"");
      else if(b.type==="quote") lines.push(...String(b.text).split("\n").map((x)=>"> "+x),"");
      else if(b.type==="bullets") lines.push(...(b.items||[]).map((x)=>"- "+x),"");
      else if(b.type==="ordered") lines.push(...(b.items||[]).map((x,i)=>(i+1)+". "+x),"");
      else if(b.type==="checklist") lines.push(...(b.items||[]).map((x)=>"- ["+(x.checked?"x":" ")+"] "+x.text),"");
      else if(b.type==="image") lines.push("!["+(b.caption||"image")+"]("+b.src+")","");
      else if(b.type==="section") lines.push("### "+b.title,"");
    }
  }
  return lines.join("\n").trim()+"\n";
}

function Editor({block,onChange,onNode}){
  const plainRef=useRef(null);
  useEffect(()=>{if(plainRef.current)onNode?.(plainRef.current);},[onNode,block.block_id]);
  if(["paragraph","title","heading","quote"].includes(block.type)){
    return <RichBlockEditor ref={onNode} block={block} onChange={onChange} className="document-rich-editor"/>;
  }
  if(["bullets","ordered"].includes(block.type)) return <textarea ref={plainRef} className="document-text-editor" value={(block.items||[]).join("\n")} onChange={(e)=>onChange({...block,items:e.target.value.split("\n")})}/>;
  if(block.type==="checklist") return <div className="checklist-editor">{(block.items||[]).map((item,i)=><div key={item.item_id} className="checklist-editor-row"><input type="checkbox" checked={item.checked} onChange={(e)=>{const items=clone(block.items);items[i].checked=e.target.checked;onChange({...block,items});}}/><input value={item.text} onChange={(e)=>{const items=clone(block.items);items[i].text=e.target.value;onChange({...block,items});}}/></div>)}</div>;
  return <textarea ref={plainRef} id={"document-block-editor-"+block.block_id} className="document-text-editor" value={block.text||""} onChange={(e)=>onChange({...block,text:e.target.value})} rows={Math.min(16,Math.max(3,String(block.text||"").split("\n").length+1))}/>;
}

export function DocumentsWorkspace({documentId,onDocumentCreated,onDocumentsChanged,onCloseDocument,announce}){
  const [list,setList]=useState([]),[record,setRecord]=useState(null),[draft,setDraft]=useState(null),[comments,setComments]=useState([]);
  const [loading,setLoading]=useState(true),[error,setError]=useState(null),[activeTab,setActiveTab]=useState(null),[editing,setEditing]=useState(false);
  const [selectedBlock,setSelectedBlock]=useState(null),[collapsed,setCollapsed]=useState(()=>new Set()),[tabSheet,setTabSheet]=useState(false),[commentSheet,setCommentSheet]=useState(false);
  const [commentText,setCommentText]=useState(""),[undo,setUndo]=useState([]),[redo,setRedo]=useState([]);
  const editorNode=useRef(null);

  async function loadList(){const p=await api.documents();setList(p.documents||[]);onDocumentsChanged?.(p.documents||[]);}
  async function loadDoc(value){if(!value){setRecord(null);setDraft(null);return;}const p=await api.document(value);setRecord(p);setDraft(clone(p.document));setComments(p.comments||[]);setActiveTab((cur)=>p.document.tabs.some((t)=>t.tab_id===cur)?cur:p.document.tabs[0]?.tab_id||null);setUndo([]);setRedo([]);}
  useEffect(()=>{setLoading(true);setError(null);Promise.all([loadList(),loadDoc(documentId)]).catch(setError).finally(()=>setLoading(false));},[documentId]);

  const tab=useMemo(()=>draft?.tabs.find((r)=>r.tab_id===activeTab)||draft?.tabs[0]||null,[draft,activeTab]);
  const selected=useMemo(()=>tab?.blocks.find((r)=>r.block_id===selectedBlock)||null,[tab,selectedBlock]);

  function mutate(fn){setDraft((cur)=>{if(!cur)return cur;const next=clone(cur);fn(next);setUndo((rows)=>[...rows.slice(-49),clone(cur)]);setRedo([]);return next;});}
  function replaceBlock(block){mutate((next)=>{const t=next.tabs.find((r)=>r.tab_id===activeTab),i=t.blocks.findIndex((r)=>r.block_id===block.block_id);if(i>=0)t.blocks[i]=block;});}
  function undoOne(){if(!undo.length||!draft)return;const prev=undo[undo.length-1];setUndo(undo.slice(0,-1));setRedo((r)=>[...r,clone(draft)]);setDraft(clone(prev));}
  function redoOne(){if(!redo.length||!draft)return;const next=redo[redo.length-1];setRedo(redo.slice(0,-1));setUndo((r)=>[...r,clone(draft)]);setDraft(clone(next));}
  async function saveDoc(next=draft){if(!record||!next)return null;const saved=await api.saveDocument(record.document_id,next,record.revision);setRecord(saved);setDraft(clone(saved.document));setComments(saved.comments||comments);setUndo([]);setRedo([]);onDocumentsChanged?.();announce?.("Document saved.");return saved;}
  async function toggleChecklist(blockId,itemId){const next=clone(draft),t=next.tabs.find((r)=>r.tab_id===activeTab),b=t.blocks.find((r)=>r.block_id===blockId),item=b.items.find((x)=>x.item_id===itemId);item.checked=!item.checked;setUndo((r)=>[...r.slice(-49),clone(draft)]);setRedo([]);setDraft(next);if(!editing)await saveDoc(next);}
  function addBlock(type){
    const b={block_id:uid("blk"),type};
    if(["paragraph","title","heading","code","quote"].includes(type)){b.text="";b.marks=[];}
    if(type==="heading")b.level=2;
    if(["bullets","ordered"].includes(type))b.items=[""];
    if(type==="checklist")b.items=[{item_id:uid("item"),text:"",checked:false}];
    if(type==="section"){
      const child={block_id:uid("blk"),type:"paragraph",text:"",marks:[]};
      b.title="Section";b.children=[child.block_id];
      mutate((next)=>next.tabs.find((r)=>r.tab_id===activeTab).blocks.push(b,child));
      setSelectedBlock(child.block_id);setEditing(true);return;
    }
    mutate((next)=>next.tabs.find((r)=>r.tab_id===activeTab).blocks.push(b));setSelectedBlock(b.block_id);setEditing(true);
  }
  function format(kind){
    const editor=editorNode.current;
    if(!selected)return;
    if(kind==="quote"){replaceBlock({...selected,type:"quote"});return;}
    if(editor&&typeof editor[kind]==="function"){editor[kind]();return;}
    if(kind==="emoji"&&editor?.insertText){editor.insertText("🙂");return;}
    if(kind==="indent"&&editor?.indent){editor.indent();return;}
    if(kind==="outdent"&&editor?.outdent){editor.outdent();return;}
    if(editor instanceof HTMLTextAreaElement){
      const start=editor.selectionStart||0,end=editor.selectionEnd||start,text=editor.value;
      const inserted=kind==="emoji"?"🙂":kind==="indent"?"  ":"";
      if(inserted){
        editor.setRangeText(inserted,start,end,"end");
        editor.dispatchEvent(new Event("input",{bubbles:true}));
      }
    }
  }
  async function addComment(){if(!record||!tab||!selectedBlock||!commentText.trim())return;const c=await api.addDocumentComment(record.document_id,{tab_id:tab.tab_id,block_id:selectedBlock,text:commentText.trim()});setComments((r)=>[...r,c]);setCommentText("");announce?.("Comment added.");}
  async function resolveComment(commentId){const c=await api.updateDocumentComment(commentId,"RESOLVED");setComments((r)=>r.map((x)=>x.comment_id===commentId?c:x));}

  if(loading)return <section className="workspace document-loading-shell"><div className="document-toolbar skeleton-toolbar"/><div className="document-spinner" aria-label="Loading document"/></section>;
  if(error&&!record)return <section className="workspace"><p className="error-card" role="alert">{error.status||"ERROR"}: {error.message}</p></section>;
  if(!documentId||!record)return <section className="workspace" aria-labelledby="documents-title"><header className="workspace-header"><h1 id="documents-title">Documents</h1><button type="button" className="primary-button" onClick={async()=>{const created=await api.createDocument({title:"Untitled document"});await loadList();onDocumentCreated?.(created.document_id);}}>New document</button></header><div id="documents-list" className="document-library">{list.map((row)=><button key={row.document_id} type="button" className="document-card" onClick={()=>onDocumentCreated?.(row.document_id)}><strong>{row.title}</strong><span className="muted small">Revision {row.revision}</span></button>)}{!list.length&&<p className="muted">No documents yet.</p>}</div></section>;

  const activeComments=comments.filter((r)=>r.state==="ACTIVE");
  const hiddenChildren=new Set();
  for(const row of tab?.blocks||[]) if(row.type==="section"&&collapsed.has(row.block_id)) for(const child of row.children||[]) hiddenChildren.add(child);
  return <section className={"workspace document-workspace "+(editing?"document-editing":"")} aria-labelledby="document-title">
    <header className="document-toolbar"><div><button type="button" className="icon-button" aria-label="Close document" onClick={onCloseDocument}>✕</button><button type="button" className="icon-button" aria-label="Undo" disabled={!undo.length} onClick={undoOne}>↶</button><button type="button" className="icon-button" aria-label="Redo" disabled={!redo.length} onClick={redoOne}>↷</button></div><div><button type="button" className="icon-button" aria-label={"Comments "+activeComments.length} onClick={()=>setCommentSheet(true)}>◌</button><button type="button" className="icon-button" aria-label="Share document" onClick={async()=>{const text=markdown(draft),name=(draft.title||"mucaro-document").replace(/[^a-z0-9]+/gi,"-").toLowerCase()+".md",result=await shareFile(new Blob([text],{type:"text/markdown;charset=utf-8"}),name,{title:draft.title});announce?.(shareMessage(result,name));}}>↥</button><button type="button" className="icon-button" aria-label="Save document file" onClick={()=>saveFile(new Blob([markdown(draft)],{type:"text/markdown;charset=utf-8"}),(draft.title||"document")+".md")}>⇩</button>{editing?<button type="button" className="done-button" onClick={async()=>{await saveDoc();setEditing(false);}}>✓</button>:<button type="button" className="secondary-button" onClick={()=>setEditing(true)}>Edit</button>}</div></header>
    <div className="document-tabbar"><button type="button" className="document-tab-selector" onClick={()=>setTabSheet(true)}>{tab?.title||"Tab"} ⌄</button></div>
    <div className="document-scroll"><div className="document-meta"><span>{new Date(record.updated_at).toLocaleDateString()}</span><span>Revision {record.revision}</span><span>{draft.metadata?.origin_type||"manual"}</span></div><input id="document-title" className="document-title-input" value={draft.title} readOnly={!editing} onChange={(e)=>mutate((n)=>{n.title=e.target.value;})}/><div className="document-blocks">{(tab?.blocks||[]).map((row)=>hiddenChildren.has(row.block_id)?null:editing&&selectedBlock===row.block_id?<div key={row.block_id} className="document-block-editor-wrap"><Editor block={row} onChange={replaceBlock} onNode={(node)=>{editorNode.current=node;}}/></div>:<ViewBlock key={row.block_id} block={row} selected={selectedBlock===row.block_id} collapsed={collapsed.has(row.block_id)} onSelect={()=>{setSelectedBlock(row.block_id);if(editing)requestAnimationFrame(()=>editorNode.current?.focus?.());}} onToggle={()=>setCollapsed((cur)=>{const n=new Set(cur);n.has(row.block_id)?n.delete(row.block_id):n.add(row.block_id);return n;})} onChecklist={(item)=>toggleChecklist(row.block_id,item)}/>)}</div>
      {editing&&<div className="document-add-row">{["paragraph","heading","bullets","ordered","checklist","quote","code","section","divider"].map((kind)=><button key={kind} type="button" className="ghost-button" onClick={()=>addBlock(kind)}>+ {kind}</button>)}<label className="ghost-button">+ image<input type="file" accept="image/png,image/jpeg,image/webp,image/gif" hidden onChange={async(e)=>{const file=e.target.files?.[0];e.currentTarget.value="";if(!file||!record)return;try{const asset=await api.uploadDocumentAsset(record.document_id,file);mutate((n)=>n.tabs.find((x)=>x.tab_id===activeTab).blocks.push({block_id:uid("blk"),type:"image",src:asset.url,caption:file.name,asset_id:asset.asset_id,sha256:asset.sha256}));announce?.("Image added to local document storage.");}catch(err){setError(err);announce?.("Image could not be added.");}}}/></label></div>}</div>
    {editing&&<div className="document-formatbar" role="toolbar" aria-label="Formatting"><button onClick={()=>setCommentSheet(true)}>◌</button><button onClick={()=>format("emoji")}>☺</button><button onClick={()=>format("outdent")}>⇤</button><button onClick={()=>format("indent")}>⇥</button><select aria-label="Block style" value={["paragraph","heading","quote","code"].includes(selected?.type)?selected.type:"paragraph"} onChange={(e)=>selected&&replaceBlock({...selected,type:e.target.value,...(e.target.value==="heading"?{level:2}:{})})}><option value="paragraph">Aa</option><option value="heading">H2</option><option value="quote">Quote</option><option value="code">Code</option></select><button onClick={()=>format("bold")}><strong>B</strong></button><button onClick={()=>format("italic")}><em>I</em></button><button onClick={()=>format("underline")}><u>U</u></button><button onClick={()=>format("strike")}><s>S</s></button><button onClick={()=>format("quote")}>❝</button><button onClick={()=>{const node=editorNode.current;if(node?.blur)node.blur();else document.activeElement?.blur?.();}}>⌨︎</button></div>}
    {tabSheet&&<Sheet title="Document tabs" side="bottom" size="compact" onClose={()=>setTabSheet(false)}><div className="document-tab-sheet">{draft.tabs.map((row)=><button key={row.tab_id} type="button" className="sheet-row" aria-current={row.tab_id===activeTab?"true":undefined} onClick={()=>{setActiveTab(row.tab_id);setTabSheet(false);}}>{row.title}</button>)}<button type="button" className="sheet-row" onClick={()=>{const t=uid("tab");mutate((n)=>n.tabs.push({tab_id:t,title:"Tab "+(n.tabs.length+1),position:n.tabs.length,blocks:[]}));setActiveTab(t);setTabSheet(false);setEditing(true);}}>＋ New tab</button></div></Sheet>}
    {commentSheet&&<Sheet title="Comments" side="bottom" size="medium" onClose={()=>setCommentSheet(false)}><div className="comment-list">{comments.map((row)=><article key={row.comment_id} className={"comment "+row.state.toLowerCase()}><p>{row.text}</p><span className="muted small">{row.state}</span>{row.state==="ACTIVE"&&<button type="button" className="ghost-button" onClick={()=>resolveComment(row.comment_id)}>Resolve</button>}</article>)}</div>{selectedBlock?<form onSubmit={(e)=>{e.preventDefault();addComment();}} className="comment-form"><label className="field"><span>Add comment to selected block</span><textarea value={commentText} onChange={(e)=>setCommentText(e.target.value)}/></label><button type="submit" className="primary-button" disabled={!commentText.trim()}>Add comment</button></form>:<p className="muted">Select a block to anchor a comment.</p>}</Sheet>}
  </section>;
}
