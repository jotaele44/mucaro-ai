import React, { useCallback, useEffect, useId, useRef, useState } from "react";
import { api } from "../api.js";

const LOCAL = "openai_compatible_local";
const REMOTE = "openai_compatible_remote";

// Friendly names for the registered providers; the provider id stays visible so it matches logs and the CLI.
const CHOICES = {
  [LOCAL]: { label: "Model on this computer", hint: "Any model served by Ollama, LM Studio or llama.cpp here. Nothing leaves this device." },
  [REMOTE]: { label: "Model on another server", hint: "Any OpenAI-compatible server on your network or the internet (vLLM, a remote Ollama, a hosted open-model endpoint)." },
  anthropic: { label: "Claude (Anthropic)", hint: "Needs live mode and an API key set where Mucaro runs." },
  mock: { label: "No model (quote sources only)", hint: "Built-in offline answerer. It can only quote matching sources; it cannot hold a conversation." },
};
const ORDER = [LOCAL, REMOTE, "anthropic", "mock"];

const REASONS = {
  LOCAL_MODEL_SERVER_UNREACHABLE: "No model server is answering at this address. Start Ollama, LM Studio or llama.cpp, or correct the address.",
  LOCAL_MODEL_URL_NOT_LOOPBACK: "This address is not on this computer. Choose “Model on another server” for it.",
  LOCAL_SERVER_LISTS_NO_MODELS: "The server is running but has no model installed. Pull or load a model first.",
  REMOTE_MODEL_URL_NOT_SET: "Enter the server address, then choose Detect models.",
  REMOTE_MODEL_URL_INVALID: "The server address is not a valid http(s) address.",
  REMOTE_MODEL_SERVER_UNREACHABLE: "No model server is answering at this address.",
  REMOTE_MODEL_SERVER_REJECTED_KEY: "The server rejected the API key.",
  REMOTE_SERVER_LISTS_NO_MODELS: "The server lists no models. Type the model name.",
  REMOTE_MODEL_CLEARTEXT_HTTP: "This address uses plain http: the API key and your content are not encrypted on the network.",
  LIVE_MODE_DISABLED: "Live mode is off where Mucaro runs (MUCARO_AI_LIVE_MODE=1 turns it on).",
};

function reasonText(code) {
  const [name, ...rest] = String(code).split(":");
  const value = rest.join(":");
  if (name.endsWith("_MODEL_NOT_INSTALLED")) return `The model “${value}” is not on this server.`;
  if (name === "API_KEY_MISSING") return `The API key ${value} is not set where Mucaro runs.`;
  if (name === "OPTIONAL_DEPENDENCY_MISSING") return `The Python package ${value} is not installed.`;
  if (name.startsWith("MODEL_INVOKE_PERMISSION")) return "This Mucaro profile does not allow calling a model.";
  return REASONS[name] || code;
}

/**
 * Choose the model for a chat: provider, server address, model and (for remote servers) key and consent.
 * `value` is { model_provider, model_name, allow_live, agent }; server address and key are saved on the device
 * that runs Mucaro, the model choice belongs to the chat.
 */
