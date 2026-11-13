import React, { useEffect, useRef, useState } from "react";
import { CleartextNotice } from "./CleartextNotice.jsx";

/**
 * Shown in place of the workspaces when the service wants a token this browser does not have, or no longer has.
 * reason: "required" (none stored), "rejected" (a stored or typed token was refused), "disabled" (the computer was not
 * set up for use from another device, so a token would not help).
 */
export function TokenGate({ reason, busy, onSubmit, onRetry }) {
  const [token, setToken] = useState("");
  const [remember, setRemember] = useState(false);
  const [shown, setShown] = useState(false);
  const input = useRef(null);

  // Put the cursor in the field when the gate appears and after each check, so a typo can be fixed at once.
  useEffect(() => {
    if (!busy && reason !== "disabled") input.current?.focus({ preventScroll: true });
  }, [busy, reason]);

  if (reason === "disabled") {
    return (
      <section className="workspace gate" aria-labelledby="gate-title">
        <header className="workspace-header">
          <h1 id="gate-title">Phone access is turned off</h1>
          <p className="muted">Mucaro on the computer is not set up to be used from another device, so it refused this one.</p>
        </header>
        <div className="panel">
          <p>
            On the computer, start Mucaro with phone access, then open the link it prints on this device. The link carries the access token, so there is nothing to type.
            The steps are in docs/PHONE_ACCESS.md.
          </p>
          <div className="button-row">
            <button type="button" className="secondary-button" onClick={onRetry}>Try again</button>
          </div>
        </div>
      </section>
    );
  }

  function submit(event) {
    event.preventDefault();
    const value = token.trim();
    if (value && !busy) onSubmit(value, remember);
  }

  return (
    <section className="workspace gate" aria-labelledby="gate-title">
      <header className="workspace-header">
        <h1 id="gate-title">Enter the access token</h1>
        <p className="muted">
          This Mucaro is protected by an access token. When Mucaro starts on the computer with phone access, it prints a link that already contains the token: open that link
          on this device. Or paste the token here.
        </p>
      </header>
      {reason === "rejected" && !busy && (
        <p className="error-card" role="alert">
          That access token was not accepted. If Mucaro was restarted on the computer it made a new one: open the newest link it printed, or paste the new token.
        </p>
      )}
      <CleartextNotice />
      <form className="panel" onSubmit={submit}>
        <div className="field">
          <label htmlFor="gate-token">Access token</label>
          <div className="gate-input">
            <input
              id="gate-token"
              ref={input}
              type={shown ? "text" : "password"}
              value={token}
              onChange={(event) => setToken(event.target.value)}
              autoComplete="off"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              enterKeyHint="go"
            />
            <button type="button" className="secondary-button" aria-pressed={shown} onClick={() => setShown((value) => !value)}>
              {shown ? "Hide" : "Show"}
            </button>
          </div>
        </div>
        <label className="switch">
          <input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} />
          <span>Remember on this device</span>
        </label>
        <p className="muted small">Leave this off on a shared device: the token is then forgotten when this tab closes.</p>
        <button type="submit" className="primary-button" disabled={busy || !token.trim()}>
          {busy ? "Checking…" : "Continue"}
        </button>
      </form>
    </section>
  );
}
