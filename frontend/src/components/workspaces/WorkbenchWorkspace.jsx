import React, { useState } from "react";
import { api } from "../../api.js";

const PLATFORM_OPTIONS = ["macos", "windows", "linux"];

export function WorkbenchWorkspace({ announce }) {
  const [objective, setObjective] = useState("");
  const [features, setFeatures] = useState("");
  const [sourceCommit, setSourceCommit] = useState("");
  const [profile, setProfile] = useState("maximum");
  const [network, setNetwork] = useState("isolated");
  const [hardware, setHardware] = useState("unknown");
  const [platforms, setPlatforms] = useState(["macos"]);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  function togglePlatform(platform) {
    setPlatforms((current) =>
      current.includes(platform) ? current.filter((item) => item !== platform) : [...current, platform],
    );
  }

  async function compile(event) {
    event.preventDefault();
    if (!objective.trim() || !platforms.length) return;
    setBusy(true);
    setError(null);
    try {
      const payload = await api.workbenchPlan({
        user_request: objective.trim(),
        source_commit: sourceCommit.trim() || null,
        features: features
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
        inputs_available: ["operator_ui"],
        profile,
        network,
        hardware,
        platforms,
      });
      setResult(payload);
      announce("Workbench plan compiled. Nothing was executed.");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const plan = result?.workbench_plan;
  const execution = result?.execution_plan;
  const gate = result?.gate;

  return (
    <section className="workspace" aria-labelledby="workbench-title">
      <header className="workspace-header">
        <h1 id="workbench-title">Workbench</h1>
        <p className="muted small">
          Compile a deterministic execution plan against Mucaro&apos;s existing gate. This surface never approves or executes actions.
        </p>
      </header>

      {error && <p className="error-card" role="alert">{error.status}: {error.message}</p>}

      <form className="panel" onSubmit={compile}>
        <label className="field">
          <span>Objective</span>
          <textarea
            value={objective}
            onChange={(event) => setObjective(event.target.value)}
            rows={5}
            maxLength={20000}
            placeholder="Describe the exact work to plan"
          />
        </label>

        <label className="field">
          <span>Capability scope</span>
          <input
            value={features}
            onChange={(event) => setFeatures(event.target.value)}
            placeholder="GIS, OCR, coding (comma-separated)"
            maxLength={4000}
          />
        </label>

        <label className="field">
          <span>Source commit (optional)</span>
          <input
            className="mono"
            value={sourceCommit}
            onChange={(event) => setSourceCommit(event.target.value)}
            placeholder="40-character Git SHA"
            inputMode="text"
          />
        </label>

        <div className="inline-form">
          <label className="field">
            <span>Profile</span>
            <select value={profile} onChange={(event) => setProfile(event.target.value)}>
              <option value="blockers">Blockers</option>
              <option value="extended">Extended</option>
              <option value="maximum">Maximum</option>
            </select>
          </label>
          <label className="field">
            <span>Network</span>
            <select value={network} onChange={(event) => setNetwork(event.target.value)}>
              <option value="isolated">Isolated</option>
              <option value="offline">Offline</option>
            </select>
          </label>
          <label className="field">
            <span>Local model hardware</span>
            <select value={hardware} onChange={(event) => setHardware(event.target.value)}>
              <option value="unknown">Unknown</option>
              <option value="available">Available</option>
              <option value="unavailable">Unavailable</option>
            </select>
          </label>
        </div>

        <fieldset className="panel">
          <legend>Platforms</legend>
          {PLATFORM_OPTIONS.map((platform) => (
            <label key={platform} className="switch">
              <input
                type="checkbox"
                checked={platforms.includes(platform)}
                onChange={() => togglePlatform(platform)}
              />
              <span>{platform}</span>
            </label>
          ))}
        </fieldset>

        <div className="workspace-actions">
          <button type="submit" className="primary-button" disabled={busy || !objective.trim() || !platforms.length}>
            {busy ? "Compiling…" : "Compile plan"}
          </button>
          <span className="muted small">Plan only · execution authorization remains false.</span>
        </div>
      </form>

      {result && (
        <div className="panel" aria-live="polite">
          <div className="tool-row-head">
            <span className="status-chip">{result.status}</span>
            <strong>Execution authority unchanged</strong>
          </div>
          <dl className="facts">
            <dt>Gate state</dt><dd>{gate?.state || "—"}</dd>
            <dt>Planning readiness</dt><dd>{gate?.readiness_percent ?? "—"}%</dd>
            <dt>Source identity</dt><dd>{result?.source_identity?.status || "—"}</dd>
            <dt>Observed source</dt><dd className="mono">{result?.source_identity?.observed_source_commit || "unavailable"}</dd>
            <dt>Source evidence</dt><dd>{result?.evidence_status?.source_commit?.state || "OPEN"}</dd>
            <dt>Plan state</dt><dd>{plan?.status || "—"}</dd>
            <dt>Certification</dt><dd>{plan?.certification || "—"}</dd>
            <dt>Execution profile</dt><dd>{execution?.profile || "—"}</dd>
            <dt>Lane catalog</dt><dd>{execution?.catalog_status || "—"}</dd>
            <dt>Blocked lanes</dt><dd>{execution?.blocked_lanes?.length ?? "—"} / {execution?.lanes?.length ?? "—"}</dd>
            <dt>Execution-plan state</dt><dd>{execution?.state || "—"}</dd>
            <dt>Plan SHA-256</dt><dd className="mono">{plan?.plan_sha256 || "—"}</dd>
          </dl>

          <p>
            <strong>Nothing was executed.</strong> Approval and run execution remain owned by the canonical execution gate and work-run runtime.
          </p>

          <details>
            <summary>Missing certification evidence ({result?.missing_certification_evidence?.length || 0})</summary>
            <ul className="warning-list">
              {(result?.missing_certification_evidence || []).map((item) => <li key={item}>{item}</li>)}
            </ul>
          </details>

          <details>
            <summary>Plan batches ({plan?.batches?.length || 0})</summary>
            <ol className="chunk-list">
              {(plan?.batches || []).map((batch) => (
                <li key={batch.id}>
                  <strong>{batch.id} · {batch.stage}</strong>
                  <p>{batch.objective}</p>
                  <span className="muted small">{batch.status}{batch.approval_required ? " · approval required" : ""}</span>
                </li>
              ))}
            </ol>
          </details>

          <details>
            <summary>Execution lanes ({execution?.lanes?.length || 0})</summary>
            <p className="muted small">
              {execution?.catalog_status === "UNBOUND"
                ? "T00–T19 are planning labels only until an authoritative catalog is bound; no lane is executable from this Workbench."
                : "Lane identities are bound to the declared catalog."}
            </p>
            <ul className="warning-list">
              {(execution?.lane_status || []).map(([lane, state]) => <li key={lane}><span className="mono">{lane}</span> · {state}</li>)}
            </ul>
          </details>
        </div>
      )}
    </section>
  );
}
