import React, { useEffect, useState } from "react";
import { api } from "../../api.js";
import { CleartextNotice } from "../CleartextNotice.jsx";

// Claude connection: the key is write-only here (the server never sends it back), and nothing is sent to
// Anthropic until the person turns on the consent switch.
function ClaudePanel({ claude, onClaudeChange, announce }) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);

  async function run(action, success) {
    setBusy(true);
    setMessage(null);
    try {
      const result = await action();
      if (result?.status?.startsWith("CLAUDE_") && result.models) onClaudeChange(result);
      else onClaudeChange(await api.claude());
      const text = typeof success === "function" ? success(result) : success;
      setMessage({ ok: true, text });
      announce(text);
      return true;
    } catch (err) {
      setMessage({ ok: false, text: err.message || String(err.status) });
      return false;
    } finally {
      setBusy(false);
    }
  }

  if (!claude) return null;
  const model = claude.models.find((row) => row.id === claude.model) || claude.models[0];
  return (
    <fieldset className="panel claude-panel">
      <legend>Claude</legend>
      <p className={claude.ready ? "status-ok" : "muted"} role="status">
        {claude.ready ? `Connected · ${model.label}` : claude.detail}
      </p>

      <form
        className="inline-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (!key.trim()) return;
          run(() => api.saveClaudeKey(key.trim()), (result) => `API key saved${result?.stored_in === "keychain" ? " in your Keychain" : ""}.`).then((ok) => ok && setKey(""));
        }}
      >
        <label className="field grow">
          <span>Anthropic API key {claude.key_present && <span className="muted small">(saved: {claude.key_hint}{claude.key_source === "env" ? ", from the environment" : ""})</span>}</span>
          <input type="password" autoComplete="off" spellCheck="false" placeholder={claude.key_present ? "Paste a new key to replace it" : "sk-ant-api…"}
            value={key} onChange={(event) => setKey(event.target.value)} disabled={busy} />
        </label>
        <button type="submit" className="secondary-button" disabled={busy || !key.trim()}>Save</button>
      </form>
      <p className="muted small">
        Create a key at console.anthropic.com (a Claude Pro or Max subscription does not include API use). Usage is billed by
        Anthropic to that account; each answer shows its approximate cost.
      </p>
      <div className="button-row">
        <button type="button" className="ghost-button" disabled={busy || !claude.key_present} onClick={() => run(api.testClaude, (result) => result.detail)}>
          Test connection
        </button>
        {claude.key_present && claude.key_source !== "env" && (
          <button type="button" className="ghost-button" disabled={busy} onClick={() => run(api.removeClaudeKey, "API key removed.")}>
            Remove key
          </button>
        )}
      </div>

      <label className="switch">
        <input type="checkbox" checked={Boolean(claude.live_consent)} disabled={busy}
          onChange={(event) => run(() => api.updateClaude({ live_consent: event.target.checked }), event.target.checked ? "Claude turned on." : "Claude turned off.")} />
        <span>Send my messages to Anthropic so Claude can answer them (with the documents, files and web pages it reads)</span>
      </label>

      <label className="field">
        <span>Model</span>
        <select value={claude.model} disabled={busy} onChange={(event) => run(() => api.updateClaude({ model: event.target.value }), "Model changed.")}>
          {claude.models.map((row) => (
            <option key={row.id} value={row.id}>
              {row.label} — ${row.pricing.input} / ${row.pricing.output} per million tokens
            </option>
          ))}
        </select>
        <span className="muted small">{model.summary}</span>
      </label>
      {model.efforts.length > 0 && (
        <label className="field">
          <span>Effort</span>
          <select value={claude.effort} disabled={busy} onChange={(event) => run(() => api.updateClaude({ effort: event.target.value }), "Effort changed.")}>
            {model.efforts.map((effort) => <option key={effort} value={effort}>{effort}</option>)}
          </select>
          <span className="muted small">Higher effort thinks longer and costs more; medium suits most questions.</span>
        </label>
      )}
      <label className="switch">
        <input type="checkbox" checked={Boolean(claude.web_tools)} disabled={busy}
          onChange={(event) => run(() => api.updateClaude({ web_tools: event.target.checked }), "Saved. Applies to new chats.")} />
        <span>Web search and page reading (new chats)</span>
      </label>
      <label className="switch">
        <input type="checkbox" checked={Boolean(claude.code_sandbox)} disabled={busy}
          onChange={(event) => run(() => api.updateClaude({ code_sandbox: event.target.checked }), "Saved. Applies to new chats.")} />
        <span>Code sandbox for calculations and data analysis (new chats)</span>
      </label>
      {message && <p className={message.ok ? "status-ok small" : "error-text"} role={message.ok ? "status" : "alert"}>{message.text}</p>}
    </fieldset>
  );
}

// One settings home (TGT-033): model, retrieval, access token and offline capability view.
export function SettingsWorkspace({ options, onOptionsChange, announce, claude = null, onClaudeChange = () => {} }) {
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

      <ClaudePanel claude={claude} onClaudeChange={onClaudeChange} announce={announce} />

      <fieldset className="panel">
        <legend>Offline answers (when Claude is off)</legend>
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
