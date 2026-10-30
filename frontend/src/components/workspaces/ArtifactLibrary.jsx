import React, { useEffect, useRef, useState } from "react";
import { api } from "../../api.js";
import { saveFile } from "../../files.js";

// The shared store survives workspace changes. A listing never claims that file bytes were checked.
export function ArtifactLibrary({ announce }) {
  const [page, setPage] = useState(null);
  const [filter, setFilter] = useState("");
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);
  const inFlight = useRef(false);
  const alive = useRef(true);

  async function act(key, operation) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(key);
    setError(null);
    try { await operation(); }
    catch (err) { if (alive.current) setError(err); }
    finally {
      inFlight.current = false;
      if (alive.current) setBusy(null);
    }
  }

  function load(more = false) {
    return act("list", async () => {
      const payload = await api.artifacts({
        query: more ? page.query : filter, offset: more ? page.next_offset : 0,
        revision: more ? page.revision : undefined,
      });
      if (alive.current) {
        setPage((current) => ({ ...payload, artifacts: more ? [...current.artifacts, ...payload.artifacts] : payload.artifacts }));
        announce?.(`${payload.total} saved outputs${payload.query ? " matching the filter" : ""}.`);
      }
    });
  }

  useEffect(() => {
    alive.current = true;
    load();
    return () => { alive.current = false; };
  }, []);

  function inspect(artifactId) {
    act(`inspect:${artifactId}`, async () => {
      const payload = await api.artifact(artifactId);
      if (alive.current) setDetail(payload);
    });
  }

  function download(row) {
    act(`download:${row.artifact_id}`, async () => {
      const blob = await api.artifactFile(row.artifact_id);
      if (alive.current) {
        saveFile(blob, row.filename || `${row.artifact_id}.bin`);
        announce?.(`Download prepared: ${row.filename || row.artifact_id}.`);
      }
    });
  }

  return (
    <section aria-labelledby="saved-outputs-title" aria-busy={Boolean(busy)}>
      <h2 id="saved-outputs-title">Saved outputs</h2>
      <p className="muted small">Reopen OCR, GIS, and answer artifacts saved by the app. Downloads check the recorded size and SHA-256 before returning bytes.</p>
      <form className="panel" onSubmit={(event) => { event.preventDefault(); load(); }}>
        <label className="field">Filter saved outputs<input value={filter} maxLength={200} onChange={(event) => setFilter(event.target.value)} placeholder="Filename, producer, or artifact ID" /></label>
        <div className="button-row">
          <button type="submit" className="secondary-button" disabled={Boolean(busy)}>Apply filter</button>
          <button type="button" className="secondary-button" disabled={Boolean(busy)} onClick={() => load()}>Refresh saved outputs</button>
        </div>
      </form>
      {error && <p className="error-card" role="alert">{error.message || "Saved outputs could not load. Refresh to try again."}{page && " The list below is the last loaded snapshot."}</p>}
      {busy === "list" && <p role="status">Loading saved outputs…</p>}
      {busy?.startsWith("inspect:") && <p role="status">Loading artifact details…</p>}
      {page && <p className="muted small">Showing {page.artifacts.length} of {page.total} saved outputs{page.query ? ` matching “${page.query}”` : ""}. File integrity is checked on download.</p>}
      {page && !page.artifacts.length && <p className="empty-state muted">{page.query ? "No saved outputs match this filter. Change the filter and apply it again." : "No saved outputs yet. Run an OCR, GIS, or grounded-answer task to create one."}</p>}
      <ul className="tool-list" aria-label="Saved output artifacts">
        {(page?.artifacts || []).map((row) => (
          <li key={row.artifact_id} className="tool-row">
            <div className="tool-row-head"><strong>{row.filename || `Artifact from ${row.producer}`}</strong><span className="status-chip">{row.artifact_type}</span></div>
            <p className="muted small">{row.byte_size.toLocaleString()} bytes · {row.producer} · {row.created_utc}</p>
            <p className="mono small">{row.artifact_id}</p>
            <div className="workspace-actions">
              <button type="button" className="secondary-button" disabled={Boolean(busy)} onClick={() => inspect(row.artifact_id)}>Inspect lineage</button>
              {row.download_available ? <button type="button" className="secondary-button" disabled={Boolean(busy)} onClick={() => download(row)}>{busy === `download:${row.artifact_id}` ? "Preparing…" : "Download saved file"}</button> : <span className="muted small">Metadata only; no downloadable export file.</span>}
            </div>
          </li>
        ))}
      </ul>
      {page?.next_offset != null && <button type="button" className="secondary-button" disabled={Boolean(busy)} onClick={() => load(true)}>Load more saved outputs</button>}
      {detail && (
        <section className="panel" aria-labelledby="artifact-detail-title">
          <div className="tool-row-head"><h3 id="artifact-detail-title">Saved artifact details</h3><button type="button" className="secondary-button" onClick={() => setDetail(null)}>Close artifact details</button></div>
          <dl className="facts">
            <dt>Artifact ID</dt><dd className="mono">{detail.artifact.artifact_id}</dd>
            <dt>SHA-256</dt><dd className="mono">{detail.artifact.sha256}</dd>
            <dt>Provenance</dt><dd>{detail.artifact.provenance_state === "RECORDED" ? "Recorded and structurally validated" : "Legacy record without provenance metadata"}</dd>
            <dt>File integrity</dt><dd>{detail.artifact.download_available ? "Checked on download" : "No downloadable export file"}</dd>
          </dl>
          <h4>Direct parents ({detail.parents.length})</h4>
          <ul>{detail.parents.map((parent) => <li key={parent.artifact_id}><span className="mono small">{parent.artifact_id}</span> · {parent.artifact_type}<button type="button" className="secondary-button" disabled={Boolean(busy)} onClick={() => inspect(parent.artifact_id)}>Inspect parent {parent.artifact_id}</button></li>)}</ul>
          <p className="muted small">Names help find outputs. Stable artifact IDs and recorded hashes bind their lineage; matching names alone do not prove identity.</p>
        </section>
      )}
    </section>
  );
}
