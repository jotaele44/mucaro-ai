import React, { useEffect, useMemo, useState } from "react";
import { api } from "../../api.js";

// One governed settings home (TGT-033). Persistent preferences are backend-owned;
// the local access token remains a separate credential boundary and is never written through chat.
export function SettingsWorkspace({ options, preferences, onPreferencesChanged, announce }) {
  const [models, setModels] = useState([]);
  const [manifest, setManifest] = useState(null);
  const [config, setConfig] = useState(null);
  const [token, setToken] = useState(api.getOperatorToken());
  const [modelName, setModelName] = useState(options.model_name || "");
  const [busyKey, setBusyKey] = useState(null);
  const [error, setError] = useState(null);

  const values = useMemo(() => ({ ...options, ...(preferences?.values || {}) }), [options, preferences]);

  useEffect(() => {
    setModelName(values.model_name || "");
  }, [values.model_name]);

  useEffect(() => {
    Promise.all([api.models(), api.offlineManifest(), api.config()])
      .then(([m, o, c]) => {
        setModels(m.providers || []);
        setManifest(o);
        setConfig(c);
      })
      .catch(setError);
  }, []);

  async function savePreference(changes, label) {
    const key = Object.keys(changes).join(",");
    setBusyKey(key);
    setError(null);
    try {
      const payload = await api.updatePreferences(changes, preferences?.revision ?? null);
      onPreferencesChanged(payload);
      announce(label || "Settings updated.");
      return payload;
    } catch (err) {
      if (err.status === "ERROR_PREFERENCE_VERSION_CONFLICT") {
        try {
          onPreferencesChanged(await api.preferences());
        } catch {
          /* keep the original conflict visible */
        }
      }
      setError(err);
      throw err;
    } finally {
      setBusyKey(null);
    }
  }

  const byState = (state) => (manifest?.capabilities || []).filter((row) => row.state_now === state);
  return (
    <section className="workspace" aria-labelledby="settings-title">
      <header className="workspace-header">
        <h1 id="settings-title">Settings</h1>
        <p className="muted">The same persistent preferences are used by Settings and supported chat prompts.</p>
      </header>
      {error && <p className="error-card" role="alert">{error.status}: {error.message}</p>}

      <fieldset className="panel" id="settings-prompt-control">
        <legend>Prompt control</legend>
        <p className="muted small">
          You can change every managed preference from chat with direct requests such as{" "}
          <code>Set theme to dark</code>, <code>Use semantic retrieval</code>,{" "}
          <code>Use 10 retrieval results</code>, <code>Switch to local model</code>, or{" "}
          <code>Work offline only</code>.
        </p>
        <p className="muted small">
          Exact multi-setting form: <code>/settings theme=dark top_k=10 retrieval=hybrid</code>.
          Changes are validated, versioned, and written locally before Mucaro confirms them.
        </p>
        <p className="warning-inline small">
          Credentials are intentionally excluded from prompt control because prompts are retained in conversation history.
        </p>
      </fieldset>

      <fieldset className="panel" id="settings-appearance">
        <legend>Appearance</legend>
        <div className="appearance-options" role="radiogroup" aria-label="Theme">
          {[
            ["auto", "System"],
            ["light", "Light"],
            ["dark", "Dark"],
          ].map(([theme, label]) => (
            <label className="appearance-option" key={theme}>
              <input
                type="radio"
                name="mucaro-theme"
                value={theme}
                checked={(values.theme || "auto") === theme}
                disabled={Boolean(busyKey)}
                onChange={() => savePreference({ theme }, "Theme set to " + label.toLowerCase() + ".")}
              />
              <span>{label}</span>
            </label>
          ))}
        </div>
        <label className="switch">
          <input
            type="checkbox"
            checked={Boolean(values.reduced_motion)}
            disabled={Boolean(busyKey)}
            onChange={(event) => savePreference({ reduced_motion: event.target.checked }, "Motion preference updated.")}
          />
          <span>Reduce interface motion</span>
        </label>
      </fieldset>

      <fieldset className="panel" id="settings-model">
        <legend>Answer model</legend>
        {models.map((model) => (
          <label key={model.name} className="option">
            <input
              type="radio"
              name="model"
              value={model.name}
              checked={(values.model_provider || "mock") === model.name}
              disabled={Boolean(busyKey)}
              onChange={() => savePreference({ model_provider: model.name }, "Model provider set to " + model.name + ".")}
            />
            <span className="grow">
              <strong>{model.name}</strong>{" "}
              <span className="muted small">
                ({model.locality}{model.status !== "MODEL_PROVIDER_READY" ? " · " + model.status.replace("MODEL_PROVIDER_", "").toLowerCase() : ""})
              </span>
              <span className="muted small block">{model.description}</span>
              {model.warnings?.length > 0 && <span className="small block warning-inline">{model.warnings.join(", ")}</span>}
            </span>
          </label>
        ))}
        <form
          className="inline-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (modelName.trim()) savePreference({ model_name: modelName.trim() }, "Model name updated.");
          }}
        >
          <label className="field grow">
            <span>Model name</span>
            <input
              value={modelName}
              maxLength={120}
              disabled={Boolean(busyKey)}
              onChange={(event) => setModelName(event.target.value)}
            />
          </label>
          <button type="submit" className="secondary-button" disabled={Boolean(busyKey) || !modelName.trim()}>
            Save
          </button>
        </form>
        <label className="switch">
          <input
            type="checkbox"
            checked={Boolean(values.allow_live)}
            disabled={Boolean(busyKey) || Boolean(values.require_offline)}
            onChange={(event) => savePreference({ allow_live: event.target.checked }, "Remote-model permission updated.")}
          />
          <span>Allow sending a question and source excerpts to a selected remote model</span>
        </label>
        <label className="switch">
          <input
            type="checkbox"
            checked={Boolean(values.allow_mock_fallback)}
            disabled={Boolean(busyKey)}
            onChange={(event) => savePreference({ allow_mock_fallback: event.target.checked }, "Mock fallback preference updated.")}
          />
          <span>Allow deterministic mock fallback when a selected provider cannot answer</span>
        </label>
        <label className="switch">
          <input
            type="checkbox"
            checked={Boolean(values.require_offline)}
            disabled={Boolean(busyKey)}
            onChange={(event) => savePreference(
              event.target.checked ? { require_offline: true, allow_live: false } : { require_offline: false },
              event.target.checked ? "Offline-only mode enabled." : "Offline-only mode disabled.",
            )}
          />
          <span>Require offline operation</span>
        </label>
      </fieldset>

      <fieldset className="panel" id="settings-retrieval">
        <legend>Retrieval</legend>
        <label className="field">
          <span>Strategy</span>
          <select
            value={values.retrieval_strategy || "hybrid"}
            disabled={Boolean(busyKey)}
            onChange={(event) => savePreference({ retrieval_strategy: event.target.value }, "Retrieval strategy updated.")}
          >
            {["hybrid", "bm25", "persistent", "semantic", "lexical", "vector"].map((strategy) => (
              <option key={strategy} value={strategy}>{strategy}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Retrieved chunks (1–20)</span>
          <input
            type="number"
            min="1"
            max="20"
            value={values.top_k ?? 5}
            disabled={Boolean(busyKey)}
            onChange={(event) => {
              const value = Number(event.target.value);
              if (Number.isInteger(value) && value >= 1 && value <= 20) {
                savePreference({ top_k: value }, "Retrieved chunks set to " + value + ".");
              }
            }}
          />
        </label>
      </fieldset>

      <fieldset className="panel" id="settings-access">
        <legend>Local access token</legend>
        <p className="muted small">
          This credential is deliberately outside prompt-configurable preferences so it cannot be copied into a saved chat transcript.
        </p>
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

      {preferences && (
        <details className="panel">
          <summary>Preference receipt</summary>
          <dl className="facts">
            <dt>Revision</dt><dd>{preferences.revision}</dd>
            <dt>Values SHA-256</dt><dd className="mono">{preferences.values_sha256}</dd>
            <dt>Updated</dt><dd>{preferences.updated_at}</dd>
          </dl>
        </details>
      )}

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
