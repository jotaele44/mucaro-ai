export const ROUTES = Object.freeze([
  { id: "chat", label: "Chat", icon: "chat", context: [] },
  { id: "sources", label: "Sources", icon: "sources", context: [
    { label: "Overview", target: "sources-title" },
    { label: "Import files", target: "sources-import" },
    { label: "Source library", target: "sources-list" },
  ] },
  { id: "documents", label: "Documents", icon: "documents", context: [
    { label: "Documents", target: "documents-title" },
    { label: "New document", action: "new-document" },
    { label: "Recent documents", target: "documents-list" },
  ] },
  { id: "memory", label: "Memory", icon: "memory", context: [
    { label: "Overview", target: "memory-title" },
    { label: "Add memory", target: "memory-add" },
    { label: "Saved memories", target: "memory-list" },
  ] },
  { id: "provenance", label: "Provenance", icon: "provenance", context: [
    { label: "Audit overview", target: "prov-title" },
    { label: "Action history", target: "prov-actions" },
  ] },
  { id: "settings", label: "Settings", icon: "settings", context: [
    { label: "Settings", target: "settings-title" },
  ] },
]);
export const ROUTE_IDS = new Set(ROUTES.map((route) => route.id));
export const MAX_OPEN_TABS = 12;

let mountSerial = 0;
function mountId() {
  return "pane-" + (globalThis.crypto?.randomUUID?.() || (Date.now().toString(36) + "-" + (++mountSerial)));
}
export function tabKey(view, resourceId = null) {
  if (!ROUTE_IDS.has(view)) return null;
  if (view === "chat") return "chat:" + (resourceId || "new");
  if (view === "documents") return "document:" + (resourceId || "library");
  return "workspace:" + view;
}
export function makeTab(view, resourceId = null, returnKey = null) {
  if (!ROUTE_IDS.has(view)) return null;
  if (!["chat", "documents"].includes(view)) resourceId = null;
  if (resourceId != null && (typeof resourceId !== "string" || resourceId.length > 180 || !/^[a-zA-Z0-9_-]+$/.test(resourceId))) return null;
  return { key: tabKey(view, resourceId), view, resourceId, mountId: mountId(), returnKey: typeof returnKey === "string" ? returnKey : null };
}
export function restoreTabs(raw, legacyConversationId = null) {
  if (!Array.isArray(raw)) return [makeTab("chat", legacyConversationId) || makeTab("chat")];
  const seen = new Set(), mounts = new Set(), valid = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const tab = makeTab(
      item.view,
      ["chat", "documents"].includes(item.view) ? item.resourceId ?? item.conversationId ?? null : null,
      typeof item.returnKey === "string" ? item.returnKey : null,
    );
    if (!tab || item.key !== tab.key || seen.has(tab.key)) continue;
    if (typeof item.mountId === "string" && /^pane-[a-zA-Z0-9-]{1,100}$/.test(item.mountId) && !mounts.has(item.mountId)) tab.mountId = item.mountId;
    while (mounts.has(tab.mountId)) tab.mountId = mountId();
    seen.add(tab.key); mounts.add(tab.mountId); valid.push(tab);
    if (valid.length === MAX_OPEN_TABS) break;
  }
  if (!valid.length) return [makeTab("chat")];
  const keys = new Set(valid.map((tab) => tab.key));
  for (const tab of valid) if (!keys.has(tab.returnKey) || tab.returnKey === tab.key) tab.returnKey = null;
  return valid;
}
export function addTab(tabs, requested) {
  if (!requested) return { tabs, key: null, rejected: true };
  const current = tabs.find((tab) => tab.key === requested.key);
  if (current) return { tabs, key: current.key, rejected: false };
  if (tabs.length >= MAX_OPEN_TABS) return { tabs, key: null, rejected: true };
  return { tabs: [...tabs, requested], key: requested.key, rejected: false };
}
export function adoptResource(tabs, oldKey, view, resourceId) {
  const replacement = makeTab(view, resourceId);
  const old = tabs.find((tab) => tab.key === oldKey && tab.view === view);
  if (!replacement || !old) return null;
  replacement.mountId = old.mountId;
  return {
    tabs: tabs.filter((tab) => tab.key !== replacement.key || tab.key === oldKey).map((tab) => tab.key === oldKey ? replacement : tab),
    key: replacement.key,
  };
}
export function removeTab(tabs, key, activeKey) {
  const index = tabs.findIndex((tab) => tab.key === key);
  if (index === -1) return { tabs, key: activeKey };
  let next = tabs.filter((tab) => tab.key !== key);
  if (!next.length) next = [makeTab("chat")];
  const removed = tabs[index];
  const selected = activeKey === key
    ? (removed.returnKey && next.some((tab) => tab.key === removed.returnKey)
      ? removed.returnKey
      : next[Math.min(index, next.length - 1)].key)
    : (next.some((tab) => tab.key === activeKey) ? activeKey : next[0].key);
  return { tabs: next, key: selected };
}
