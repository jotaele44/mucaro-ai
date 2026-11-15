import React, { useEffect, useState } from "react";
import { api } from "../../api.js";
import { CleartextNotice } from "../CleartextNotice.jsx";
import { ModelSetup, modelOf } from "../ModelSetup.jsx";

// One settings home (TGT-033): model, retrieval, access token and offline capability view.
export function SettingsWorkspace({ options, onOptionsChange, announce }) {
  const [manifest, setManifest] = useState(null);
  const [config, setConfig] = useState(null);
  const [token, setToken] = useState(api.getOperatorToken());
  const [error, setError] = useState(null);

  useEffect(() => {
    Promise.all([api.offlineManifest(), api.config()])
      .then(([o, c]) => {
        setManifest(o);
        setConfig(c);
      })
      .catch(setError);
  }, []);

  const byState = (state) => (manifest?.capabilities || []).filter((row) => row.state_now === state);
  return (
    <section className="workspace" aria-labelledby="settings-title">
      <header className="workspace-header">
        <h1 id="settings-title">Settings</h1>
      </header>
      {error && <p className="error-card" role="alert">{error.status}: {error.message}</p>}

      <ModelSetup
        legend="Default model for new chats"
        value={modelOf(options)}
        onChange={(model) => onOptionsChange({ ...options, ...model })}
        announce={announce}
      />
      <p className="muted small">Each chat keeps the model it was started with; change it from the chat's Model button.</p>

      <fieldset className="panel">
        <legend>Retrieval</legend>
        <label className="field">
          <span>Strategy</span>
          <select value={options.retrieval_strategy} onChange={(event) => onOptionsChange({ ...options, retrieval_strategy: event.target.value })}>
            {["hybrid", "bm25", "persistent", "semantic", "lexical", "vector"].map((strategy) => <option key={strategy} value={strategy}>{strategy}</option>)}
          </select>
        </label>
      </fieldset>

      <fieldset className="panel">
        <legend>Local access token</legend>
        <CleartextNotice />
        {api.tokenIsLaunchManaged() ? (
          <p className="muted small">The Mucaro app created a fresh token for this launch and uses it automatically. Nothing to set here.</p>
        ) : (
          <>
            <p className="muted small">{config?.auth?.auth_required ? "This Mucaro service requires a token." : "No token is required by this Mucaro service."}</p>
            <form
              className="inline-form"
              onSubmit={(event) => {
                event.preventDefault();
                api.setOperatorToken(token, { remember: true });
                announce("Token saved for this device.");
              }}
            >
              <label className="field grow">
                <span>Token</span>
                <input type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} />
              </label>
              <button type="submit" className="secondary-button">Save</button>
            </form>
          </>
        )}
      </fieldset>

      {manifest && (
        <details className="panel" open>
          <summary>What works offline</summary>
          <ul className="offline-list">
            {byState("AVAILABLE_LOCALLY").map((row) => <li key={row.capability}><span className="status-chip status-completed">Local</span> {row.description}</li>)}
            {byState("REQUIRES_REMOTE_SERVICE").map((row) => <li key={row.capability}><span className="status-chip status-queued">Needs network</span> {row.description}</li>)}
            {byState("LOCAL_DEPENDENCY_MISSING").map((row) => <li key={row.capability}><span className="status-chip status-failed">Unavailable</span> {row.description} — missing {row.local_dependency}</li>)}
          </ul>
        </details>
      )}
    </section>
  );
}