export function ModelSetup({ value, onChange, announce = () => {}, legend = "Model for this chat" }) {
  const [providers, setProviders] = useState(null);
  const [urls, setUrls] = useState({ [LOCAL]: "", [REMOTE]: "" });
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const id = useId();
  const alive = useRef(true);

  const apply = useCallback((payload, { keepUrls = false } = {}) => {
    if (!alive.current) return;
    setProviders(payload.providers || []);
    if (!keepUrls && payload.settings) {
      setUrls({ [LOCAL]: payload.settings.local?.base_url || "", [REMOTE]: payload.settings.remote?.base_url || "" });
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    api.models().then(apply).catch((err) => alive.current && setError(err));
    return () => {
      alive.current = false;
    };
  }, [apply]);

  const provider = value.model_provider || "mock";
  const row = (providers || []).find((item) => item.name === provider);
  const server = row?.local_server || null;
  const installed = server?.installed_models || [];
  const isServer = provider === LOCAL || provider === REMOTE;
  const remote = row?.locality === "remote";
  const chosen = value.model_name || "";
  const known = !chosen || installed.some((name) => name.toLowerCase() === chosen.toLowerCase());

  function choose(name) {
    const next = (providers || []).find((item) => item.name === name);
    // A model name belongs to one server, and consent to send content never carries over to another provider.
    onChange({
      model_provider: name,
      model_name: null,
      allow_live: next?.locality === "remote" ? Boolean(value.allow_live) && name === provider : false,
      agent: canUseTools(name),
    });
    setError(null);
  }

  async function detect(event) {
    event?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const section = provider === LOCAL ? "local" : "remote";
      const update = { base_url: urls[provider] };
      if (provider === REMOTE && apiKey) update.api_key = apiKey;
      const saved = await api.saveModelSettings({ [section]: update });
      apply(saved);
      setApiKey("");
      const fresh = (saved.providers || []).find((item) => item.name === provider);
      const names = fresh?.local_server?.installed_models || [];
      if (chosen && !names.some((name) => name.toLowerCase() === chosen.toLowerCase())) onChange({ ...value, model_name: null });
      announce(fresh?.local_server?.reachable ? `Found ${names.length} model${names.length === 1 ? "" : "s"}.` : "The model server did not answer.");
    } catch (err) {
      setError(err);
    } finally {
      if (alive.current) setBusy(false);
    }
  }

  let state = null;
  if (row) {
    const blocking = (row.warnings || []).filter((code) => !/_MODEL_AUTO_SELECTED:|_CLEARTEXT_HTTP$|_MODEL_NOT_INSTALLED:/.test(code));
    if (isServer && server?.reachable && (chosen ? known : installed.length > 0)) {
      state = { ok: true, text: `Ready · ${chosen || installed[0]}` };
    } else if (isServer && server?.reachable && chosen && !known) {
      state = { ok: false, text: `The model “${chosen}” is not on this server.` };
    } else if (row.status === "MODEL_PROVIDER_READY") {
      state = { ok: true, text: provider === "mock" ? "Ready · no language model" : `Ready · ${row.resolved_model || row.default_model}` };
    } else {
      state = { ok: false, text: (blocking.length ? blocking : row.warnings || []).map(reasonText).join(" ") || "Not ready." };
    }
    if (state.ok && remote && !value.allow_live) state = { ok: false, text: "Allow sending content to this server below to use it." };
  }

  return (
    <fieldset className="panel model-setup">
      <legend>{legend}</legend>
      {error && <p className="error-card" role="alert">{error.status}: {error.message}</p>}
      {!providers && !error && <p className="muted small">Checking available models…</p>}
      {providers && (
        <div className="model-choices" role="radiogroup" aria-label="Model provider">
          {ORDER.filter((name) => providers.some((item) => item.name === name)).map((name) => (
            <label key={name} className={`option ${provider === name ? "selected" : ""}`}>
              <input type="radio" name={`${id}-provider`} value={name} checked={provider === name} onChange={() => choose(name)} />
              <span className="grow">
                <strong>{CHOICES[name]?.label || name}</strong> <span className="muted small">({name})</span>
                <span className="muted small block">{CHOICES[name]?.hint}</span>
              </span>
            </label>
          ))}
        </div>
      )}

      {providers && isServer && (
        <form className="model-server" onSubmit={detect}>
          <label className="field grow">
            <span>Server address</span>
            <input
              type="text"
              inputMode="url"
              autoComplete="off"
              spellCheck={false}
              placeholder={provider === LOCAL ? "http://127.0.0.1:11434/v1" : "https://models.example.com/v1"}
              value={urls[provider]}
              onChange={(event) => setUrls({ ...urls, [provider]: event.target.value })}
            />
          </label>
          {provider === REMOTE && (
            <label className="field grow">
              <span>API key {server?.api_key_configured ? "(saved — leave blank to keep)" : "(optional)"}</span>
              <input type="password" autoComplete="off" value={apiKey} onChange={(event) => setApiKey(event.target.value)} />
            </label>
          )}
          <button type="submit" className="secondary-button" disabled={busy || !urls[provider].trim()}>
            {busy ? "Checking…" : "Detect models"}
          </button>
        </form>
      )}

      {providers && isServer && (
        <label className="field">
          <span>Model</span>
          {installed.length > 0 ? (
            <select value={known ? installed.find((name) => name.toLowerCase() === chosen.toLowerCase()) || "" : "__other"} onChange={(event) => onChange({ ...value, model_name: event.target.value || null })}>
              <option value="">Automatic — {installed[0]}</option>
              {installed.map((name) => <option key={name} value={name}>{name}</option>)}
              {!known && <option value="__other" disabled>{chosen} (not on this server)</option>}
            </select>
          ) : (
            <input
              type="text"
              autoComplete="off"
              spellCheck={false}
              placeholder="Model name, for example llama3.1:8b"
              value={chosen}
              onChange={(event) => onChange({ ...value, model_name: event.target.value || null })}
            />
          )}
        </label>
      )}

      {providers && remote && (
        <label className="switch">
          <input type="checkbox" checked={Boolean(value.allow_live)} onChange={(event) => onChange({ ...value, allow_live: event.target.checked })} />
          <span>Allow sending my questions and source excerpts to this server</span>
        </label>
      )}

      {providers && isServer && (
        <label className="switch">
          <input type="checkbox" checked={Boolean(value.agent)} onChange={(event) => onChange({ ...value, agent: event.target.checked })} />
          <span>Let the model propose actions (search the web, fetch a page, read a repository). Each one asks you first.</span>
        </label>
      )}

      {state && (
        <p className="model-state small" role="status">
          <span className={`status-chip ${state.ok ? "status-completed" : "status-failed"}`}>{state.ok ? "Ready" : "Not ready"}</span>{" "}
          {state.ok ? state.text.replace(/^Ready · /, "") : state.text}
        </p>
      )}
      {row?.warnings?.includes("REMOTE_MODEL_CLEARTEXT_HTTP") && <p className="small warning-inline">{REASONS.REMOTE_MODEL_CLEARTEXT_HTTP}</p>}
    </fieldset>
  );
}

/** Providers whose models can propose tool calls. */
export const canUseTools = (provider) => provider === LOCAL || provider === REMOTE;

/** A chat's model choice from saved options; proposing actions is on for model servers unless it was turned off. */
export const modelOf = (options) => {
  const provider = options?.model_provider || "mock";
  return {
    model_provider: provider,
    model_name: options?.model_name || null,
    allow_live: Boolean(options?.allow_live),
    agent: canUseTools(provider) && options?.agent !== false,
  };
};

/** Short label for a chat's model, for headers and receipts. */
export function modelLabel(model) {
  if (!model?.model_provider) return null;
  if (model.model_provider === "mock") return "no model (quotes sources)";
  const kind = model.model_provider === LOCAL ? "local" : model.model_provider === REMOTE ? "remote server" : model.model_provider;
  return model.model_name ? `${model.model_name} (${kind})` : `automatic (${kind})`;
}
