// Single registry for implemented navigation destinations. Do not advertise unfinished pages.
export const ROUTES = Object.freeze([
  { id: "chat", label: "Chat", icon: "chat", context: [] },
  { id: "sources", label: "Sources", icon: "sources", context: [
    { label: "Sources overview", target: "sources-title" },
    { label: "Import files", target: "sources-import" },
    { label: "Source library", target: "sources-list" },
  ] },
  { id: "memory", label: "Memory", icon: "memory", context: [
    { label: "Memory overview", target: "memory-title" },
    { label: "Add memory", target: "memory-add" },
    { label: "Saved memories", target: "memory-list" },
  ] },
  { id: "provenance", label: "Provenance", icon: "provenance", context: [
    { label: "Audit overview", target: "prov-title" },
    { label: "Action history", target: "prov-actions" },
  ] },
  { id: "settings", label: "Settings", icon: "settings", context: [
    { label: "Settings overview", target: "settings-title" },
    { label: "Answer model", target: "settings-model" },
    { label: "Retrieval", target: "settings-retrieval" },
    { label: "Local access", target: "settings-access" },
  ] },
]);
export const ROUTE_IDS = new Set(ROUTES.map((route) => route.id));
export const MAX_OPEN_TABS = 10;

let mountSerial = 0;
function mountId() {
  // UI instance identity only: this is never an evidence or source identifier.
  return "pane-" + (globalThis.crypto?.randomUUID?.() || (Date.now().toString(36) + "-" + (++mountSerial)));
}
export function tabKey(view, conversationId = null) {
  if (!ROUTE_IDS.has(view)) return null;
  return view === "chat" ? "chat:" + (conversationId || "new") : "workspace:" + view;
}
export function makeTab(view, conversationId = null) {
  if (!ROUTE_IDS.has(view)) return null;
  if (view !== "chat") conversationId = null;
  if (conversationId != null && (typeof conversationId !== "string" || conversationId.length > 160 || !/^[a-zA-Z0-9_-]+$/.test(conversationId))) return null;
  return { key: tabKey(view, conversationId), view, conversationId, mountId: mountId() };
}
export function restoreTabs(raw, legacyConversationId = null) {
  if (!Array.isArray(raw)) return [makeTab("chat", legacyConversationId) || makeTab("chat")];
  const seen = new Set();
  const mounts = new Set();
  const valid = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const tab = makeTab(item.view, item.view === "chat" ? item.conversationId ?? null : null);
    if (!tab || item.key !== tab.key || seen.has(tab.key)) continue;
    // Do not trust persisted mount IDs if malformed or duplicated.
    if (typeof item.mountId === "string" && /^pane-[a-zA-Z0-9-]{1,100}$/.test(item.mountId) && !mounts.has(item.mountId)) {
      tab.mountId = item.mountId;
    }
    while (mounts.has(tab.mountId)) tab.mountId = mountId();
    seen.add(tab.key);
    mounts.add(tab.mountId);
    valid.push(tab);
    if (valid.length === MAX_OPEN_TABS) break;
  }
  return valid.length ? valid : [makeTab("chat")];
}
export function addTab(tabs, requested) {
  if (!requested) return { tabs, key: null, rejected: true };
  const current = tabs.find((tab) => tab.key === requested.key);
  if (current) return { tabs, key: current.key, rejected: false };
  if (tabs.length >= MAX_OPEN_TABS) return { tabs, key: null, rejected: true };
  return { tabs: [...tabs, requested], key: requested.key, rejected: false };
}
export function adoptConversation(tabs, oldKey, conversationId) {
  const replacement = makeTab("chat", conversationId);
  if (!replacement || !conversationId || !tabs.some((tab) => tab.key === oldKey && tab.view === "chat")) return null;
  const old = tabs.find((tab) => tab.key === oldKey);
  // Preserve the mounted ChatView during a new conversation's first streamed turn.
  replacement.mountId = old.mountId;
  return {
    tabs: tabs.filter((tab) => tab.key !== replacement.key || tab.key === oldKey)
      .map((tab) => tab.key === oldKey ? replacement : tab),
    key: replacement.key,
  };
}
export function removeTab(tabs, key, activeKey) {
  const index = tabs.findIndex((tab) => tab.key === key);
  if (index === -1) return { tabs, key: activeKey };
  let next = tabs.filter((tab) => tab.key !== key);
  if (!next.length) next = [makeTab("chat")];
  const selected = activeKey === key
    ? next[Math.min(index, next.length - 1)].key
    : (next.some((tab) => tab.key === activeKey) ? activeKey : next[0].key);
  return { tabs: next, key: selected };
}
