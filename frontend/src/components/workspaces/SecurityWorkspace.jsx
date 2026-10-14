import React, { useState } from "react";
import { api } from "../../api.js";

// Static candidates only. Do not imply full security verification.
export function SecurityWorkspace() {
  const [path, setPath] = useState("");
  const [ready, setReady] = useState(null);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState(null);

  async function validateTarget() {
    setBusy("validate"); setError(null); setReady(null); setResult(null);
    try { setReady(await api.securityScanPreflight({ path: path.trim() })); }
    catch (err) { setError(err); }
    finally { setBusy(""); }
  }

  async function scan() {
    if (!ready) return;
    setBusy("scan"); setError(null); setResult(null);
    try { setResult(await api.securityScanRun({ path: ready.repo_root, expected_head: ready.head_sha })); }
    catch (err) { setError(err); setReady(null); }
    finally { setBusy(""); }
  }

  return (
    <section className="workspace" aria-labelledby="security-scan-title">
      <header className="workspace-header"><h1 id="security-scan-title">Security Scan</h1></header>
      <p className="muted">Local-only static candidate discovery. Requires directory access and a clean Git checkout. A candidate is not a confirmed vulnerability.</p>
      <div className="panel">
        <label className="field">
          <span>Repository path on this computer</span>
          <input type="text" placeholder="~/Projects/example-repository" value={path}
            onChange={(event) => { setPath(event.target.value); setReady(null); setResult(null); setError(null); }} />
        </label>
        <div className="workspace-actions">
          <button type="button" className="secondary-button" disabled={!!busy || !path.trim()} onClick={validateTarget}>{busy === "validate" ? "Validating…" : "Validate target"}</button>
          <button type="button" className="primary-button" disabled={!!busy || !ready} onClick={scan}>{busy === "scan" ? "Scanning…" : "Start scan"}</button>
        </div>
        {error && <p role="alert" className="error-card">{error.status || "ERROR"}: {error.message || String(error)}
          <button type="button" className="secondary-button" disabled={!!busy} onClick={validateTarget}>Retry</button></p>}
        {ready && <p role="status">Validated {ready.tracked_file_count} tracked files · HEAD <code>{ready.head_sha}</code></p>}
        {result && <div role="region" aria-label="Security scan result">
          <h2>Result: {result.status}</h2>
          <p>{result.counts.SCANNED} files examined · {result.excluded_count} excluded · {result.finding_count} candidate matches.</p>
          <ul className="source-list">
            {result.findings.map((row, i) =>
              <li key={i}><strong>{row.rule}</strong> · {row.severity} · <code>{row.path}:{row.line}</code> · candidate only</li>)}
            {result.finding_count === 0 && <li>No configured patterns matched scanned files. This is not a security certificate.</li>}
          </ul>
          <details className="panel">
            <summary>Boundaries, counts and limitations</summary>
            <p>HEAD <code>{result.head_sha}</code></p>
            <pre>{JSON.stringify(result.counts, null, 2)}</pre>
            <ul>{result.limitations.map((limit) => <li key={limit}>{limit}</li>)}</ul>
          </details>
        </div>}
      </div>
    </section>
  );
}
