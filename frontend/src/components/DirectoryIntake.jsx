import React, { useEffect, useState } from "react";
import { api } from "../api.js";

function errorDetail(err) {
  const detail = err?.payload?.detail;
  if (detail && typeof detail === "object") return detail;
  return { status: err?.status || "ERROR", detail: err?.message || "Request failed." };
}

export function DirectoryIntake({ onIngested }) {
  const [path, setPath] = useState("");
  const [reason, setReason] = useState("local source intake");
  const [grants, setGrants] = useState([]);
  const [permission, setPermission] = useState(null);
  const [pendingGrant, setPendingGrant] = useState(null);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function refreshGrants() {
    const payload = await api.directoryPermissions();
    setGrants(payload.grants || []);
  }

  useEffect(() => { refreshGrants().catch((err) => setError(err.message)); }, []);

  async function ingestCheckedPath(targetPath) {
    const payload = await api.ingestPath({ path: targetPath });
    setResult(payload);
    if (payload.directory_permission) setPermission(payload.directory_permission);
    if (onIngested) await onIngested();
  }

  async function checkAndIngest() {
    const target = path.trim();
    if (!target || busy) return;
    setBusy(true);
    setError(null);
    setResult(null);
    setPendingGrant(null);
    try {
      const payload = await api.checkDirectoryPermission({ path: target, reason });
      setPermission(payload);
      if (payload.status === "ERROR_DIRECTORY_PERMISSION_REQUIRED") {
        setPendingGrant({ path: target, canonicalRoot: payload.canonical_root, reason });
        return;
      }
      await ingestCheckedPath(target);
    } catch (err) {
      const detail = errorDetail(err);
      setPermission(detail);
      setError(detail.detail || err.message);
    } finally {
      setBusy(false);
    }
  }

  async function grantAndIngest() {
    const target = pendingGrant?.path;
    if (!target || busy) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const grant = await api.grantDirectoryPermission({ path: target, reason });
      setPermission({
        status: "PERMISSION_OK",
        scope: "granted_directory",
        canonical_root: grant.canonical_root,
        grant,
      });
      setPendingGrant(null);
      await refreshGrants();
      await ingestCheckedPath(target);
    } catch (err) {
      const detail = errorDetail(err);
      setError(detail.detail || err.message);
    } finally {
      setBusy(false);
    }
  }

  async function revokeGrant(grant) {
    setBusy(true);
    setError(null);
    try {
      await api.revokeDirectoryPermission({ path: grant.canonical_root, reason: "operator revoke" });
      await refreshGrants();
      if (permission?.canonical_root === grant.canonical_root) setPermission(null);
    } catch (err) {
      const detail = errorDetail(err);
      setError(detail.detail || err.message);
    } finally {
      setBusy(false);
    }
  }

  const permissionStatus = permission?.status || "NOT_CHECKED";
  const canContinue = path.trim().length > 0 && !busy;

  return (
    <section className="panel directory-intake">
      <div className="directory-access-heading">
        <div>
          <h2>Directory Access</h2>
          <p className="hint">Choose one folder on the computer running Mucaro. The same flow checks access, requests a grant only when needed, then ingests the folder.</p>
        </div>
        <span className="badge" aria-label={`Directory access status: ${permissionStatus}`}>{permissionStatus}</span>
      </div>

      <div className="path-form">
        <label>
          Folder path on the host computer
          <input
            value={path}
            onChange={(event) => {
              setPath(event.target.value);
              setPermission(null);
              setPendingGrant(null);
              setResult(null);
            }}
            placeholder="~/Documents/mucaro-sources"
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
          />
        </label>
        <label>
          Reason
          <input value={reason} onChange={(event) => setReason(event.target.value)} />
        </label>
      </div>

      {permission && (
        <div className="directory-access-summary" aria-live="polite">
          <span><strong>Status</strong> {permission.status}</span>
          <span><strong>Scope</strong> {permission.scope || "none"}</span>
          <span><strong>Root</strong> <code>{permission.canonical_root || "unresolved"}</code></span>
        </div>
      )}

      <div className="button-row directory-primary-action">
        <button type="button" className="primary-button" onClick={checkAndIngest} disabled={!canContinue}>
          {busy ? "Working…" : "Check access & ingest"}
        </button>
      </div>

      {error && <div className="notice error" role="alert">{error}</div>}

      {pendingGrant && (
        <div className="permission-confirm">
          <h3>Grant access to continue</h3>
          <p>Mucaro needs explicit read access to the resolved folder before it can ingest supported files.</p>
          <dl>
            <div><dt>Requested</dt><dd>{pendingGrant.path}</dd></div>
            <div><dt>Granted root</dt><dd>{pendingGrant.canonicalRoot}</dd></div>
          </dl>
          <div className="button-row">
            <button type="button" className="primary-button" onClick={grantAndIngest} disabled={busy}>
              {busy ? "Working…" : "Grant access & ingest"}
            </button>
            <button type="button" className="secondary-button" onClick={() => setPendingGrant(null)} disabled={busy}>Cancel</button>
          </div>
        </div>
      )}

      {result && (
        <div className="notice" role="status">
          {result.status} · files ingested {result.files_ingested ?? 1}
        </div>
      )}

      <div className="grant-list">
        <h3>Granted directories</h3>
        <p className="hint">Active grants live here. Revoke is the single grant-management action; changing the folder path above starts a new access check.</p>
        {grants.length === 0 && <p className="hint">No user-directory grants are active.</p>}
        {grants.map((grant) => (
          <article key={grant.canonical_root} className="grant-row">
            <div>
              <strong>{grant.canonical_root}</strong>
              <small>{grant.reason || "No reason recorded"} · {grant.granted_by}</small>
            </div>
            <button type="button" className="secondary-button" onClick={() => revokeGrant(grant)} disabled={busy}>Revoke</button>
          </article>
        ))}
      </div>
    </section>
  );
}
