import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { ConfirmSheet } from "./ConfirmSheet.jsx";

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
  const [revoking, setRevoking] = useState(null);

  async function refreshGrants() {
    const payload = await api.directoryPermissions();
    setGrants(payload.grants || []);
  }

  useEffect(() => { refreshGrants().catch((err) => setError(err.message)); }, []);

  function changePath(value) {
    setPath(value);
    setPermission(null);
    setPendingGrant(null);
    setResult(null);
    setError(null);
  }

  async function checkAccess() {
    if (!path.trim()) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const payload = await api.checkDirectoryPermission({ path, reason });
      setPermission(payload);
      setPendingGrant(
        payload.status === "ERROR_DIRECTORY_PERMISSION_REQUIRED"
          ? { path, canonicalRoot: payload.canonical_root, reason }
          : null,
      );
    } catch (err) {
      const detail = errorDetail(err);
      setPermission(detail);
      setPendingGrant(null);
      setError(detail.detail || err.message);
    } finally {
      setBusy(false);
    }
  }

  async function grantAccess() {
    const target = pendingGrant?.path;
    if (!target?.trim()) return;
    setBusy(true);
    setError(null);
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
    } catch (err) {
      const detail = errorDetail(err);
      setError(detail.detail || err.message);
    } finally {
      setBusy(false);
    }
  }

  async function ingestDirectory() {
    if (!path.trim() || permission?.status !== "PERMISSION_OK") return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const payload = await api.ingestPath({ path });
      setResult(payload);
      setPermission(payload.directory_permission || permission);
      if (onIngested) await onIngested();
    } catch (err) {
      const detail = errorDetail(err);
      if (detail.status === "ERROR_DIRECTORY_PERMISSION_REQUIRED") {
        setPermission(detail);
        setPendingGrant({ path, canonicalRoot: detail.canonical_root, reason });
      }
      setError(detail.detail || err.message);
    } finally {
      setBusy(false);
    }
  }

  function selectGrant(grant) {
    setPath(grant.canonical_root);
    setPermission({
      status: "PERMISSION_OK",
      scope: "granted_directory",
      canonical_root: grant.canonical_root,
      grant,
    });
    setPendingGrant(null);
    setResult(null);
    setError(null);
  }

  const currentGrant = useMemo(
    () => grants.find((grant) => grant.canonical_root === permission?.canonical_root) || null,
    [grants, permission],
  );

  // Revoking asks first (main's confirmation, M5): the button opens a ConfirmSheet that calls this.
  async function revokeGrant(grant) {
    if (!grant) return;
    setBusy(true);
    setError(null);
    try {
      await api.revokeDirectoryPermission({ path: grant.canonical_root, reason: "operator revoke" });
      await refreshGrants();
      setPermission(null);
      setPendingGrant(null);
    } catch (err) {
      const detail = errorDetail(err);
      setError(detail.detail || err.message);
    } finally {
      setBusy(false);
    }
  }

  const permissionStatus = permission?.status || "NOT_CHECKED";
  const hasPath = path.trim().length > 0;

  return (
    <section className="panel directory-intake" aria-labelledby="directory-access-title">
      <div className="panel-header">
        <h2 id="directory-access-title">Directory Access</h2>
        <span className="badge">{permissionStatus}</span>
      </div>
      <p className="hint">
        Choose one host folder, verify its permission state, then ingest it. Permission changes live only in this control surface.
      </p>

      <div className="path-form">
        <label>
          Folder path on the host computer
          <input
            value={path}
            onChange={(event) => changePath(event.target.value)}
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
      <div className="button-row directory-actions">
        <button type="button" className="secondary-button" onClick={checkAccess} disabled={!hasPath || busy}>Check access</button>
        <button type="button" className="primary-button" onClick={ingestDirectory} disabled={!hasPath || busy || permissionStatus !== "PERMISSION_OK"}>
          Ingest folder
        </button>
      </div>

      {error && <div className="notice error" role="alert">{error}</div>}
      <div className="permission-control" aria-label="Current directory permission">
        <dl className="facts">
          <dt>Path</dt><dd className="mono">{path.trim() || "No folder selected"}</dd>
          <dt>Status</dt><dd>{permissionStatus}</dd>
          <dt>Scope</dt><dd>{permission?.scope || "none"}</dd>
          <dt>Granted root</dt><dd className="mono">{permission?.canonical_root || "unresolved"}</dd>
        </dl>

        {pendingGrant && (
          <div className="permission-confirm">
            <h3>Confirm Directory Access</h3>
            <p>Mucaro AI will be allowed to read supported files under this directory for local ingestion.</p>
            <dl className="facts">
              <dt>Requested</dt><dd className="mono">{pendingGrant.path}</dd>
              <dt>Granted root</dt><dd className="mono">{pendingGrant.canonicalRoot}</dd>
            </dl>
            <div className="button-row">
              <button type="button" className="secondary-button" onClick={() => { setPendingGrant(null); setPermission(null); }} disabled={busy}>Cancel</button>
              <button type="button" className="primary-button" onClick={grantAccess} disabled={busy}>Grant access</button>
            </div>
          </div>
        )}

        {currentGrant && !pendingGrant && (
          <div className="button-row">
            <button type="button" className="danger-button" onClick={() => setRevoking(currentGrant)} disabled={busy}>Revoke access</button>
          </div>
        )}
      </div>

      {result && (
        <div className="notice" role="status">
          {result.status} · files ingested {result.files_ingested ?? 1}
        </div>
      )}

      <div className="grant-list">
        <h3>Granted Directories</h3>
        {grants.length === 0 && <p className="hint">No user-directory grants are active.</p>}
        {grants.map((grant) => (
          <button
            type="button"
            key={grant.canonical_root}
            className="grant-select"
            aria-pressed={currentGrant?.canonical_root === grant.canonical_root}
            onClick={() => selectGrant(grant)}
          >
            <span>
              <strong>{grant.canonical_root}</strong>
              <small>{grant.reason || "No reason recorded"} · {grant.granted_by}</small>
            </span>
            <span className="muted small">Manage</span>
          </button>
        ))}
      </div>
      {revoking && (
        <ConfirmSheet title="Revoke folder access?" confirmLabel="Revoke access" onConfirm={() => revokeGrant(revoking)} onClose={() => setRevoking(null)}>
          <p>
            Mucaro will stop reading files under <strong>{revoking.canonical_root}</strong>. Sources already ingested from it stay in the library.
          </p>
        </ConfirmSheet>
      )}
    </section>
  );
}
