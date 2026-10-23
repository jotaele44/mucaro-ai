import test from "node:test";
import assert from "node:assert/strict";
import { MAX_OPEN_TABS, ROUTES, addTab, adoptConversation, makeTab, removeTab, restoreTabs, tabKey } from "./navigation.js";

test("route registry only advertises real workspaces with a single home", () => {
  assert.deepEqual(ROUTES.map((r) => r.id), ["chat", "sources", "memory", "provenance", "settings"]);
  assert.equal(new Set(ROUTES.map((r) => r.id)).size, ROUTES.length);
  assert.equal(tabKey("sources"), "workspace:sources");
  assert.equal(makeTab("plugins"), null);
});

test("restoration fails closed on invalid, duplicate and forged tab entries", () => {
  const good = makeTab("chat", "abc_123");
  const raw = [null, {}, { key: "workspace:plugins", view: "plugins" }, good,
    { ...good, mountId: "pane-forged" }, { key: "chat:evil/route", view: "chat", conversationId: "evil/route" },
    { key: "workspace:sources", view: "sources", conversationId: "unexpected", mountId: "../wrong" }];
  const result = restoreTabs(raw);
  assert.equal(result.length, 2);
  assert.deepEqual(result.map((t) => t.key), ["chat:abc_123", "workspace:sources"]);
  assert.match(result[1].mountId, /^pane-/);
  assert.deepEqual(restoreTabs("corrupt").map((t) => t.key), ["chat:new"]);
  assert.deepEqual(restoreTabs([]).map((t) => t.key), ["chat:new"]);
  assert.deepEqual(restoreTabs(null, "id123").map((t) => t.key), ["chat:id123"]);
  assert.equal(makeTab("chat", "invalid/slash"), null);
});

test("activating existing tab never duplicates it", () => {
  const initial = [makeTab("chat")];
  const existing = addTab(initial, makeTab("chat"));
  assert.equal(existing.rejected, false);
  assert.equal(existing.key, "chat:new");
  assert.equal(existing.tabs, initial);
});

test("tab limit rejects addition without silent loss", () => {
  let tabs = [makeTab("chat")];
  for (let i = 1; i < MAX_OPEN_TABS; i++) tabs = addTab(tabs, makeTab("chat", "id" + i)).tabs;
  const denied = addTab(tabs, makeTab("sources"));
  assert.equal(denied.rejected, true);
  assert.equal(denied.tabs, tabs);
  assert.equal(restoreTabs([...tabs, makeTab("settings")]).length, MAX_OPEN_TABS);
});

test("adopting a newly created conversation preserves pane identity and all other tabs", () => {
  const first = makeTab("chat");
  const second = makeTab("sources");
  const outcome = adoptConversation([first, second], first.key, "new_1");
  assert.equal(outcome.key, "chat:new_1");
  assert.equal(outcome.tabs[0].mountId, first.mountId);
  assert.equal(outcome.tabs[1].mountId, second.mountId);
  assert.equal(outcome.tabs.length, 2);
  assert.equal(adoptConversation([second], second.key, "id1"), null);
  assert.equal(adoptConversation([first], first.key, "../bad"), null);
});

test("closing a non-active tab retains selection; closing the last tab gives a safe chat home", () => {
  const chat = makeTab("chat");
  const sources = makeTab("sources");
  const first = removeTab([chat, sources], sources.key, chat.key);
  assert.equal(first.key, chat.key);
  assert.equal(first.tabs.length, 1);
  const last = removeTab(first.tabs, chat.key, chat.key);
  assert.equal(last.key, "chat:new");
  assert.equal(last.tabs.length, 1);
  assert.equal(removeTab([chat], "missing", chat.key).tabs[0], chat);
});

test("tab mount IDs stay unique when restoring duplicated persisted IDs", () => {
  const chat = makeTab("chat"), sources = makeTab("sources");
  const restored = restoreTabs([chat, { ...sources, mountId: chat.mountId }]);
  assert.equal(new Set(restored.map((t) => t.mountId)).size, 2);
});
