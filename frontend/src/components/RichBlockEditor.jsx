import React, { forwardRef, useEffect, useImperativeHandle, useRef } from "react";

const TAG_FOR_MARK={bold:"strong",italic:"em",underline:"u",strike:"s"};
const MARK_FOR_TAG={STRONG:"bold",B:"bold",EM:"italic",I:"italic",U:"underline",S:"strike",STRIKE:"strike"};
const sig=(block)=>JSON.stringify([String(block?.text||""),block?.marks||[]]);

function mergeMarks(marks){
  const rows=[...marks].filter((m)=>m.end>m.start).sort((a,b)=>a.type.localeCompare(b.type)||a.start-b.start||a.end-b.end);
  const out=[];
  for(const mark of rows){
    const last=out[out.length-1];
    if(last&&last.type===mark.type&&mark.start<=last.end) last.end=Math.max(last.end,mark.end);
    else out.push({...mark});
  }
  return out.sort((a,b)=>a.start-b.start||a.end-b.end||a.type.localeCompare(b.type));
}

function populate(root,block){
  root.replaceChildren();
  const text=String(block?.text||""),marks=block?.marks||[];
  const boundaries=new Set([0,text.length]);
  for(const mark of marks){boundaries.add(Math.max(0,Math.min(text.length,mark.start)));boundaries.add(Math.max(0,Math.min(text.length,mark.end)));}
  const points=[...boundaries].sort((a,b)=>a-b),fragment=document.createDocumentFragment();
  for(let i=0;i<points.length-1;i++){
    const start=points[i],end=points[i+1];if(end<=start)continue;
    let node=document.createTextNode(text.slice(start,end));
    const active=marks.filter((m)=>m.start<=start&&m.end>=end).map((m)=>m.type);
    for(const type of ["strike","underline","italic","bold"]){
      if(!active.includes(type))continue;
      const el=document.createElement(TAG_FOR_MARK[type]);el.appendChild(node);node=el;
    }
    fragment.appendChild(node);
  }
  if(!text.length) fragment.appendChild(document.createTextNode(""));
  root.appendChild(fragment);
}

function parse(root){
  let text="";const marks=[];
  function walk(node,active=[]){
    if(node.nodeType===Node.TEXT_NODE){
      const start=text.length;text+=node.nodeValue||"";const end=text.length;
      for(const type of active) if(end>start) marks.push({type,start,end});
      return;
    }
    if(node.nodeType!==Node.ELEMENT_NODE)return;
    if(node.tagName==="BR"){text+="\n";return;}
    const own=MARK_FOR_TAG[node.tagName],next=own?[...active,own]:active;
    for(const child of node.childNodes)walk(child,next);
  }
  for(const child of root.childNodes)walk(child,[]);
  return {text:text.replace(/\r/g,""),marks:mergeMarks(marks)};
}

export const RichBlockEditor=forwardRef(function RichBlockEditor({block,onChange,className=""},ref){
  const root=useRef(null),lastEmitted=useRef("");
  useEffect(()=>{
    const next=sig(block);
    if(!root.current||next===lastEmitted.current)return;
    populate(root.current,block);lastEmitted.current=next;
  },[block]);
  function emit(){
    if(!root.current)return;
    const parsed=parse(root.current),next={...block,...parsed};
    lastEmitted.current=sig(next);onChange(next);
  }
  function command(name,value=null){
    root.current?.focus();
    document.execCommand(name,false,value);
    emit();
  }
  useImperativeHandle(ref,()=>({
    focus:()=>root.current?.focus(),
    bold:()=>command("bold"),
    italic:()=>command("italic"),
    underline:()=>command("underline"),
    strike:()=>command("strikeThrough"),
    insertText:(value)=>command("insertText",value),
    indent:()=>command("insertText","  "),
    outdent:()=>{
      const sel=window.getSelection();if(!sel?.rangeCount||!root.current?.contains(sel.anchorNode))return;
      const range=sel.getRangeAt(0);const before=range.startContainer.nodeType===Node.TEXT_NODE?range.startContainer.nodeValue?.slice(Math.max(0,range.startOffset-2),range.startOffset):"";
      if(before==="  "){range.setStart(range.startContainer,Math.max(0,range.startOffset-2));range.deleteContents();emit();}
    },
    read:()=>root.current?parse(root.current):{text:"",marks:[]},
  }),[block,onChange]);
  return <div ref={root} className={"rich-block-editor "+className} contentEditable suppressContentEditableWarning
    role="textbox" aria-label="Edit document block" aria-multiline="true"
    onInput={emit}
    onPaste={(event)=>{event.preventDefault();command("insertText",event.clipboardData.getData("text/plain"));}}
    onKeyDown={(event)=>{if(event.key==="Enter"){event.preventDefault();command("insertLineBreak");}}}
  />;
});
