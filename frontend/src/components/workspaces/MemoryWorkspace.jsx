import React, { useCallback, useEffect, useState } from "react";
import { api } from "../../api.js";
import { Sheet } from "../Sheet.jsx";

// Explicit memory (TGT-013, INV-021): what is retained, where it came from, and on/off/delete controls.
export function MemoryWorkspace({ announce }) {
  const [rows, setRows] = useState([]);
  const [content, setContent] = useState("");
  const [kind, setKind] = useState("preference");
  const [pendingDelete, setPendingDelete] = useState(null);
  const [updatingId, setUpdatingId] = useState(null);
  const [deleting, setDeleting] = useState(false);
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
      await api.remember(content.trim(), kind);
      setContent("");
      await load();
      announce("Saved to memory.");
    } catch (err) {
      setError(err);
    }
  }

  async function toggle(row) {
    setUpdatingId(row.memory_id);
    setError(null);
    try {
      await api.updateMemory(row.memory_id, !row.enabled);
      await load();
      announce(`Memory ${row.enabled ? "disabled" : "enabled"}.`);
    } catch (err) {
      setError(err);
    } finally {
      setUpdatingId(null);
    }
  }

  async function remove(memoryId) {
    setDeleting(true);
    setError(null);
    try {
      await api.deleteMemory(memoryId);
      setPendingDelete(null);
      await load();
      announce("Memory deleted.");
    } catch (err) {
      setError(err);
    } finally {
      setDeleting(false);
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
          <input
            name="memory-content"
            autoComplete="off"
            value={content}
            onChange={(event) => setContent(event.target.value)}
            maxLength={800}
            placeholder="Prefer tables for comparisons…"
          />
        </label>
        <label className="field">
          <span>Kind</span>
          <select name="memory-kind" value={kind} onChange={(event) => setKind(event.target.value)}>
            <option value="preference">Preference</option>
            <option value="profile">Profile</option>
            <option value="instruction">Instruction</option>
            <option value="fact">Fact</option>
          </select>
        </label>
        <button type="submit" className="primary-button" disabled={!content.trim()}>Save</button>
      </form>
      {!rows.length && <p className="empty-state muted">Nothing remembered.</p>}
      <ul className="memory-list">
        {rows.map((row) => (
          <li key={row.memory_id} className={row.enabled ? "" : "disabled-row"}>
            <div className="grow">
              <p>{row.content}</p>
              <p className="muted small">{row.kind} · from {row.source} · {row.updated_at}</p>
            </div>
            <label className="switch">
              <input type="checkbox" checked={row.enabled} disabled={updatingId === row.memory_id || deleting} onChange={() => toggle(row)} />
              <span>{updatingId === row.memory_id ? "Saving…" : row.enabled ? "On" : "Off"}</span>
            </label>
            <button type="button" className="ghost-button" onClick={() => setPendingDelete(row)} disabled={deleting} aria-label={`Delete memory: ${row.content}`}>
              Delete
            </button>
          </li>
        ))}
      </ul>
      {pendingDelete && (
        <Sheet title="Delete saved memory?" onClose={() => setPendingDelete(null)} side="center">
          <p>This permanently deletes the saved memory item “{pendingDelete.content}”. It will no longer be available as context for future answers.</p>
          <div className="button-row">
            <button type="button" className="secondary-button" onClick={() => setPendingDelete(null)} disabled={deleting}>Keep memory</button>
            <button type="button" className="danger-button" onClick={() => remove(pendingDelete.memory_id)} disabled={deleting}>
              {deleting ? "Deleting…" : "Delete permanently"}
            </button>
          </div>
        </Sheet>
      )}
    </section>
  );
}
