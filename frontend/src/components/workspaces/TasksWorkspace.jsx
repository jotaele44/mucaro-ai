import React, { useCallback, useEffect, useState } from "react";
import { api } from "../../api.js";

const STATUS_LABEL = {
  TODO: "To do",
  IN_PROGRESS: "In progress",
  BLOCKED: "Blocked",
  DONE: "Done",
  FAILED: "Failed",
  CANCELLED: "Cancelled",
};

function errorText(error) {
  return `${error.category || "ERROR"} — ${error.status || "REQUEST_FAILED"}: ${error.message}`;
}

function nextActions(task) {
  if (task.status === "TODO") return [{ status: "IN_PROGRESS", label: "Start" }, { status: "CANCELLED", label: "Cancel" }];
  if (task.status === "IN_PROGRESS") return [{ status: "DONE", label: "Mark done" }, { status: "BLOCKED", label: "Block" }];
  if (task.status === "BLOCKED") return [{ status: "TODO", label: "Return to queue" }];
  if (task.status === "FAILED") return [{ status: "TODO", label: "Retry" }];
  return [];
}

export function TasksWorkspace({ announce }) {
  const [payload, setPayload] = useState(null);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState("normal");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setPayload(await api.tasks());
      setError(null);
    } catch (err) {
      setError(err);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function create(event) {
    event.preventDefault();
    if (!title.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.createTask({ title: title.trim(), description: description.trim(), priority });
      setTitle("");
      setDescription("");
      setPriority("normal");
      await load();
      announce("Plan task created.");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function transition(task, status) {
    setBusy(true);
    setError(null);
    try {
      await api.updateTask(task.task_id, { status, expected_sequence: task.sequence });
      await load();
      announce(`Task ${STATUS_LABEL[status].toLowerCase()}.`);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const tasks = payload?.tasks || [];
  return (
    <section className="workspace" aria-labelledby="tasks-title">
      <header className="workspace-header">
        <h1 id="tasks-title">Plan tasks</h1>
        <p className="muted small">Durable DAG tasks with explicit state transitions. Agent runs can bind to a task, but never mark it complete automatically.</p>
      </header>
      {error && <p className="error-card" role="alert">{errorText(error)}</p>}
      <form className="panel" onSubmit={create}>
        <div className="panel-heading"><strong>Add a plan task</strong><span className="muted small">Dependencies remain governed by the backend DAG.</span></div>
        <label className="field"><span>Title</span><input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={500} placeholder="Define the next executable step" /></label>
        <label className="field"><span>Description</span><textarea value={description} onChange={(event) => setDescription(event.target.value)} maxLength={10000} rows={2} placeholder="What evidence or outcome is required?" /></label>
        <div className="inline-form">
          <label className="field"><span>Priority</span><select value={priority} onChange={(event) => setPriority(event.target.value)}><option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option><option value="urgent">Urgent</option></select></label>
          <button type="submit" className="primary-button" disabled={busy || !title.trim()}>{busy ? "Saving…" : "Add task"}</button>
        </div>
      </form>
      {payload && <p className="muted small">{payload.task_count} task{payload.task_count === 1 ? "" : "s"} · ready: {payload.ready_task_ids?.length || 0} · graph valid: {payload.dependency_graph_valid ? "yes" : "no"}</p>}
      {!tasks.length && <p className="empty-state muted">No plan tasks recorded yet.</p>}
      <ul className="goal-list" aria-label="Durable plan tasks">
        {tasks.map((task) => (
          <li key={task.task_id} className="panel goal-card">
            <div className="goal-head">
              <div className="grow">
                <div className="tool-row-head"><span className={`status-chip status-${task.status.toLowerCase()}`}>{STATUS_LABEL[task.status] || task.status}</span><span className="status-chip">{task.priority}</span></div>
                <h2>{task.title}</h2>
                <p className="muted small">ID <span className="mono">{task.task_id}</span> · sequence {task.sequence}{task.depends_on?.length ? ` · depends on ${task.depends_on.join(", ")}` : ""}</p>
              </div>
            </div>
            {task.description && <p>{task.description}</p>}
            {nextActions(task).length > 0 && <div className="workspace-actions">{nextActions(task).map((action) => <button key={action.status} type="button" className={action.status === "DONE" ? "primary-button" : "secondary-button"} disabled={busy} onClick={() => transition(task, action.status)}>{action.label}</button>)}</div>}
          </li>
        ))}
      </ul>
    </section>
  );
}
