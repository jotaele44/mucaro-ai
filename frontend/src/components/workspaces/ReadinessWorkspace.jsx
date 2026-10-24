import React, { useEffect, useState } from "react";
import { api } from "../../api.js";

function stateClass(state) {
  return state === "AVAILABLE" ? "completed" : state === "CANDIDATE" || state === "PARTIAL" ? "queued" : state === "ABSENT" ? "failed" : "pending";
}

// Readiness is an evidence display, not a second audit engine: it reports the backend's declared state and limitations.
export function ReadinessWorkspace() {
  const [descriptor, setDescriptor] = useState(null);
  const [offline, setOffline] = useState(null);
  const [security, setSecurity] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    Promise.all([api.intelligencePlane(), api.offlineManifest(), api.security()])
      .then(([plane, offlineManifest, securityReport]) => {
        setDescriptor(plane.descriptor);
        setOffline(offlineManifest);
        setSecurity(securityReport);
      })
      .catch(setError);
  }, []);

  const capabilities = Object.entries(descriptor?.capabilities || {});
  const offlineRows = offline?.capabilities || [];
  return (
    <section className="workspace" aria-labelledby="readiness-title">
      <header className="workspace-header">
        <h1 id="readiness-title">Readiness</h1>
        <p className="muted small">Declared capability state, offline boundaries, and security posture. Open or candidate states are not promoted to certified.</p>
      </header>
      {error && <p className="error-card" role="alert">{error.status || "ERROR"} — {error.message}</p>}
      {descriptor && (
        <div className="panel">
          <dl className="facts">
            <dt>Repository</dt><dd>{descriptor.repo_id}</dd>
            <dt>Membership</dt><dd>{descriptor.membership_status}</dd>
            <dt>Role</dt><dd>{descriptor.roles?.join(" · ")}</dd>
            <dt>Schema</dt><dd className="mono">{descriptor.schema}</dd>
          </dl>
        </div>
      )}
      <ul className="readiness-list">
        {capabilities.map(([name, capability]) => (
          <li key={name} className="panel readiness-row">
            <div className="goal-head">
              <div className="grow"><div className="tool-row-head"><span className={`status-chip status-${stateClass(capability.state)}`}>{capability.state}</span><strong>{name.replaceAll("_", " ")}</strong></div><p className="muted small">{capability.contract || "No contract declared"}</p></div>
            </div>
            <p>{capability.notes}</p>
          </li>
        ))}
      </ul>
      {offline && (
        <details className="panel" open>
          <summary>Offline capability boundary ({offlineRows.length})</summary>
          <ul className="offline-list">{offlineRows.map((row) => <li key={row.capability}><span className={`status-chip status-${row.state_now === "AVAILABLE_LOCALLY" ? "completed" : row.state_now === "REQUIRES_REMOTE_SERVICE" ? "queued" : "failed"}`}>{row.state_now}</span> {row.description}{row.local_dependency ? ` · ${row.local_dependency}` : ""}</li>)}</ul>
        </details>
      )}
      {security && (
        <details className="panel">
          <summary>Security posture</summary>
          <dl className="facts">{Object.entries(security).map(([key, value]) => <React.Fragment key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{typeof value === "object" ? JSON.stringify(value) : String(value)}</dd></React.Fragment>)}</dl>
        </details>
      )}
    </section>
  );
}
