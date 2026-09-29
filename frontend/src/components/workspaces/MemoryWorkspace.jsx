import React, { useCallback, useEffect, useState } from "react";
import { api } from "../../api.js";

// Explicit memory (TGT-013, INV-021): what is retained, where it came from, and on/off/delete controls.
export function MemoryWorkspace({ announce }) {
  const [rows, setRows] = useState([]);
  const [content, setContent] = useState("");
  const [kind, setKind] = useState("preference");
  const [scope, setScope] = useState("global");
  const [scopeId, setScopeId] = useState("");
  const [error, setError] = useState(null);

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
      await api.remember(content.trim(), kind, scope, scope === "conversation" ? scopeId.trim() || null : null);
      setContent("");
      if (scope === "conversation") setScopeId("");
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
        <p className="muted small">Memory is context about you, used only when enabled. It never approves actions and is never cited as evidence.</p>
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
          <select value={scope} onChange={(event) => { setScope(event.target.value); setScopeId(""); }}>
            <option value="global">Global</option>
            <option value="workspace">Current workspace</option>
            <option value="conversation">Conversation</option>
          </select>
        </label>
        {scope === "conversation" && (
          <label className="field">
            <span>Conversation ID</span>
            <input
              value={scopeId}
              onChange={(event) => setScopeId(event.target.value)}
              placeholder="conv_…"
              maxLength={160}
              required
            />
          </label>
        )}
        <button
          type="submit"
          className="primary-button"
          disabled={!content.trim() || (scope === "conversation" && !scopeId.trim())}
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
                {row.kind} · {row.scope || "global"}
                {row.scope_id ? ` · ${row.scope_id}` : ""}
                {row.scope_migration ? " · legacy → global" : ""}
                {` · from ${row.source} · ${row.updated_at}`}
              </p>
            </div>
            <label className="switch">
              <input type="checkbox" checked={row.enabled} onChange={() => api.updateMemory(row.memory_id, !row.enabled).then(load)} />
              <span>{row.enabled ? "On" : "Off"}</span>
            </label>
            <button type="button" className="ghost-button" onClick={() => api.deleteMemory(row.memory_id).then(load).then(() => announce("Memory deleted."))} aria-label={`Delete memory: ${row.content}`}>
              Delete
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
