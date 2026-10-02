import React, { useState } from "react";
import { Sheet } from "./Sheet.jsx";

// Confirmation for actions that cannot be undone. Cancel holds the initial focus, a failure stays inside the sheet,
// and the sheet cannot be dismissed while the action is running.
export function ConfirmSheet({ title, children, confirmLabel, cancelLabel = "Keep it", onConfirm, onClose }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function confirm() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
      onClose();
    } catch (err) {
      setError(`${err?.status || "ERROR"}: ${err?.message || "The action failed."}`);
      setBusy(false);
    }
  }

  return (
    <Sheet title={title} onClose={busy ? () => {} : onClose} side="center">
      <div>{children}</div>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      <div className="button-row">
        <button type="button" className="secondary-button" data-autofocus onClick={onClose} disabled={busy}>
          {cancelLabel}
        </button>
        <button type="button" className="danger-button" onClick={confirm} disabled={busy}>
          {busy ? "Working…" : confirmLabel}
        </button>
      </div>
    </Sheet>
  );
}
