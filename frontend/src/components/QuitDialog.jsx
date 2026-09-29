import React, { useState } from "react";
import { api } from "../api.js";
import { Sheet } from "./Sheet.jsx";

// Quit for the no-terminal launchers (Start Mucaro AI.app / Start Mucaro AI.exe): Mucaro runs in the
// background with no window of its own, so this is the way to stop it. The launcher stops the service
// within a second of POST /shutdown.
export function QuitDialog({ onClose, onStopped }) {
  const [quitting, setQuitting] = useState(false);
  const [error, setError] = useState(null);

  async function quit() {
    setQuitting(true);
    setError(null);
    try {
      await api.quit();
      onStopped();
    } catch (err) {
      setError(err);
      setQuitting(false);
    }
  }

  return (
    <Sheet title="Quit Mucaro AI?" onClose={onClose} side="center">
      <p>Mucaro stops running on this computer. Your sources, conversations and memory stay saved. Double-click Start Mucaro AI to start it again.</p>
      {error && <p className="error-card" role="alert">Mucaro could not quit: {error.message}</p>}
      <div className="button-row">
        <button type="button" className="secondary-button" onClick={onClose} disabled={quitting}>Keep running</button>
        <button type="button" className="primary-button" onClick={quit} disabled={quitting}>{quitting ? "Quitting…" : "Quit"}</button>
      </div>
    </Sheet>
  );
}

export function StoppedScreen() {
  return (
    <main id="main" className="stopped-screen">
      <section className="panel" role="status">
        <h1>Mucaro AI has stopped</h1>
        <p>You can close this tab. Double-click Start Mucaro AI to start it again.</p>
      </section>
    </main>
  );
}
