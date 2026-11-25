import React, { useEffect, useMemo, useRef, useState } from "react";
import { copyFailureMessage, copyText } from "../clipboard.js";
import { PROPOSED_ID, contextualNextVectorOptions, relayOptions } from "../nextVectorOptions.js";

/**
 * User-controlled follow-up choice. Selection has no side effects.
 * Start sends a single prompt through ChatView's canonical send() and work_runs.
 *
 * `proposal` is the finished run's `next_vector` event, if it produced one: the vector the model wrote at the
 * end of its answer. It is offered first, with its exact prompt, and is still only sent by Start.
 */
export function NextVectorPicker({ request, proposal = null, suggestions = null, onStart, announce, disabled = false }) {
  const relay = useMemo(() => {
    if (suggestions != null) return { options: contextualNextVectorOptions(request, suggestions), proposed: null, initial: "continue" };
    return relayOptions(request, proposal);
  }, [request, proposal, suggestions]);
  const { options, proposed } = relay;
  const initial = options.some((item) => item.id === relay.initial) ? relay.initial : options[0]?.id;
  const [selected, setSelected] = useState(initial);
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const pendingRetry = useRef(null);
  const [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const copiedTimer = useRef(null);
  useEffect(() => {
    setSelected(initial);
    submitting.current = false;
    pendingRetry.current = null;
    setBusy(false);
    setError(null);
    setExpanded(false);
    setCopied(false);
  }, [request, initial, proposed?.text]);
  useEffect(() => () => clearTimeout(copiedTimer.current), []);

  if (!options.length) return null;
  const option = options.find((item) => item.id === selected);
  async function start(event) {
    event.preventDefault();
    if (!option || busy || submitting.current || disabled) return;
    submitting.current = true;
    setBusy(true);
    setError(null);
    // Preserve the same request identity after an ambiguous transport failure.
    // A changed selection/prompt represents a different user intent.
    if (!pendingRetry.current || pendingRetry.current.prompt !== option.prompt) {
      pendingRetry.current = {
        prompt: option.prompt,
        key: typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
          ? crypto.randomUUID()
          : `${Date.now()}-${Math.random().toString(16).slice(2)}`,
      };
    }
    try {
      // Replays of an accepted-but-unacknowledged POST reuse the same key.
      await onStart(option.prompt, pendingRetry.current.key);
      pendingRetry.current = null;
    } catch (err) {
      setError(err?.message || "The selected prompt could not be submitted.");
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  async function copyVector() {
    const result = await copyText(proposed.text);
    announce?.(result.ok ? "Vector copied to clipboard." : copyFailureMessage(result));
    if (!result.ok) return;
    setCopied(true);
    clearTimeout(copiedTimer.current);
    copiedTimer.current = setTimeout(() => setCopied(false), 1500);
  }

  return (
    <form className="next-vector-picker panel" onSubmit={start} aria-label="Suggested next steps">
      <fieldset disabled={busy || disabled}>
        <legend>Choose the next vector</legend>
        <p className="muted small">Select an option, then start it. Selecting alone does not send a message or authorize tools.</p>
        {options.map((item) => (
          <label className={`next-vector-option${item.id === PROPOSED_ID ? " next-vector-proposed" : ""}`} key={item.id}>
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
      {proposed && (
        <div className="next-vector-provenance" data-selected={selected === PROPOSED_ID}>
          <p className="muted small">
            The first option is the vector the model wrote at the end of its answer{proposed.author ? ` (${proposed.author})` : ""}. It is a
            proposal, not an instruction: starting it sends the exact prompt below as your next message, and actions still ask first.
            {proposed.readNetwork && " This run read web content, so the vector reflects the model's reading of it."}
          </p>
          {proposed.gateSummary && (
            <p className="muted small">Gates as the model states them, not verified: {proposed.gateSummary}.</p>
          )}
          {proposed.reviewReasons.length > 0 && (
            <p className="notice small" role="note">
              Read the proposed vector before starting it: {proposed.reviewReasons.join("; ")}. It is not selected by default.
            </p>
          )}
        </div>
      )}
      <div className="next-vector-tools">
        <button type="button" className="ghost-button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
          {expanded ? "Hide exact prompt" : "Preview exact prompt"}
        </button>
        {proposed && (
          <button type="button" className="ghost-button" onClick={copyVector}>
            {copied ? "Vector copied" : "Copy proposed vector"}
          </button>
        )}
      </div>
      {expanded && <pre className="next-vector-prompt" tabIndex={0} aria-label="Exact prompt that will be sent">{option?.prompt}</pre>}
      <button type="submit" className="primary-button wide" disabled={!option || busy || disabled}>
        {busy ? "Sending selected vector…" : "Start selected vector"}
      </button>
      {error && <p className="error-text small" role="alert">{error}</p>}
    </form>
  );
}
