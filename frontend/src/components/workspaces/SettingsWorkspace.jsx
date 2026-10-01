import React, { useEffect, useState } from "react";
import { api } from "../../api.js";

// One settings home (TGT-033): model, retrieval, access token and offline capability view.
export function SettingsWorkspace({ options, onOptionsChange, announce }) {
  const [models, setModels] = useState([]);
  const [manifest, setManifest] = useState(null);
  const [config, setConfig] = useState(null);
  const [token, setToken] = useState(api.getOperatorToken());
  const [error, setError] = useState(null);

  useEffect(() => {
    Promise.all([api.models(), api.offlineManifest(), api.config()])
      .then(([m, o, c]) => {
        setModels(m.providers || []);
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

      <fieldset className="panel">
        <legend>Answer model</legend>
        {models.map((model) => (
          <label key={model.name} className="option">
            <input
              type="radio"
              name="model"
              value={model.name}
              checked={(options.model_provider || "mock") === model.name}
              onChange={() => onOptionsChange({ ...options, model_provider: model.name, allow_live: model.locality === "remote" ? options.allow_live : false })}
            />
            <span className="grow">
              <strong>{model.name}</strong> <span className="muted small">({model.locality}{model.status !== "MODEL_PROVIDER_READY" ? ` · ${model.status.replace("MODEL_PROVIDER_", "").toLowerCase()}` : ""})</span>
              <span className="muted small block">{model.description}</span>
              {model.warnings?.length > 0 && <span className="small block warning-inline">{model.warnings.join(", ")}</span>}
            </span>
          </label>
        ))}
        <label className="switch">
          <input type="checkbox" checked={Boolean(options.allow_live)} onChange={(event) => onOptionsChange({ ...options, allow_live: event.target.checked })} />
          <span>Allow sending question and source excerpts to a remote model when one is selected</span>
        </label>
      </fieldset>

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
        {api.tokenIsLaunchManaged() ? (
          <p className="muted small">The Mucaro app created a fresh token for this launch and uses it automatically. Nothing to set here.</p>
        ) : (
          <>
            <p className="muted small">{config?.auth?.auth_required ? "This Mucaro service requires a token." : "No token is required by this Mucaro service."}</p>
            <form
              className="inline-form"
              onSubmit={(event) => {
                event.preventDefault();
                api.setOperatorToken(token);
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
