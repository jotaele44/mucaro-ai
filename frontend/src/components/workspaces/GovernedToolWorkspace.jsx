import React, { useCallback, useEffect, useState } from "react";
import { api } from "../../api.js";
import { ToolResult } from "../results/ToolResult.jsx";

// A specialized workspace over governed tools (TGT-003). It shows the calls of its tools with purpose-built result
// views and lets the person request a new one. A request goes through the same gate as any action: it is created
// waiting for approval, approval binds to the exact arguments shown, and it runs only when the person presses Run.
// The conversation stays the orchestration plane; this is a focused place to inspect and repeat these actions.
export function GovernedToolWorkspace({ id, title, intro, tools, request, announce, emptyText }) {
  const [calls, setCalls] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const [values, setValues] = useState(() => Object.fromEntries((request?.fields || []).map((field) => [field.name, ""])));

  const load = useCallback(() => {
    api
      .toolCalls()
      .then((payload) => setCalls((payload.tool_calls || []).filter((call) => tools.includes(call.tool_name)).reverse()))
      .catch(setError)
      .finally(() => setLoaded(true));
  }, [tools]);

  useEffect(load, [load]);

  async function act(label, work) {
    setBusy(label);
    setError(null);
    try {
      await work();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
      load();
    }
  }

  const submit = (event) => {
    event.preventDefault();
    act("request", async () => {
      const args = request.build(values);
      const created = await api.requestTool(request.tool, args, `Requested from the ${title} workspace`);
      announce?.(`Requested ${request.tool}; it waits for your approval.`);
      return created;
    });
  };
  const valid = (request?.fields || []).every((field) => !field.required || String(values[field.name] || "").trim());

  return (
    <section className="workspace" aria-labelledby={`${id}-title`} data-testid={`${id}-workspace`} data-loaded={loaded ? "true" : "false"}>
      <header className="workspace-header">
        <h1 id={`${id}-title`}>{title}</h1>
        <p className="muted small">{intro}</p>
      </header>
      {error && <p className="error-card" role="alert">{error.status}: {error.message}</p>}

      {request && (
        <form className="panel request-form" onSubmit={submit} aria-label={request.label}>
          {request.fields.map((field) => (
            <label key={field.name} className="field">
              <span>{field.label}</span>
              <input
                value={values[field.name]}
                placeholder={field.placeholder}
                onChange={(event) => setValues({ ...values, [field.name]: event.target.value })}
                autoComplete="off"
                spellCheck={false}
              />
            </label>
          ))}
          <button type="submit" className="secondary-button" disabled={!valid || busy !== null}>
            {busy === "request" ? "Requesting…" : request.label}
          </button>
          <p className="muted small">{request.hint}</p>
        </form>
      )}

      <ul className="tool-list" aria-label={`${title} actions`}>
        {calls.map((call) => (
          <li key={call.tool_call_id} className="tool-row" data-testid="workspace-call">
            <div className="tool-row-head">
              <span className={`status-chip status-${String(call.status || "").toLowerCase()}`}>{call.status}</span>
              <strong>{call.tool_name}</strong>
              <span className="muted small">{call.arguments?.path || call.arguments?.repo_root || call.arguments?.repo || ""}</span>
            </div>
            {call.status === "PENDING_APPROVAL" && (
              <div className="call-actions">
                <span className="muted small">Arguments digest {String(call.arguments_sha256 || "").slice(0, 12)}…</span>
                <button
                  type="button"
                  className="primary-button"
                  disabled={busy !== null}
                  onClick={() => act(call.tool_call_id, async () => {
                    await api.approveTool(call.tool_call_id, call.arguments_sha256);
                    announce?.(`Allowed ${call.tool_name}. Press Run to start it.`);
                  })}
                >
                  Allow
                </button>
                <button
                  type="button"
                  className="secondary-button"
                  disabled={busy !== null}
                  onClick={() => act(call.tool_call_id, () => api.denyTool(call.tool_call_id, call.arguments_sha256))}
                >
                  Deny
                </button>
              </div>
            )}
            {call.status === "APPROVED" && !call.run_id && (
              <div className="call-actions">
                <button
                  type="button"
                  className="primary-button"
                  disabled={busy !== null}
                  onClick={() => act(call.tool_call_id, async () => {
                    await api.runTool(call.tool_call_id);
                    announce?.(`Ran ${call.tool_name}.`);
                  })}
                >
                  {busy === call.tool_call_id ? "Running…" : "Run"}
                </button>
              </div>
            )}
            {call.status === "FAILED" && call.result?.status && (
              <p className="error-text">{call.result.status}: {call.result.detail || ""}</p>
            )}
            {call.status === "EXECUTED" && call.result && <ToolResult toolName={call.tool_name} result={call.result} status={call.result.status} />}
          </li>
        ))}
      </ul>
      {!calls.length && !error && <p className="empty-state muted">{emptyText}</p>}
    </section>
  );
}
