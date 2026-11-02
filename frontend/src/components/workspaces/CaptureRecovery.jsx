import React, { useEffect, useRef, useState } from "react";
import { api } from "../../api.js";

const DRAFT_KEY = "mucaro.captureRecovery";
function draft() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(DRAFT_KEY) || "{}");
    return { captureId: typeof saved?.captureId === "string" ? saved.captureId : "", sha256: typeof saved?.sha256 === "string" ? saved.sha256 : "" };
  } catch { return {}; }
}

export function CaptureRecovery({ announce }) {
  const [captureId, setCaptureId] = useState(() => draft()?.captureId || "");
  const [sha256, setSha256] = useState(() => draft()?.sha256 || "");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const inFlight = useRef(false);
  useEffect(() => {
    try { sessionStorage.setItem(DRAFT_KEY, JSON.stringify({ captureId, sha256 })); } catch {}
  }, [captureId, sha256]);

  async function recover(event) {
    event.preventDefault();
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true); setError(null); setResult(null);
    try {
      const saved = await api.recoverCapture(captureId.trim(), sha256.trim());
      setResult(saved);
      announce?.("Saved capture indexed without fetching its URL again.");
    } catch (err) { setError(err); }
    finally { inFlight.current = false; setBusy(false); }
  }
  return (
    <details className="panel">
      <summary>Recover a saved web capture</summary>
      <p className="muted small">Use this after a capture reports that its file was saved but could not be indexed. Copy the capture ID and SHA-256 from that failure receipt. Recovery retains the saved bytes and does not fetch the URL again.</p>
      <form onSubmit={recover} aria-busy={busy}>
        <label className="field">Saved capture ID<input required pattern="websnap_[0-9a-f]{16}" maxLength={24} value={captureId} disabled={busy} onChange={(event) => setCaptureId(event.target.value)} placeholder="websnap_…" /></label>
        <label className="field">Saved capture SHA-256<input required pattern="[0-9a-f]{64}" maxLength={64} value={sha256} disabled={busy} onChange={(event) => setSha256(event.target.value)} /></label>
        <button type="submit" className="secondary-button" disabled={busy || !captureId.trim() || !sha256.trim()}>{busy ? "Indexing saved capture…" : "Index saved capture"}</button>
      </form>
      {error && <p className="error-card" role="alert">{error.message || "The saved capture could not be indexed. Preserve the file and check its receipt."}</p>}
      {result && <p role="status">Saved capture indexed. Original bytes retained; no URL was fetched. Indexed captures: {result.ledger_entry_count}.</p>}
      <p className="muted small">Indexing restores the saved observation record. It does not establish that the page is current, validate its claims, or add it to the source library automatically.</p>
    </details>
  );
}
