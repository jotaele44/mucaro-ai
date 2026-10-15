import React, { useEffect, useState } from "react";
import { api } from "../../api.js";

// Provenance (TGT-032): tool-action ledger with audit-chain verification, progressive disclosure.
export function ProvenanceWorkspace({ onNavigateToChat }) {
  const [summary, setSummary] = useState(null);
  const [calls, setCalls] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    Promise.all([api.toolSummary(), api.toolCalls()])
      .then(([s, c]) => {
        setSummary(s);
        setCalls((c.tool_calls || []).slice().reverse());
      })
      .catch(setError);
  }, []);

  const chainOk = summary?.audit_chain?.status === "TOOL_AUDIT_CHAIN_OK";
  return (
    <section className="workspace" aria-labelledby="prov-title">
      <header className="workspace-header">
        <h1 id="prov-title">Provenance</h1>
        <p className="muted small">Every action Mucaro requested, who decided, and what happened.</p>
      </header>
      {error && <p className="error-card" role="alert">{error.status}: {error.message}</p>}
      {summary && (
        <dl className="facts">
          <dt>Audit chain</dt>
          <dd className={chainOk ? "" : "error-text"}>{chainOk ? `Verified (${summary.audit_chain.chained_event_count} events)` : `${summary.audit_chain.status} ${summary.audit_chain.reason || ""}`}</dd>
          <dt>Actions</dt>
          <dd>
            {summary.total} total · {Object.entries(summary.by_status).filter(([, n]) => n).map(([state, n]) => `${n} ${state.toLowerCase()}`).join(" · ") || "none"}
          </dd>
        </dl>
      )}
      <ul className="tool-list">
        {calls.map((call) => (
          <li key={call.tool_call_id} className="tool-row">
            <div className="tool-row-head">
              <span className={`status-chip status-${call.status.toLowerCase()}`}>{call.status}</span>
              <strong>{call.tool_name}</strong>
              <span className="muted small">{call.created_at}</span>
            </div>
            <details>
              <summary>Record</summary>
              <dl className="facts">
                <dt>Requested by</dt><dd>{call.requested_by}</dd>
                <dt>Approved by</dt><dd>{call.approved_by || "—"}</dd>
                <dt>Denied by</dt><dd>{call.denied_by || "—"}</dd>
                <dt>Argument digest</dt><dd className="mono">{call.arguments_sha256 || "—"}</dd>
                <dt>Run</dt><dd className="mono">{call.run_id || "—"}</dd>
              </dl>
              <pre className="json-block"><code>{JSON.stringify({ arguments: call.arguments, result: call.result }, null, 2)}</code></pre>
            </details>
          </li>
        ))}
      </ul>
      {!calls.length && !error && (
        <div className="empty-state workspace-empty-state">
          <h2>No tool actions recorded</h2>
          <p className="muted">This ledger appears when a chat requests a tool action and records the request, decision, and outcome. Ordinary answers do not fabricate provenance rows.</p>
          <button type="button" className="secondary-button" onClick={onNavigateToChat}>Go to Chat</button>
        </div>
      )}
    </section>
  );
}
