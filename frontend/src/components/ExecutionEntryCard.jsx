import React, { useEffect, useRef, useState } from "react";

import { api } from "../api.js";
import { Sheet } from "./Sheet.jsx";

const READY = { GOAL: "VECTOR_READY", PLAN: "PLAN_APPROVED" };
const CANDIDATE = { GOAL: "VECTOR_CANDIDATE", PLAN: "PLAN_CANDIDATE" };

function fallbackLabels(mode) {
  return mode === "PLAN"
    ? { locale: "en", mode, primary: "Execute", secondary: "Revise" }
    : { locale: "en", mode, primary: "Proceed", secondary: "Adjust" };
}

function makeKey(entry, action) {
  const random =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `entry-ui:${entry.artifact_id}:${entry.revision}:${action}:${random}`;
}

export function ExecutionEntryCard({ context, locale = "en", onAction, onInspect, onStop, onResume }) {
  const entry = context?.entry;
  const decision = context?.decision;
  const run = context?.run;
  const [labels, setLabels] = useState(() => (entry ? fallbackLabels(entry.mode) : null));
  const [busy, setBusy] = useState(false);
  const [editor, setEditor] = useState(false);
  const [draft, setDraft] = useState(entry?.prompt || "");
  const [error, setError] = useState(null);
  const actionLock = useRef(false);

  useEffect(() => {
    setDraft(entry?.prompt || "");
    setEditor(false);
    setError(null);
  }, [entry?.artifact_id, entry?.revision, entry?.prompt]);

  useEffect(() => {
    let cancelled = false;
    if (!entry) {
      setLabels(null);
      return () => {
        cancelled = true;
      };
    }
    setLabels(fallbackLabels(entry.mode));
    api.executionEntryLabels(locale, entry.mode)
      .then((payload) => {
        if (!cancelled) setLabels(payload);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [entry?.mode, locale]);

  if (!entry) return null;

  const consumed = decision && ["CLAIMED", "DISPATCHED"].includes(decision.state);
  const ready = entry.state === READY[entry.mode];
  const candidate = entry.state === CANDIDATE[entry.mode];
  const disabledReason = !ready ? `Requires ${READY[entry.mode]} with every mandatory gate PASS.` : "";
  const revisionLabel = entry.mode === "GOAL" ? `Vector revision ${entry.vector_revision}` : `Plan revision ${entry.plan_revision}`;

  async function perform(action, changes = {}) {
    if (actionLock.current) return;
    actionLock.current = true;
    setBusy(true);
    setError(null);
    try {
      await onAction(action, makeKey(entry, action), changes);
      setEditor(false);
    } catch (err) {
      setError(`${err?.status || "ERROR"}: ${err?.message || "The decision could not be recorded."}`);
    } finally {
      setBusy(false);
      actionLock.current = false;
    }
  }

  const activeState = run?.state;
  const canStop = ["RUNNING", "AWAITING_APPROVAL", "AWAITING_INPUT"].includes(activeState);
  const canResume = activeState === "INTERRUPTED";

  return (
    <section className="execution-entry-card" aria-labelledby={`execution-entry-${entry.artifact_id}`}>
      <div className="execution-entry-copy">
        <p className="eyebrow">{entry.mode} mode · {revisionLabel}</p>
        <h2 id={`execution-entry-${entry.artifact_id}`}>{entry.objective_id}</h2>
        <p>{entry.prompt}</p>
        <p className="muted small">State: {entry.state} · Source {entry.source_commit.slice(0, 12)}</p>
      </div>

      {consumed ? (
        <div className="button-row execution-entry-actions" aria-label={`${entry.mode} execution controls`}>
          <button type="button" className="secondary-button" onClick={() => onInspect?.(decision, run)}>Inspect</button>
          {canStop && <button type="button" className="secondary-button" onClick={() => onStop?.(run)}>Stop</button>}
          {canResume && (
            <button type="button" className="primary-button" onClick={() => onResume?.(decision)} disabled={busy}>
              {busy ? "Resuming…" : "Resume"}
            </button>
          )}
        </div>
      ) : (
        <>
          {disabledReason && (
            <p className="notice execution-entry-reason" id={`execution-entry-reason-${entry.artifact_id}`} role="status">
              {disabledReason}
            </p>
          )}
          <div className="button-row execution-entry-actions" aria-label={`${entry.mode} entry actions`}>
            <button
              type="button"
              className="secondary-button"
              disabled={busy || (!candidate && !ready)}
              onClick={() => setEditor(true)}
            >
              {labels?.secondary || fallbackLabels(entry.mode).secondary}
            </button>
            <button
              type="button"
              className="primary-button"
              disabled={busy || !ready}
              aria-describedby={!ready ? `execution-entry-reason-${entry.artifact_id}` : undefined}
              onClick={() => perform(labels?.primary || fallbackLabels(entry.mode).primary)}
            >
              {busy ? "Working…" : labels?.primary || fallbackLabels(entry.mode).primary}
            </button>
          </div>
        </>
      )}

      {error && <p className="error-text small" role="alert">{error}</p>}

      {editor && (
        <Sheet title={entry.mode === "GOAL" ? "Adjust goal" : "Revise plan"} onClose={() => setEditor(false)} side="center">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              perform(entry.mode === "GOAL" ? "Adjust" : "Revise", { prompt: draft.trim() });
            }}
          >
            <label className="field">
              <span>{entry.mode === "GOAL" ? "Adjusted objective" : "Revised plan"}</span>
              <textarea data-autofocus value={draft} onChange={(event) => setDraft(event.target.value)} rows={6} maxLength={20000} />
            </label>
            <p className="muted small">
              Saving creates a new candidate revision. It never changes or executes the approved revision in place.
            </p>
            <div className="button-row">
              <button type="button" className="secondary-button" onClick={() => setEditor(false)} disabled={busy}>Cancel</button>
              <button type="submit" className="primary-button" disabled={busy || !draft.trim()}>
                {busy ? "Saving…" : "Save new revision"}
              </button>
            </div>
          </form>
        </Sheet>
      )}
    </section>
  );
}
