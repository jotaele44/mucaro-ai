import React, { useEffect, useMemo, useRef, useState } from "react";
import { nextVectorOptions } from "../nextVectorOptions.js";

/**
 * User-controlled follow-up choice. Selection has no side effects.
 * Start sends a single prompt through ChatView's canonical send() and work_runs.
 */
export function NextVectorPicker({ request, onStart, disabled = false }) {
  const options = useMemo(() => nextVectorOptions(request), [request]);
  const [selected, setSelected] = useState("continue");
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    setSelected("continue");
    submitting.current = false;
    setBusy(false);
    setError(null);
    setExpanded(false);
  }, [request]);

  if (!options.length) return null;
  const option = options.find((item) => item.id === selected);
  async function start(event) {
    event.preventDefault();
    if (!option || busy || submitting.current || disabled) return;
    submitting.current = true;
    setBusy(true);
    setError(null);
    try {
      // Existing canonical send path enforces permissions, idempotency and run tracking.
      await onStart(option.prompt);
    } catch (err) {
      setError(err?.message || "The selected prompt could not be submitted.");
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  return (
    <form className="next-vector-picker panel" onSubmit={start} aria-label="Suggested next steps">
      <fieldset disabled={busy || disabled}>
        <legend>Choose the next vector</legend>
        <p className="muted small">Select an option, then start it. Selecting alone does not send a message or authorize tools.</p>
        {options.map((item) => (
          <label className="next-vector-option" key={item.id}>
            <input
              type="radio"
              name="next-vector"
              value={item.id}
              checked={selected === item.id}
              onChange={() => setSelected(item.id)}
            />
            <span>{item.label}</span>
          </label>
        ))}
      </fieldset>
      <button type="button" className="ghost-button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
        {expanded ? "Hide exact prompt" : "Preview exact prompt"}
      </button>
      {expanded && <pre className="next-vector-prompt">{option?.prompt}</pre>}
      <button type="submit" className="primary-button wide" disabled={!option || busy || disabled}>
        {busy ? "Sending selected vector…" : "Start selected vector"}
      </button>
      {error && <p className="error-text small" role="alert">{error}</p>}
    </form>
  );
}
