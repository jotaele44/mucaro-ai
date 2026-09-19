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

  async function checkAccess() {
    if (!path.trim()) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const payload = await api.checkDirectoryPermission({ path, reason });
      setPermission(payload);
      if (payload.status === "ERROR_DIRECTORY_PERMISSION_REQUIRED") {
        setPendingGrant({ path, canonicalRoot: payload.canonical_root, reason });
      } else {
        setPendingGrant(null);
      }
    } catch (err) {
      const detail = errorDetail(err);
      setPermission(detail);
      setError(detail.detail || err.message);
    } finally {
      setBusy(false);
    }
  }

  async function grantAccess({ ingestAfterGrant = false } = {}) {
    const target = pendingGrant?.path || path;
    if (!target.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const grant = await api.grantDirectoryPermission({ path: target, reason });
      setPermission({ status: "PERMISSION_OK", scope: "granted_directory", canonical_root: grant.canonical_root, grant });
      setPendingGrant(null);
      await refreshGrants();
      if (ingestAfterGrant) {
        await ingestDirectory(target);
      }
    } catch (err) {
      const detail = errorDetail(err);
      setError(detail.detail || err.message);
    } finally {
      setBusy(false);
    }
  }

  async function ingestDirectory(targetPath = path) {
    if (!targetPath.trim()) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const payload = await api.ingestPath({ path: targetPath });
      setResult(payload);
      setPermission(payload.directory_permission || null);
      if (onIngested) await onIngested();
    } catch (err) {
      const detail = errorDetail(err);
      if (detail.status === "ERROR_DIRECTORY_PERMISSION_REQUIRED") {
        setPermission(detail);
        setPendingGrant({ path: targetPath, canonicalRoot: detail.canonical_root, reason });
      }
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
  const canIngest = path.trim().length > 0 && !busy;

  return (
    <section className="panel directory-intake">
      <div className="panel-header">
        <h2>Directory Access</h2>
        <span className="badge">{permissionStatus}</span>
      </div>
      <div className="path-form">
        <label>
          Local path
          <input
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="/Users/name/Documents/mucaro-sources"
          />
        </label>
        <label>
          Reason
          <input value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
      </div>
      <div className="button-row">
        <button onClick={checkAccess} disabled={!canIngest}>Check Access</button>
        <button onClick={() => ingestDirectory()} disabled={!canIngest}>Ingest Path</button>
      </div>
      {error && <div className="notice error">{error}</div>}
      {permission && (
        <div className="permission-status">
          <dl>
            <div><dt>Status</dt><dd>{permission.status}</dd></div>
            <div><dt>Scope</dt><dd>{permission.scope || "none"}</dd></div>
            <div><dt>Root</dt><dd>{permission.canonical_root || "unresolved"}</dd></div>
          </dl>
        </div>
      )}
      {pendingGrant && (
        <div className="permission-confirm">
          <h3>Confirm Directory Access</h3>
          <p>Mucaro AI will be allowed to read supported files under this directory for local ingestion.</p>
          <dl>
            <div><dt>Requested</dt><dd>{pendingGrant.path}</dd></div>
            <div><dt>Granted Root</dt><dd>{pendingGrant.canonicalRoot}</dd></div>
          </dl>
          <div className="button-row">
            <button onClick={() => grantAccess({ ingestAfterGrant: false })} disabled={busy}>Grant Access</button>
            <button onClick={() => grantAccess({ ingestAfterGrant: true })} disabled={busy}>Grant and Ingest</button>
            <button className="secondary" onClick={() => setPendingGrant(null)} disabled={busy}>Cancel</button>
          </div>
        </div>
      )}
      {result && (
        <div className="notice">
          {result.status} files_ingested={result.files_ingested ?? 1}
        </div>
      )}
      <div className="grant-list">
        <h3>Granted Directories</h3>
        {grants.length === 0 && <p className="hint">No user-directory grants are active.</p>}
        {grants.map((grant) => (
          <article key={grant.canonical_root} className="grant-row">
            <div>
              <strong>{grant.canonical_root}</strong>
              <small>{grant.reason || "No reason recorded"} - {grant.granted_by}</small>
            </div>
            <button className="secondary" onClick={() => revokeGrant(grant)} disabled={busy}>Revoke</button>
          </article>
        ))}
      </div>
    </section>
  );
}
