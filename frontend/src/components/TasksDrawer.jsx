import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { STATE_LABEL } from "../feed.js";
import { Sheet } from "./Sheet.jsx";

function duration(start, end) {
  if (!start || !end) return null;
  const ms = new Date(end) - new Date(start);
  return ms >= 0 ? `${(ms / 1000).toFixed(1)} s` : null;
}

function Transcript({ runId }) {
  const [events, setEvents] = useState(null);
  useEffect(() => {
    api.runEvents(runId).then((payload) => setEvents(payload.events)).catch(() => setEvents([]));
  }, [runId]);
  if (!events) return <p className="muted small">Loading transcript…</p>;
  return (
    <ol className="transcript">
      {events.filter((event) => event.type !== "text_delta").map((event) => (
        <li key={event.seq}>
          <span className="mono small">#{event.seq}</span> <strong>{event.type}</strong>{" "}
          <span className="muted small">{event.data.tool_name || event.data.status || event.data.outcome || event.data.reason || ""}</span>
        </li>
      ))}
    </ol>
  );
}

// Background tasks (F18): every run with its state, timing and receipt-derived tool counts.
export function TasksDrawer({ onClose }) {
  const [runs, setRuns] = useState([]);
  const [next, setNext] = useState(null);
  const [open, setOpen] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async (before) => {
    try {
      const page = await api.workRuns(before);
      setRuns((previous) => (before ? [...previous, ...page.runs] : page.runs));
      setNext(page.next_before_seq);
    } catch (err) {
      setError(err);
    }
  }, []);

  useEffect(() => {
    load(null);
  }, [load]);

  return (
    <Sheet title="Background tasks" onClose={onClose}>
      {error && <p className="error-text" role="alert">{error.status}: {error.message}</p>}
      {!runs.length && !error && <p className="muted">No tasks yet.</p>}
      <ul className="task-list">
        {runs.map((run) => {
          const counts = run.tool_summary.by_status;
          return (
            <li key={run.run_id} className="task-row">
              <div className="task-head">
                <span className={`status-chip status-${run.state.toLowerCase()}`}>{STATE_LABEL[run.state] || run.state}</span>
                <span className="grow">{run.prompt_preview}</span>
              </div>
              <p className="muted small">
                {duration(run.started_at, run.finished_at) || "—"} · {run.tool_summary.total} tool call{run.tool_summary.total === 1 ? "" : "s"}
                {counts.FAILED ? ` · ${counts.FAILED} failed` : ""}
                {counts.DENIED ? ` · ${counts.DENIED} denied` : ""} · {run.event_count} events
                {run.error ? ` · ${run.error.category}` : ""}
              </p>
              <button type="button" className="ghost-button" aria-expanded={open === run.run_id} onClick={() => setOpen(open === run.run_id ? null : run.run_id)}>
                {open === run.run_id ? "Hide transcript" : "View transcript"}
              </button>
              {open === run.run_id && <Transcript runId={run.run_id} />}
            </li>
          );
        })}
      </ul>
      {next && (
        <button type="button" className="secondary-button" onClick={() => load(next)}>
          Load more
        </button>
      )}
    </Sheet>
  );
}
