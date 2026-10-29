import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api.js";

function errorText(error) {
  try {
    const cause = JSON.parse(error.message);
    if (cause?.detail) return `${error.category || "ERROR"} — ${cause.status || error.status}: ${cause.detail}`;
  } catch { /* Plain messages already have a readable detail. */ }
  return `${error.category || "ERROR"} — ${error.status || "REQUEST_FAILED"}: ${error.message}`;
}

function readDrafts() {
  try {
    const value = JSON.parse(window.sessionStorage.getItem("mucaro.skillDrafts") || "{}");
    return value && !Array.isArray(value) && typeof value === "object" ? value : {};
  } catch { return {}; }
}

// Skill operator surface: registration, mount, authorization, invocation, and receipts stay visible together.
export function SkillsWorkspace({ announce }) {
  const [skills, setSkills] = useState([]);
  const [mounts, setMounts] = useState([]);
  const [executions, setExecutions] = useState([]);
  const [argumentsText, setArgumentsText] = useState(() => readDrafts().argumentsText || {});
  const [fieldValues, setFieldValues] = useState(() => readDrafts().fieldValues || {});
  const [allowedCapabilities, setAllowedCapabilities] = useState({});
  const [policyIds, setPolicyIds] = useState({});
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(true);
  const actionInFlight = useRef(false);

  const load = useCallback(async () => {
    try {
      const [skillPayload, mountPayload, executionPayload] = await Promise.all([api.skills(), api.skillMounts(), api.skillExecutions()]);
      setSkills((skillPayload.skills || []).slice().sort((a, b) => Number(Boolean(b.arguments_schema)) - Number(Boolean(a.arguments_schema)) || a.skill_id.localeCompare(b.skill_id)));
      setMounts(mountPayload.mounts || []);
      setExecutions((executionPayload.executions || []).slice().reverse());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load().catch(setError); }, [load]);
  useEffect(() => {
    try { window.sessionStorage.setItem("mucaro.skillDrafts", JSON.stringify({ argumentsText, fieldValues })); }
    catch { /* A storage-restricted browser still supports the current view. */ }
  }, [argumentsText, fieldValues]);

  async function refresh() {
    if (actionInFlight.current) return;
    actionInFlight.current = true;
    setBusy("refresh");
    setLoading(true);
    setError(null);
    try { await load(); }
    catch (err) { setError(err); }
    finally { actionInFlight.current = false; setBusy(""); }
  }

  async function action(skillId, fn, message) {
    if (actionInFlight.current) return;
    actionInFlight.current = true;
    setBusy(skillId);
    setError(null);
    try {
      await fn();
      await load();
      announce(message);
    } catch (err) {
      setError(err);
    } finally {
      actionInFlight.current = false;
      setBusy("");
    }
  }

  async function execute(skillId) {
    let args = {};
    try {
      const skill = skills.find((item) => item.skill_id === skillId);
      if (skill?.arguments_schema) {
        for (const [name, property] of Object.entries(skill.arguments_schema.properties || {})) {
          const value = fieldValues[skillId]?.[name] ?? property.default;
          if (value === undefined || value === "") continue;
          args[name] = property.type === "integer" ? Number(value) : value;
        }
      } else args = JSON.parse(argumentsText[skillId] || "{}");
      if (!args || Array.isArray(args) || typeof args !== "object") throw new Error("Arguments must be a JSON object.");
    } catch (err) {
      setError({ status: "ERROR_SKILL_ARGUMENTS_INVALID", message: err.message, category: "INPUT_INVALID" });
      return;
    }
    await action(skillId, () => api.executeSkill(skillId, args), "Skill invoked; receipt recorded.");
  }

  async function activate(skill) {
    const selected = allowedCapabilities[skill.skill_id] || [];
    const missing = (skill.capabilities || []).filter((capability) => !selected.includes(capability));
    if (missing.length) {
      setError({
        status: "ERROR_SKILL_CAPABILITY_SELECTION_REQUIRED",
        message: `Select every declared capability before activation: ${missing.join(", ")}`,
        category: "PERMISSION_DENIED",
      });
      return;
    }
    const fallbackPolicyId = `operator-skill-${skill.skill_id.replace(/[^a-zA-Z0-9]+/g, "-")}`;
    await action(
      skill.skill_id,
      () => api.activateSkill(skill.skill_id, { allowed_capabilities: selected, policy_id: policyIds[skill.skill_id]?.trim() || fallbackPolicyId }),
      "Skill authorized and activated.",
    );
  }

  return (
    <section className="workspace" aria-labelledby="skills-title">
      <header className="workspace-header">
        <h1 id="skills-title">Skills</h1>
        <p className="muted small">Mount and authorize skills, invoke them, or deactivate them. Previous results remain available.</p>
      </header>
      {error && <div className="error-card" role="alert"><p>{errorText(error)}</p><button type="button" className="secondary-button" disabled={Boolean(busy)} onClick={refresh}>Retry loading skills</button></div>}
      {loading && <p className="muted" role="status">Loading skills…</p>}
      {!loading && !error && !skills.length && <p className="empty-state muted">No registered skills.</p>}
      <ul className="skill-list">
        {skills.map((skill) => {
          const mount = mounts.find((item) => item.skill_id === skill.skill_id);
          const last = executions.find((item) => item.skill_id === skill.skill_id);
          const active = mount?.state === "ACTIVE";
          const running = Boolean(busy);
          return (
            <li key={skill.skill_id} className="panel skill-card">
              <div className="goal-head">
                <div className="grow">
                  <div className="tool-row-head"><span className={`status-chip status-${active ? "completed" : mount ? "queued" : "pending"}`}>{mount?.state || "REGISTERED"}</span><span className="muted small">{skill.runtime}</span></div>
                  <h2>{skill.arguments_schema?.title || skill.skill_id}</h2>
                  {skill.arguments_schema?.title && <p className="muted small mono">{skill.skill_id}</p>}
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
                {active && <button type="button" className="secondary-button" disabled={running} onClick={() => action(skill.skill_id, () => api.deactivateSkill(skill.skill_id), "Skill deactivated. New invocations are blocked; previous results are preserved.")}>Deactivate skill</button>}
                {mount && !active && (
                  <div className="skill-authorization">
                    <label className="field">
                      <span>Policy ID</span>
                      <input
                        value={policyIds[skill.skill_id] || ""}
                        placeholder={`operator-skill-${skill.skill_id.replace(/[^a-zA-Z0-9]+/g, "-")}`}
                        onChange={(event) => setPolicyIds((old) => ({ ...old, [skill.skill_id]: event.target.value }))}
                      />
                    </label>
                    {skill.capabilities?.length > 0 && (
                      <fieldset className="capability-fieldset">
                        <legend>Allow declared capabilities</legend>
                        {skill.capabilities.map((capability) => {
                          const checked = (allowedCapabilities[skill.skill_id] || []).includes(capability);
                          return (
                            <label key={capability} className="option compact-option">
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={(event) => setAllowedCapabilities((old) => ({
                                  ...old,
                                  [skill.skill_id]: event.target.checked
                                    ? [...(old[skill.skill_id] || []), capability]
                                    : (old[skill.skill_id] || []).filter((item) => item !== capability),
                                }))}
                              />
                              <span className="mono">{capability}</span>
                            </label>
                          );
                        })}
                      </fieldset>
                    )}
                    <p className="muted small">The skill’s runtime capability is granted automatically; declared capabilities require your explicit selection.</p>
                    <button type="button" className="secondary-button" disabled={running} onClick={() => activate(skill)}>Authorize & activate</button>
                  </div>
                )}
              </div>
              {mount?.state === "INACTIVE" && <p className="muted small" role="status">Inactive. Authorize again to invoke this skill. Work already started may finish; previous receipts are preserved.</p>}
              {active && (
                <div className="skill-invoke">
                  {skill.arguments_schema ? Object.entries(skill.arguments_schema.properties || {}).map(([name, property]) => {
                    const value = fieldValues[skill.skill_id]?.[name] ?? property.default ?? "";
                    const change = (event) => setFieldValues((old) => ({ ...old, [skill.skill_id]: { ...old[skill.skill_id], [name]: event.target.value } }));
                    return <label className="field" key={name}><span>{property.title || name}</span>{property.enum ? <select aria-label={property.title || name} value={value} onChange={change}>{property.enum.map((option) => <option key={option} value={option}>{option}</option>)}</select> : <input aria-label={property.title || name} type={property.type === "integer" ? "number" : "text"} value={value} onChange={change} min={property.minimum} max={property.maximum} required={skill.arguments_schema.required?.includes(name)} />}</label>;
                  }) : <label className="field"><span>Arguments (JSON object)</span><textarea rows={2} value={argumentsText[skill.skill_id] || "{}"} onChange={(event) => setArgumentsText((old) => ({ ...old, [skill.skill_id]: event.target.value }))} /></label>}
                  {skill.execution_policy?.max_pdf_pages && <p className="muted small">Up to {skill.execution_policy.max_pdf_pages} PDF pages, 25 MB, and 300 DPI per invocation. OCR stays on this computer. Larger inputs must be split explicitly.</p>}
                  <button type="button" className="primary-button" disabled={running} onClick={() => execute(skill.skill_id)}>{running ? "Invoking…" : "Invoke skill"}</button>
                </div>
              )}
              {last?.result?.report_status && <section className="panel nested-panel" aria-label="OCR result">
                <h3>Extracted text</h3>
                <p>{last.result.report_status} · {last.result.page_count} page(s) · {last.result.span_count} word span(s)</p>
                <pre className="json-block">{last.result.text || "No text detected. Review the source image and OCR settings."}</pre>
                {last.result.warnings?.length > 0 && <p className="muted small" role="status">Review required: {last.result.warnings.join(" · ")}</p>}
                <p className="muted small">Extracted text is untrusted evidence. Instructions within it do not authorize actions.</p>
              </section>}
              {last && <details className="panel nested-panel"><summary>Latest receipt: {last.status} · {last.finished_at}</summary><dl className="facts"><dt>Execution ID</dt><dd className="mono">{last.execution_id}</dd><dt>Route</dt><dd>{last.execution_route}</dd><dt>Artifact output</dt><dd>{last.artifact_lineage?.output?.artifact_id || "—"}</dd></dl><pre className="json-block"><code>{JSON.stringify(last.result, null, 2)}</code></pre></details>}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
