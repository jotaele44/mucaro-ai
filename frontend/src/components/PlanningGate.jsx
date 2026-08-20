import React, { useState } from "react";
import { api } from "../api.js";

export function PlanningGate({ onApproved }) {
  const [task, setTask] = useState("Build a local RAG prototype with source lineage and execution gate.");
  const [gate, setGate] = useState(null);
  const [error, setError] = useState(null);

  async function runPlan() {
    setError(null);
    const result = await api.plan({ user_request: task, inputs_available: ["execution_mode: local", "target_project: mucaro-ai"], approval_state: "NOT_REQUESTED" });
    setGate(result);
  }

  async function approve() {
    setError(null);
    const result = await api.approve({ gate, approved_mode: gate.recommended_mode });
    setGate(result);
    if (onApproved) onApproved(result);
  }

  return (
    <section className="panel planning-gate">
      <div className="panel-header">
        <h2>Planning-to-Execution Gate</h2>
        <span className="badge">Approval Lock</span>
      </div>
      <textarea value={task} onChange={(e) => setTask(e.target.value)} />
      <div className="button-row">
        <button onClick={runPlan}>Build Planning Gate</button>
        <button onClick={approve} disabled={!gate || gate.state === "EXECUTING"}>Approve Recommended Mode</button>
      </div>
      {error && <pre className="error">{error}</pre>}
      {gate && (
        <div className="gate-grid">
          <div><strong>Mission</strong><p>{gate.mission}</p></div>
          <div><strong>Active Vector</strong><p>{gate.active_vector}</p></div>
          <div><strong>State</strong><p>{gate.state}</p></div>
          <div><strong>Readiness</strong><p>{gate.readiness_percent}%</p></div>
          <div><strong>Recommended Mode</strong><p>{gate.recommended_mode}</p></div>
          <div><strong>Trace</strong><p>{gate.execution_trace_id || "Not approved"}</p></div>
          <div className="wide"><strong>Gaps</strong><ul>{gate.gaps.map((x) => <li key={x}>{x}</li>)}</ul></div>
          <div className="wide"><strong>Risks</strong><ul>{gate.risks.map((x) => <li key={x}>{x}</li>)}</ul></div>
        </div>
      )}
    </section>
  );
}
