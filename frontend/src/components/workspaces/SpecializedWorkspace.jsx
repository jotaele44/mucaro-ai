import React, { useEffect, useState } from "react";
import { api } from "../../api.js";

const COPY = {
  gis: {
    title: "GIS workspace",
    description: "Inspect spatial capability state and offline dependencies. The conversation remains the orchestration plane for governed GIS work.",
  },
  code: {
    title: "Code workspace",
    description: "Inspect repository and runtime readiness before a coding run. This surface never implies that an isolated execution has happened.",
  },
  artifacts: {
    title: "Artifacts",
    description: "Review outputs attached to persisted work runs and download only run-bound export formats.",
  },
};

function stateClass(state) {
  if (!state) return "pending";
  if (["AVAILABLE", "AVAILABLE_LOCALLY", "READY", "COMPLETE", "COMPLETED"].includes(state)) return "completed";
  if (["CANDIDATE", "PARTIAL", "REQUIRES_REMOTE_SERVICE"].includes(state)) return "queued";
  if (["ABSENT", "BLOCKED", "LOCAL_DEPENDENCY_MISSING", "FAILED"].includes(state)) return "failed";
  return "pending";
}

function Status({ value }) {
  return <span className={`status-chip status-${stateClass(value)}`}>{value || "UNKNOWN"}</span>;
}

function CapabilityRows({ rows }) {
  return (
    <ul className="readiness-list">
      {rows.map((row) => (
        <li key={row.id || row.capability || row.name} className="panel readiness-row">
          <div className="tool-row-head"><Status value={row.state || row.state_now} /><strong>{row.name || row.capability}</strong></div>
          <p>{row.notes || row.description}</p>
          {(row.contract || row.local_dependency) && <p className="muted small">{row.contract || `Dependency: ${row.local_dependency}`}</p>}
        </li>
      ))}
    </ul>
  );
}

function GISView({ setError }) {
  const [payload, setPayload] = useState(null);
  useEffect(() => {
    Promise.all([api.intelligencePlane(), api.offlineManifest()])
      .then(([plane, offline]) => setPayload({ plane: plane.descriptor, offline }))
      .catch(setError);
  }, [setError]);
  if (!payload) return <p className="muted">Loading GIS capability evidence…</p>;
  const capability = payload.plane.capabilities?.advanced_gis_processing;
  const offline = (payload.offline.capabilities || []).filter((row) => row.capability.startsWith("gis."));
  return (
    <>
      <section className="panel">
        <div className="tool-row-head"><Status value={capability?.state} /><h2>Spatial engine</h2></div>
        <p>{capability?.notes}</p>
        <dl className="facts"><dt>Contract</dt><dd className="mono">{capability?.contract || "—"}</dd><dt>Network</dt><dd>Local capability state only; execution remains governed by the conversation and tool permissions.</dd></dl>
      </section>
      <section className="panel"><h2>Offline boundary</h2><CapabilityRows rows={offline} /></section>
    </>
  );
}

function CodeView({ setError }) {
  const [coding, setCoding] = useState(null);
  useEffect(() => { api.codingReadiness().then(setCoding).catch(setError); }, [setError]);
  if (!coding) return <p className="muted">Loading coding readiness…</p>;
  return (
    <section className="panel">
      <div className="tool-row-head"><Status value={coding.status} /><h2>Repository execution readiness</h2></div>
      <p className="muted small">This is a prerequisite diagnostic. No repository command, container run, image pull, test, lint, build, or mutation was executed by this view.</p>
      <dl className="facts">
        <dt>Execution state</dt><dd>{coding.execution_status}</dd>
        <dt>Source HEAD</dt><dd className="mono">{coding.source?.head_sha || "UNKNOWN"}</dd>
        <dt>Container engine</dt><dd>{coding.runtime?.engine || coding.runtime?.engine_error?.status || "UNAVAILABLE"}</dd>
        <dt>Image</dt><dd className="mono">{coding.runtime?.image || coding.runtime?.image_error?.status || "REQUIRED"}</dd>
        <dt>Network</dt><dd>{coding.runtime?.network_allowed === false ? "DISABLED" : "UNVERIFIED"}</dd>
      </dl>
      {coding.blockers?.length > 0 && <p className="error-card" role="status">Blocked prerequisites: {coding.blockers.join(", ")}</p>}
    </section>
  );
}

function ArtifactsView({ setError }) {
  const [runs, setRuns] = useState([]);
  const [downloading, setDownloading] = useState(null);
  useEffect(() => { api.workRuns().then((payload) => setRuns(payload.runs || [])).catch(setError); }, [setError]);

  async function download(runId, kind) {
    setDownloading(`${runId}:${kind}`);
    try {
      const blob = await api.exportFile(runId, kind);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${runId}.${kind}`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err);
    } finally {
      setDownloading(null);
    }
  }

  return (
    <>
      {!runs.length && <p className="empty-state muted">No persisted work-run artifacts yet.</p>}
      <ul className="tool-list">
        {runs.map((run) => (
          <li key={run.run_id} className="tool-row">
            <div className="tool-row-head"><Status value={run.status} /><strong>{run.run_id}</strong><span className="muted small">{run.created_at || ""}</span></div>
            <p className="muted small">{run.prompt || run.query || "Work run"}</p>
            <div className="workspace-actions">
              {["markdown", "json", "csv"].map((kind) => <button key={kind} type="button" className="secondary-button" disabled={downloading === `${run.run_id}:${kind}`} onClick={() => download(run.run_id, kind)}>{downloading === `${run.run_id}:${kind}` ? "Preparing…" : `Download ${kind}`}</button>)}
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

export function SpecializedWorkspace({ kind }) {
  const [error, setError] = useState(null);
  const copy = COPY[kind];
  return (
    <section className="workspace" aria-labelledby={`${kind}-workspace-title`}>
      <header className="workspace-header">
        <h1 id={`${kind}-workspace-title`}>{copy.title}</h1>
        <p className="muted small">{copy.description}</p>
      </header>
      {error && <p className="error-card" role="alert">{error.status || "ERROR"} — {error.message || "The workspace could not load."}</p>}
      {kind === "gis" && <GISView setError={setError} />}
      {kind === "code" && <CodeView setError={setError} />}
      {kind === "artifacts" && <ArtifactsView setError={setError} />}
    </section>
  );
}
