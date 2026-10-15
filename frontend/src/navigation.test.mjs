import test from "node:test";
import assert from "node:assert/strict";
import { MAX_OPEN_TABS, ROUTES, addTab, adoptResource, makeTab, removeTab, restoreTabs, tabKey } from "./navigation.js";

test("route registry contains only implemented canonical destinations",()=>{
  assert.deepEqual(ROUTES.map((r)=>r.id),["chat","sources","documents","memory","provenance","settings"]);
  assert.equal(new Set(ROUTES.map((r)=>r.id)).size,ROUTES.length);
  assert.equal(makeTab("plugins"),null);
});
test("chat and document resources have distinct stable keys",()=>{
  assert.equal(tabKey("chat","abc"),"chat:abc");
  assert.equal(tabKey("documents","abc"),"document:abc");
  assert.equal(tabKey("documents"),"document:library");
});
test("invalid persisted state fails closed without duplicate tabs",()=>{
  const good=makeTab("chat","abc_1");
  const raw=[good,{...good,mountId:"pane-duplicate"},{view:"documents",resourceId:"doc_1",key:"document:doc_1",mountId:"../bad"},{view:"fake",key:"workspace:fake"}];
  const restored=restoreTabs(raw);
  assert.deepEqual(restored.map((t)=>t.key),["chat:abc_1","document:doc_1"]);
  assert.equal(new Set(restored.map((t)=>t.mountId)).size,2);
  assert.deepEqual(restoreTabs("broken").map((t)=>t.key),["chat:new"]);
});
test("opening an existing tab activates instead of duplicating",()=>{
  const tabs=[makeTab("documents","doc_1")];
  const out=addTab(tabs,makeTab("documents","doc_1"));
  assert.equal(out.tabs,tabs); assert.equal(out.key,"document:doc_1"); assert.equal(out.rejected,false);
});
test("tab cap rejects without silent eviction",()=>{
  let tabs=[makeTab("chat")];
  for(let i=1;i<MAX_OPEN_TABS;i++) tabs=addTab(tabs,makeTab("chat","c"+i)).tabs;
  const out=addTab(tabs,makeTab("settings"));
  assert.equal(out.rejected,true); assert.equal(out.tabs,tabs); assert.equal(tabs.length,MAX_OPEN_TABS);
});
test("resource adoption preserves mounted pane identity",()=>{
  const old=makeTab("documents"), chat=makeTab("chat","c1");
  const out=adoptResource([old,chat],old.key,"documents","doc_99");
  assert.equal(out.key,"document:doc_99");
  assert.equal(out.tabs[0].mountId,old.mountId);
  assert.equal(out.tabs[1].mountId,chat.mountId);
});
test("resource adoption rejects wrong view and invalid ids",()=>{
  const old=makeTab("chat");
  assert.equal(adoptResource([old],old.key,"documents","doc_1"),null);
  assert.equal(adoptResource([old],old.key,"chat","../bad"),null);
});
test("closing active chooses deterministic neighbor and last close restores new chat",()=>{
  const a=makeTab("chat","a"),b=makeTab("documents","b"),c=makeTab("settings");
  const one=removeTab([a,b,c],b.key,b.key);
  assert.equal(one.key,c.key); assert.deepEqual(one.tabs.map((t)=>t.key),[a.key,c.key]);
  const last=removeTab([a],a.key,a.key);
  assert.equal(last.key,"chat:new"); assert.equal(last.tabs.length,1);
});
