import React, { useCallback, useEffect, useState } from "react";
import { api } from "../../api.js";
import { ConfirmSheet } from "../ConfirmSheet.jsx";
import { DirectoryIntake } from "../DirectoryIntake.jsx";

// Source inspection (TGT-030): every ingested manifestation with identity, parser, warnings and provenance.
export function SourcesWorkspace({ announce }) {
  const [sources, setSources] = useState([]);
  const [index, setIndex] = useState(null);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [pendingRemoval, setPendingRemoval] = useState(null);

  const load = useCallback(async () => {
    try {
      const payload = await api.sources();
      setSources(payload.sources || []);
      setIndex(payload.index || null);
    } catch (err) {
      setError(err);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!selected) return setDetail(null);
    api.source(selected).then(setDetail).catch(setError);
  }, [selected]);

  async function upload(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.ingest(file);
      announce(`Added ${file.name} (${result.chunks_added} chunks).`);
      await load();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
      event.target.value = "";
    }
  }

  // Throws on failure so the confirmation sheet can show the error and stay open.
  async function remove(sourceId) {
    await api.deleteSource(sourceId);
    setSelected(null);
    await load();
    announce("Source removed.");
  }

  return (
    <section className="workspace" aria-labelledby="sources-title">
      <header className="workspace-header">
        <h1 id="sources-title">Sources</h1>
        <p className="muted small">
          {sources.length} source{sources.length === 1 ? "" : "s"} · index {index?.status || "unknown"}
        </p>
      </header>
      <div className="workspace-actions">
        <label className="primary-button file-button">
          {busy ? "Adding…" : "Add a file"}
          <input type="file" onChange={upload} disabled={busy} className="sr-only" />
        </label>
        <button type="button" className="secondary-button" onClick={() => api.reindexSources().then(load).then(() => announce("Index rebuilt."))}>
          Rebuild index
        </button>
      </div>
      {error && <p className="error-card" role="alert">{error.category || "ERROR"} — {error.status}: {error.message}</p>}
      <details className="panel">
        <summary>Add a folder from this Mac</summary>
        <DirectoryIntake onIngested={load} />
      </details>
      {!sources.length && <p className="empty-state muted">No sources yet. Add files or a folder; answers will cite them.</p>}
      <ul className="source-list">
        {sources.map((source) => (
          <li key={source.source_id}>
            <button type="button" className={`source-row ${selected === source.source_id ? "selected" : ""}`} onClick={() => setSelected(source.source_id)} aria-expanded={selected === source.source_id}>
              <strong>{source.file_name}</strong>
              <span className="muted small">
                {source.parser} · {source.chunk_count} chunks · {source.lineage_status}
                {source.warnings?.length ? ` · ${source.warnings.length} warning${source.warnings.length === 1 ? "" : "s"}` : ""}
              </span>
            </button>
            {selected === source.source_id && detail && (
              <div className="source-detail">
                <dl className="facts">
                  <dt>SHA-256</dt>
                  <dd className="mono">{detail.source.source_sha256}</dd>
                  <dt>Type</dt>
                  <dd>{detail.source.mime_type}</dd>
                  <dt>Trust</dt>
                  <dd>{detail.source.provenance?.trust === "UNTRUSTED_CONTENT" ? "Untrusted content (evidence only)" : detail.source.provenance?.trust || "—"}</dd>
                  <dt>Processed by</dt>
                  <dd>{detail.source.provenance ? `${detail.source.provenance.processing.processor} ${detail.source.provenance.processing.processor_version}` : detail.source.parser}</dd>
                </dl>
                {detail.source.warnings?.length > 0 && (
                  <ul className="warning-list">{detail.source.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
                )}
                <details>
                  <summary>Chunks ({detail.chunks.length})</summary>
                  <ol className="chunk-list">
                    {detail.chunks.map((chunk) => <li key={chunk.chunk_id}><span className="mono small">{chunk.chunk_id}</span><p>{chunk.text}</p></li>)}
                  </ol>
                </details>
                <button type="button" className="danger-button" onClick={() => setPendingRemoval({ id: source.source_id, name: source.file_name })}>Remove source</button>
              </div>
            )}
          </li>
        ))}
      </ul>
      {pendingRemoval && (
        <ConfirmSheet title="Remove this source?" confirmLabel="Remove permanently" onConfirm={() => remove(pendingRemoval.id)} onClose={() => setPendingRemoval(null)}>
          <p>
            Remove <strong>{pendingRemoval.name}</strong> from the source library? Mucaro deletes its chunks and its stored copy of the file, then rebuilds the index. Answers that already cited it keep their receipts, but the source can no longer be opened. This cannot be undone.
          </p>
        </ConfirmSheet>
      )}
    </section>
  );
}
