import React, { useCallback, useEffect, useState } from "react";
import { api } from "../../api.js";
import { ConfirmSheet } from "../ConfirmSheet.jsx";

// Explicit memory (TGT-013, INV-021): what is retained, where it came from, and on/off/delete controls.
export function MemoryWorkspace({ announce, currentConversationId = null }) {
  const [rows, setRows] = useState([]);
  const [content, setContent] = useState("");
  const [kind, setKind] = useState("preference");
  const [scope, setScope] = useState("global");
  const [error, setError] = useState(null);
  const [pendingDelete, setPendingDelete] = useState(null);

  const load = useCallback(async () => {
    try {
      setRows((await api.memory()).memories || []);
    } catch (err) {
      setError(err);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function add(event) {
    event.preventDefault();
    if (!content.trim()) return;
    try {
      await api.remember(content.trim(), kind, scope, scope === "conversation" ? currentConversationId : null);
      setContent("");
      await load();
      announce("Saved to memory.");
    } catch (err) {
      setError(err);
    }
  }

  return (
    <section className="workspace" aria-labelledby="memory-title">
      <header className="workspace-header">
        <h1 id="memory-title">Memory</h1>
        <p className="muted small" data-testid="memory-durable-note">
          Saved memory is kept across chats until you turn it off or delete it. A chat's own earlier messages are used
          only in that chat and are never saved here.
        </p>
        <p className="muted small">Memory is used only when enabled. It never approves actions and is never cited as evidence.</p>
      </header>
      {error && <p className="error-card" role="alert">{error.status}: {error.message}</p>}
      <form className="inline-form" onSubmit={add}>
        <label className="field grow">
          <span>Remember</span>
          <input value={content} onChange={(event) => setContent(event.target.value)} maxLength={800} placeholder="Prefer tables for comparisons" />
        </label>
        <label className="field">
          <span>Kind</span>
          <select value={kind} onChange={(event) => setKind(event.target.value)}>
            <option value="preference">Preference</option>
            <option value="profile">Profile</option>
            <option value="instruction">Instruction</option>
            <option value="fact">Fact</option>
          </select>
        </label>
        <label className="field">
          <span>Scope</span>
          <select value={scope} onChange={(event) => setScope(event.target.value)}>
            <option value="global">All chats</option>
            <option value="workspace">This workspace</option>
            <option value="conversation" disabled={!currentConversationId}>
              {currentConversationId ? "Only the current chat" : "Only the current chat (open a chat first)"}
            </option>
          </select>
        </label>
        <button
          type="submit"
          className="primary-button"
          disabled={!content.trim() || (scope === "conversation" && !currentConversationId)}
        >
          Save
        </button>
      </form>
      {!rows.length && <p className="empty-state muted">Nothing remembered.</p>}
      <ul className="memory-list">
        {rows.map((row) => (
          <li key={row.memory_id} className={row.enabled ? "" : "disabled-row"}>
            <div className="grow">
              <p>{row.content}</p>
              <p className="muted small">
                {`${row.kind} · from ${row.source} · scope ${row.scope || "global"}`}
                {row.scope_id ? ` · ${row.scope_id}` : ""}
                {row.scope_migration ? " · legacy → global" : ""}
                {` · ${row.updated_at}`}
              </p>
            </div>
            <label className="switch">
              <input type="checkbox" checked={row.enabled} onChange={() => api.updateMemory(row.memory_id, !row.enabled).then(load)} />
              <span>{row.enabled ? "On" : "Off"}</span>
            </label>
            <button type="button" className="ghost-button" onClick={() => setPendingDelete(row)} aria-label={`Delete memory: ${row.content}`}>
              Delete
            </button>
          </li>
        ))}
      </ul>
      {pendingDelete && (
        <ConfirmSheet
          title="Delete this memory?"
          confirmLabel="Delete memory"
          onConfirm={async () => {
            await api.deleteMemory(pendingDelete.memory_id);
            await load();
            announce("Memory deleted.");
          }}
          onClose={() => setPendingDelete(null)}
        >
          <p>“{pendingDelete.content}” will stop shaping answers and is permanently removed.</p>
        </ConfirmSheet>
      )}
    </section>
  );
}
