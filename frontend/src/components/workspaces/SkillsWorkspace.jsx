import React, { useCallback, useEffect, useState } from "react";
import { api } from "../../api.js";

function errorText(error) {
  return `${error.category || "ERROR"} — ${error.status || "REQUEST_FAILED"}: ${error.message}`;
}

// Skill operator surface: registration, mount, authorization, invocation, and receipts stay visible together.
export function SkillsWorkspace({ announce }) {
  const [skills, setSkills] = useState([]);
  const [mounts, setMounts] = useState([]);
  const [executions, setExecutions] = useState([]);
  const [argumentsText, setArgumentsText] = useState({});
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    try {
      const [skillPayload, mountPayload, executionPayload] = await Promise.all([api.skills(), api.skillMounts(), api.skillExecutions()]);
      setSkills(skillPayload.skills || []);
      setMounts(mountPayload.mounts || []);
      setExecutions((executionPayload.executions || []).slice().reverse());
    } catch (err) {
      setError(err);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function action(skillId, fn, message) {
    setBusy(skillId);
    setError(null);
    try {
      await fn();
      await load();
      announce(message);
    } catch (err) {
      setError(err);
    } finally {
      setBusy("");
    }
  }

  async function execute(skillId) {
    let args = {};
    try {
      args = JSON.parse(argumentsText[skillId] || "{}");
      if (!args || Array.isArray(args) || typeof args !== "object") throw new Error("Arguments must be a JSON object.");
    } catch (err) {
      setError({ status: "ERROR_SKILL_ARGUMENTS_INVALID", message: err.message, category: "INPUT" });
      return;
    }
    await action(skillId, () => api.executeSkill(skillId, args), "Skill invoked; receipt recorded.");
  }

  return (
    <section className="workspace" aria-labelledby="skills-title">
      <header className="workspace-header">
        <h1 id="skills-title">Skills</h1>
        <p className="muted small">Inspect registered skills, mount an immutable digest, authorize capabilities, then invoke with an auditable receipt.</p>
      </header>
      {error && <p className="error-card" role="alert">{errorText(error)}</p>}
      {!skills.length && <p className="empty-state muted">No registered skills.</p>}
      <ul className="skill-list">
        {skills.map((skill) => {
          const mount = mounts.find((item) => item.skill_id === skill.skill_id);
          const last = executions.find((item) => item.skill_id === skill.skill_id);
          const active = mount?.state === "ACTIVE";
          const running = busy === skill.skill_id;
          return (
            <li key={skill.skill_id} className="panel skill-card">
              <div className="goal-head">
                <div className="grow">
                  <div className="tool-row-head"><span className={`status-chip status-${active ? "completed" : mount ? "queued" : "pending"}`}>{active ? "ACTIVE" : mount ? "MOUNTED" : "REGISTERED"}</span><span className="muted small">{skill.runtime}</span></div>
                  <h2>{skill.skill_id}</h2>
                  <p>{skill.description}</p>
                </div>
              </div>
              <dl className="facts">
                <dt>Version</dt><dd>{skill.version}</dd>
                <dt>Digest</dt><dd className="mono">{skill.digest}</dd>
                <dt>Capabilities</dt><dd>{skill.capabilities?.length ? skill.capabilities.join(" · ") : "runtime-only"}</dd>
              </dl>
              <div className="workspace-actions">
                {!mount && <button type="button" className="secondary-button" disabled={running} onClick={() => action(skill.skill_id, () => api.mountSkill(skill.skill_id), "Skill mounted.")}>Mount</button>}
                {mount && !active && <button type="button" className="secondary-button" disabled={running} onClick={() => action(skill.skill_id, () => api.activateSkill(skill.skill_id), "Skill authorized and activated.")}>Authorize & activate</button>}
              </div>
              {active && (
                <div className="skill-invoke">
                  <label className="field"><span>Arguments (JSON object)</span><textarea rows={2} value={argumentsText[skill.skill_id] || "{}"} onChange={(event) => setArgumentsText((old) => ({ ...old, [skill.skill_id]: event.target.value }))} /></label>
                  <button type="button" className="primary-button" disabled={running} onClick={() => execute(skill.skill_id)}>{running ? "Invoking…" : "Invoke skill"}</button>
                </div>
              )}
              {last && <details className="panel nested-panel"><summary>Latest receipt: {last.status} · {last.finished_at}</summary><dl className="facts"><dt>Execution ID</dt><dd className="mono">{last.execution_id}</dd><dt>Route</dt><dd>{last.execution_route}</dd><dt>Artifact output</dt><dd>{last.artifact_lineage?.output?.artifact_id || "—"}</dd></dl><pre className="json-block"><code>{JSON.stringify(last.result, null, 2)}</code></pre></details>}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
