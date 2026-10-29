import React, { useEffect, useState } from "react";
import { api } from "../../api.js";
import { SkillsWorkspace } from "./SkillsWorkspace.jsx";

const COPY = {
  gis: {
    title: "GIS workspace",
    description: "Analyze local spatial data with explicit permission, frozen inputs, and saved reports. Review capability and offline limits alongside the results.",
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

function GISView({ setError, announce }) {
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
      <SkillsWorkspace announce={announce} skillPrefix="mucaro/gis-" embedded />
      <section className="panel">
        <div className="tool-row-head"><Status value={capability?.state} /><h2>Spatial engine</h2></div>
        <p>{capability?.notes}</p>
        <dl className="facts"><dt>Contract</dt><dd className="mono">{capability?.contract || "—"}</dd><dt>Execution</dt><dd>Vector tasks run locally through shared skill permissions. CRS transformations disable network grid downloads.</dd></dl>
      </section>
      <section className="panel"><h2>Offline boundary</h2><CapabilityRows rows={offline} /></section>
    </>
  );
}

function CodeView({ setError }) {
  const [coding, setCoding] = useState(null);
  const [filePath, setFilePath] = useState("README.md");
  const [toolCalls, setToolCalls] = useState({});
  const [busyTool, setBusyTool] = useState(null);
  useEffect(() => { api.codingReadiness().then(setCoding).catch(setError); }, [setError]);
  if (!coding) return <p className="muted">Loading coding readiness…</p>;

  const repoRoot = coding.repo_root;
  const readOnlyActions = [
    {
      tool: "coding.repo_status",
      label: "Inspect repository",
      description: "Read exact HEAD, branch, and dirty-state candidates.",
      arguments: () => ({ repo_root: repoRoot }),
    },
    {
      tool: "coding.diff",
      label: "Inspect current diff",
      description: "Read staged, unstaged, and untracked changes against HEAD.",
      arguments: () => ({ repo_root: repoRoot, base: "HEAD" }),
    },
  ];

  async function requestReadOnly(action) {
    if (busyTool) return;
    setBusyTool(action.tool);
    setError(null);
    try {
      const response = await api.createToolRequest(action.tool, action.arguments(), `Code workspace read-only inspection: ${action.tool}`);
      setToolCalls((current) => ({ ...current, [action.tool]: response.tool_call }));
    } catch (err) {
      setError(err);
    } finally {
      setBusyTool(null);
    }
  }

  async function approveAndRun(action) {
    const call = toolCalls[action.tool];
    if (!call || busyTool) return;
    setBusyTool(action.tool);
    setError(null);
    try {
      await api.approveTool(call.tool_call_id, call.arguments_sha256);
      const response = await api.runToolRequest(call.tool_call_id);
      setToolCalls((current) => ({ ...current, [action.tool]: response.tool_call || { ...call, status: response.status, result: response.result } }));
    } catch (err) {
      setToolCalls((current) => ({ ...current, [action.tool]: { ...call, status: err.status || "FAILED", result: { status: err.status, detail: err.message } } }));
      setError(err);
    } finally {
      setBusyTool(null);
    }
  }

  async function deny(action) {
    const call = toolCalls[action.tool];
    if (!call || busyTool) return;
    setBusyTool(action.tool);
    setError(null);
    try {
      const response = await api.denyTool(call.tool_call_id, call.arguments_sha256);
      setToolCalls((current) => ({ ...current, [action.tool]: response.tool_call }));
    } catch (err) {
      setError(err);
    } finally {
      setBusyTool(null);
    }
  }

  async function requestFileRead() {
    const path = filePath.trim();
    if (!path || busyTool) return;
    const action = { tool: "coding.read_file", arguments: () => ({ repo_root: repoRoot, path }) };
    await requestReadOnly(action);
  }

  const actionCard = (action) => {
    const call = toolCalls[action.tool];
    const status = call?.status;
    const pending = status === "PENDING_APPROVAL";
    const terminal = status === "EXECUTED" || status === "DENIED" || status === "FAILED";
    return (
      <section key={action.tool} className="panel nested-panel" aria-label={`${action.label} control`}>
        <div className="tool-row-head"><Status value={status || "READY"} /><strong>{action.label}</strong></div>
        <p className="muted small">{action.description}</p>
        {!call && <button type="button" className="secondary-button" disabled={!repoRoot || Boolean(busyTool)} onClick={() => requestReadOnly(action)}>Request read-only inspection</button>}
        {pending && (
          <div className="button-row">
            <button type="button" className="secondary-button" disabled={busyTool === action.tool} onClick={() => deny(action)}>Deny</button>
            <button type="button" className="primary-button" disabled={busyTool === action.tool} onClick={() => approveAndRun(action)}>{busyTool === action.tool ? "Running…" : "Allow once and run"}</button>
          </div>
        )}
        {terminal && <details className="nested-panel" open={status === "EXECUTED"}><summary>Receipt: {status}</summary><pre className="json-block"><code>{JSON.stringify(call.result || {}, null, 2)}</code></pre><button type="button" className="secondary-button" disabled={Boolean(busyTool)} onClick={() => requestReadOnly(action)}>Request again</button></details>}
      </section>
    );
  };

  return (
    <>
    <section className="panel">
      <div className="tool-row-head"><Status value={coding.status} /><h2>Repository execution readiness</h2></div>
      <p className="muted small">Readiness is a prerequisite diagnostic. The controls below are separately approval-gated, read-only inspections; they do not run tests, containers, image pulls, or mutations.</p>
      <dl className="facts">
        <dt>Execution state</dt><dd>{coding.execution_status}</dd>
        <dt>Source HEAD</dt><dd className="mono">{coding.source?.head_sha || "UNKNOWN"}</dd>
        <dt>Container engine</dt><dd>{coding.runtime?.engine || coding.runtime?.engine_error?.status || "UNAVAILABLE"}</dd>
        <dt>Image</dt><dd className="mono">{coding.runtime?.image || coding.runtime?.image_error?.status || "REQUIRED"}</dd>
        <dt>Network</dt><dd>{coding.runtime?.network_allowed === false ? "DISABLED" : "UNVERIFIED"}</dd>
      </dl>
      {coding.blockers?.length > 0 && <p className="error-card" role="status">Blocked prerequisites: {coding.blockers.join(", ")}</p>}
    </section>
    <section className="panel" aria-labelledby="coding-inspection-title">
      <h2 id="coding-inspection-title">Governed read-only inspection</h2>
      <p className="muted small">Each request is recorded, bound to its exact arguments, and requires an explicit one-time approval before execution.</p>
      <div className="readiness-list">{readOnlyActions.map(actionCard)}</div>
      <section className="panel nested-panel" aria-label="Read repository file control">
        <div className="tool-row-head"><Status value={toolCalls["coding.read_file"]?.status || "READY"} /><strong>Read one repository file</strong></div>
        <p className="muted small">The path must be repository-relative and the response is byte-bounded with a SHA-256 receipt.</p>
        <label className="field">Repository-relative path<input value={filePath} onChange={(event) => setFilePath(event.target.value)} placeholder="README.md" /></label>
        {!toolCalls["coding.read_file"] && <button type="button" className="secondary-button" disabled={!repoRoot || !filePath.trim() || Boolean(busyTool)} onClick={requestFileRead}>Request file read</button>}
        {toolCalls["coding.read_file"]?.status === "PENDING_APPROVAL" && <div className="button-row"><button type="button" className="secondary-button" disabled={busyTool === "coding.read_file"} onClick={() => deny({ tool: "coding.read_file" })}>Deny</button><button type="button" className="primary-button" disabled={busyTool === "coding.read_file"} onClick={() => approveAndRun({ tool: "coding.read_file" })}>{busyTool === "coding.read_file" ? "Reading…" : "Allow once and read"}</button></div>}
        {toolCalls["coding.read_file"] && ["EXECUTED", "DENIED", "FAILED"].includes(toolCalls["coding.read_file"].status) && <details className="nested-panel" open={toolCalls["coding.read_file"].status === "EXECUTED"}><summary>Receipt: {toolCalls["coding.read_file"].status}</summary><pre className="json-block"><code>{JSON.stringify(toolCalls["coding.read_file"].result || {}, null, 2)}</code></pre><button type="button" className="secondary-button" disabled={Boolean(busyTool)} onClick={requestFileRead}>Request again</button></details>}
      </section>
    </section>
    </>
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

export function SpecializedWorkspace({ kind, announce }) {
  const [error, setError] = useState(null);
  const copy = COPY[kind];
  return (
    <section className="workspace" aria-labelledby={`${kind}-workspace-title`}>
      <header className="workspace-header">
        <h1 id={`${kind}-workspace-title`}>{copy.title}</h1>
        <p className="muted small">{copy.description}</p>
      </header>
      {error && <p className="error-card" role="alert">{error.status || "ERROR"} — {error.message || "The workspace could not load."}</p>}
      {kind === "gis" && <GISView setError={setError} announce={announce} />}
      {kind === "code" && <CodeView setError={setError} />}
      {kind === "artifacts" && <ArtifactsView setError={setError} />}
    </section>
  );
}
