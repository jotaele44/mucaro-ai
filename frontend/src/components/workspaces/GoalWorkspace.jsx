import React, { useCallback, useEffect, useState } from "react";
import { api } from "../../api.js";

function errorText(error) {
  return `${error.category || "ERROR"} — ${error.status || "REQUEST_FAILED"}: ${error.message}`;
}

// Durable operator goal control: the UI mirrors the backend lifecycle rather than keeping a local plan.
export function GoalWorkspace({ announce }) {
  const [goals, setGoals] = useState([]);
  const [currentId, setCurrentId] = useState(null);
  const [selected, setSelected] = useState(null);
  const [events, setEvents] = useState([]);
  const [objective, setObjective] = useState("");
  const [activeVector, setActiveVector] = useState("general_task_execution");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const payload = await api.goals();
      const nextGoals = payload.goals || [];
      setGoals(nextGoals);
      setCurrentId(payload.current_goal_id || null);
      setSelected((old) => (old ? nextGoals.find((goal) => goal.goal_id === old.goal_id) || old : nextGoals.find((goal) => goal.is_current) || null));
    } catch (err) {
      setError(err);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!selected) {
      setEvents([]);
      return undefined;
    }
    let live = true;
    api.goalEvents(selected.goal_id).then((payload) => { if (live) setEvents(payload.events || []); }).catch((err) => { if (live) setError(err); });
    return () => { live = false; };
  }, [selected]);

  async function create(event) {
    event.preventDefault();
    if (!objective.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const payload = await api.createGoal({ objective: objective.trim(), active_vector: activeVector.trim() || "general_task_execution", make_current: true });
      setObjective("");
      setSelected(payload.goal);
      await load();
      announce("Goal set as the current goal.");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function update(goalId, payload, message) {
    setBusy(true);
    setError(null);
    try {
      const result = await api.updateGoal(goalId, payload);
      setSelected(result.goal);
      await load();
      announce(message);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="workspace" aria-labelledby="goals-title">
      <header className="workspace-header">
        <h1 id="goals-title">Goals</h1>
        <p className="muted small">One durable current goal, explicit state transitions, and an append-only event trail.</p>
      </header>
      {error && <p className="error-card" role="alert">{errorText(error)}</p>}
      <form className="panel" onSubmit={create}>
        <div className="panel-heading"><strong>Set a goal</strong><span className="muted small">This becomes the current operator focus.</span></div>
        <label className="field">
          <span>Objective</span>
          <textarea value={objective} onChange={(event) => setObjective(event.target.value)} maxLength={4000} rows={3} placeholder="Finish the next governed development vector" />
        </label>
        <div className="inline-form">
          <label className="field grow"><span>Active vector</span><input value={activeVector} onChange={(event) => setActiveVector(event.target.value)} maxLength={240} /></label>
          <button type="submit" className="primary-button" disabled={busy || !objective.trim()}>{busy ? "Saving…" : "Set current goal"}</button>
        </div>
      </form>
      {!goals.length && <p className="empty-state muted">No goals recorded yet.</p>}
      <ul className="goal-list">
        {goals.map((goal) => (
          <li key={goal.goal_id} className={`panel goal-card ${selected?.goal_id === goal.goal_id ? "selected" : ""}`}>
            <div className="goal-head">
              <div className="grow">
                <div className="tool-row-head"><span className={`status-chip status-${goal.status.toLowerCase()}`}>{goal.status}</span>{goal.is_current && <span className="status-chip status-completed">Current</span>}</div>
                <h2>{goal.objective}</h2>
                <p className="muted small">Vector: <span className="mono">{goal.active_vector}</span> · updated {goal.updated_at}</p>
              </div>
              <button type="button" className="secondary-button" onClick={() => setSelected(selected?.goal_id === goal.goal_id ? null : goal)}>{selected?.goal_id === goal.goal_id ? "Hide details" : "Inspect"}</button>
            </div>
            {selected?.goal_id === goal.goal_id && (
              <div className="goal-detail">
                <dl className="facts">
                  <dt>Goal ID</dt><dd className="mono">{goal.goal_id}</dd>
                  <dt>Assumptions</dt><dd>{goal.assumptions?.length ? goal.assumptions.join(" · ") : "None recorded"}</dd>
                  <dt>Gaps</dt><dd>{goal.gaps?.length ? goal.gaps.join(" · ") : "None recorded"}</dd>
                  <dt>Variables</dt><dd>{goal.variables?.length ? goal.variables.join(" · ") : "None recorded"}</dd>
                </dl>
                <div className="workspace-actions">
                  {goal.status === "ACTIVE" && <button type="button" className="secondary-button" disabled={busy} onClick={() => update(goal.goal_id, { status: "PAUSED" }, "Goal paused.")}>Pause</button>}
                  {goal.status === "PAUSED" && <button type="button" className="primary-button" disabled={busy} onClick={() => update(goal.goal_id, { status: "ACTIVE", make_current: true }, "Goal resumed as current.")}>Resume</button>}
                  {!goal.is_current && !["COMPLETE", "CANCELLED"].includes(goal.status) && <button type="button" className="secondary-button" disabled={busy} onClick={() => update(goal.goal_id, { make_current: true }, "Goal is now current.")}>Make current</button>}
                  {goal.status === "ACTIVE" && <button type="button" className="secondary-button" disabled={busy} onClick={() => update(goal.goal_id, { status: "COMPLETE" }, "Goal marked complete.")}>Complete</button>}
                </div>
                <details className="panel nested-panel" open>
                  <summary>Event trail ({events.length})</summary>
                  <ol className="event-list">{events.map((event) => <li key={`${event.goal_id}-${event.seq}`}><strong>{event.event_type}</strong><span className="muted small">{event.created_at}</span></li>)}</ol>
                </details>
              </div>
            )}
          </li>
        ))}
      </ul>
      {currentId && <p className="muted small">Current goal: <span className="mono">{currentId}</span></p>}
    </section>
  );
}
