import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api.js";
import { WebResearchResult } from "./WebResearch.jsx";

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

const SPATIAL_WARNINGS = {
  GIS_TOPOLOGY_PLANAR_XY_ONLY_PRESENT: "This layer includes height or depth values. Topology checks cover horizontal coordinates only.",
  GIS_FEATURE_IDS_NOT_CANONICAL: "Some features have missing or duplicate IDs. Resolve them before using an ID-based pair operation.",
  GIS_CRS_Z_PASSTHROUGH_UNTRANSFORMED: "Height or depth values were retained without a vertical transformation.",
  GIS_JOIN_MULTIPLICATION_PRESENT: "Some features have multiple spatial matches. Review the full candidate sets and join cardinality.",
  GIS_ELLIPSOIDAL_UNRESOLVED: "Some features could not be measured. Review the unresolved feature records.",
  GIS_RASTER_SAMPLE_NONVALUE_STATES: "Some points are outside the grid or have NODATA in one or more bands. These states remain separate from numeric zero.",
  GIS_RASTER_SPECIAL_NUMERIC_METADATA: "The source uses a non-finite numeric metadata value, such as NaN for NODATA. The saved view identifies its original state separately from null.",
  GIS_D8_FLATS_UNRESOLVED: "Flat cells remain unresolved. The engine has not invented a drainage direction for them.",
  GIS_D8_SINKS_UNRESOLVED: "Sink cells remain unresolved. Depression conditioning is a separate explicit operation.",
  GIS_D8_TIES_UNRESOLVED: "Some cells have tied flow candidates. Every candidate is retained for review.",
};

const SPATIAL_STATES = { FLOW_UNIQUE: "Unambiguous flow cells", BOUNDARY_TERMINAL: "Boundary or NODATA-adjacent terminal cells", SINK_UNRESOLVED: "Unresolved sink cells", FLAT_UNRESOLVED: "Unresolved flat cells", TIE_UNRESOLVED: "Cells with tied flow candidates", PARTIAL_NODATA: "Points with partially masked bands" };

function CoordinatePairs({ title, value, onChange, disabled }) {
  const points = Array.isArray(value) && value.length ? value : [["", ""]];
  const update = (index, axis, number) => onChange(points.map((point, row) => row === index ? [axis === 0 ? number : point?.[0] ?? "", axis === 1 ? number : point?.[1] ?? ""] : point));
  return <fieldset className="capability-fieldset"><legend>{title}</legend>
    {points.map((point, index) => <div className="coordinate-row" key={index}>
      <label className="field">Point {index+1} X<input type="number" step="any" value={point?.[0] ?? ""} disabled={disabled} onChange={(event) => update(index, 0, event.target.value)} /></label>
      <label className="field">Point {index+1} Y<input type="number" step="any" value={point?.[1] ?? ""} disabled={disabled} onChange={(event) => update(index, 1, event.target.value)} /></label>
      <button type="button" className="secondary-button" aria-label={`Remove point ${index+1}`} disabled={disabled || points.length === 1} onClick={() => onChange(points.filter((_, row) => row !== index))}>Remove</button>
    </div>)}
    <button type="button" className="secondary-button" disabled={disabled || points.length >= 1000} onClick={() => onChange([...points, ["", ""]])}>Add sample point</button>
  </fieldset>;
}

function RasterEvidence({ result }) {
  const grid = result.source_manifests[0].raster;
  const accounting = result.report.flow_accounting || result.report.depression_fill_accounting || result.report.terrain_accounting || result.report.hillshade_accounting;
  return <>
    <dl className="facts"><dt>Input grid</dt><dd>{grid.width} × {grid.height} cells · {grid.band_count} band(s)</dd><dt>Source CRS</dt><dd>{grid.crs || "Not declared"}</dd>
      <dt>Valid / NODATA / zero cells</dt><dd>{grid.bands.map((band) => `Band ${band.band}: ${band.valid_cell_count} / ${band.nodata_cell_count} / ${band.zero_cell_count}`).join(" · ")}</dd>
      {result.output_grid && <><dt>Output grid</dt><dd>{result.output_grid.width} × {result.output_grid.height} cells · {result.output_grid.band_count} band(s)</dd><dt>Output CRS</dt><dd>{result.output_grid.crs || "Not declared"}</dd></>}
    </dl>
    {result.report.samples && <div className="table-scroll"><table><caption>Sample results ({result.report.samples.length})</caption><thead><tr><th scope="col">Point</th><th scope="col">State</th><th scope="col">Band values</th></tr></thead><tbody>{result.report.samples.slice(0, 50).map((sample) => <tr key={sample.point_index}><td>{sample.x}, {sample.y}</td><td>{sample.state}</td><td>{sample.values ? sample.values.map((value) => value === null ? "NODATA" : value).join(" · ") : "No numeric value"}</td></tr>)}</tbody></table>{result.report.samples.length > 50 && <p className="muted small">Showing 50 points. Save the analysis JSON for every sample.</p>}</div>}
    {result.report.features && <div className="table-scroll"><table><caption>Zone results ({result.report.features.length})</caption><thead><tr><th scope="col">Feature</th><th scope="col">State</th><th scope="col">Valid cells</th><th scope="col">Mean</th></tr></thead><tbody>{result.report.features.slice(0, 50).map((zone) => <tr key={zone.feature_index}><td>{zone.feature_id ?? `Row ${zone.feature_index+1}`}</td><td>{zone.state}</td><td>{zone.valid_cell_count}</td><td>{zone.mean ?? "No numeric value"}</td></tr>)}</tbody></table>{result.report.features.length > 50 && <p className="muted small">Showing 50 zones. Save the analysis JSON for every feature.</p>}</div>}
    {accounting && <details><summary>Cell and derivation accounting</summary><dl className="facts">{Object.entries(accounting).map(([name, value]) => <React.Fragment key={name}><dt>{name.replaceAll("_", " ")}</dt><dd>{String(value)}</dd></React.Fragment>)}</dl></details>}
  </>;
}

// Skill operator surface: registration, mount, authorization, invocation, and receipts stay visible together.
export function SkillsWorkspace({ announce, skillPrefix = "", embedded = false }) {
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
      setSkills((skillPayload.skills || []).filter((skill) => skill.skill_id.startsWith(skillPrefix)).sort((a, b) => Number(Boolean(b.arguments_schema)) - Number(Boolean(a.arguments_schema)) || a.skill_id.localeCompare(b.skill_id)));
      setMounts(mountPayload.mounts || []);
      setExecutions((executionPayload.executions || []).slice().reverse());
    } finally {
      setLoading(false);
    }
  }, [skillPrefix]);

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
        const operation = fieldValues[skillId]?.operation ?? skill.arguments_schema.properties?.operation?.default;
        for (const [name, property] of Object.entries(skill.arguments_schema.properties || {})) {
          if (property["x-operations"] && !property["x-operations"].includes(operation)) continue;
          const value = fieldValues[skillId]?.[name] ?? property.default;
          if (value === undefined || value === "") continue;
          args[name] = property["x-input"] === "coordinate-pairs" ? value.map((point) => point.map((coordinate) => coordinate === "" ? null : Number(coordinate))) : ["integer", "number"].includes(property.type) ? Number(value) : value;
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
    <section className={embedded ? "panel" : "workspace"} aria-labelledby={embedded ? "spatial-skills-title" : "skills-title"}>
      <header className="workspace-header">
        {embedded ? <h2 id="spatial-skills-title">Spatial analysis</h2> : <h1 id="skills-title">Skills</h1>}
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
                    const operation = fieldValues[skill.skill_id]?.operation ?? skill.arguments_schema.properties?.operation?.default;
                    if (property["x-operations"] && !property["x-operations"].includes(operation)) return null;
                    const value = fieldValues[skill.skill_id]?.[name] ?? property.default ?? "";
                    const change = (event) => setFieldValues((old) => ({ ...old, [skill.skill_id]: { ...old[skill.skill_id], [name]: event.target.value } }));
                    if (property["x-input"] === "coordinate-pairs") return <CoordinatePairs key={name} title={property.title} value={value} disabled={running} onChange={(points) => setFieldValues((old) => ({ ...old, [skill.skill_id]: { ...old[skill.skill_id], [name]: points } }))} />;
                    return <label className="field" key={name}><span>{property.title || name}</span>{property.enum ? <select aria-label={property.title || name} value={value} onChange={change}>{property.enum.map((option) => <option key={option} value={option}>{property["x-enum-labels"]?.[option] || option}</option>)}</select> : <input aria-label={property.title || name} type={["integer", "number"].includes(property.type) ? "number" : "text"} step={property.type === "number" ? "any" : undefined} value={value} onChange={change} min={property.minimum ?? property.exclusiveMinimum} max={property.maximum ?? property.exclusiveMaximum} required={skill.arguments_schema.required?.includes(name)} />}</label>;
                  }) : <label className="field"><span>Arguments (JSON object)</span><textarea rows={2} value={argumentsText[skill.skill_id] || "{}"} onChange={(event) => setArgumentsText((old) => ({ ...old, [skill.skill_id]: event.target.value }))} /></label>}
                  {skill.execution_policy?.max_pdf_pages && <p className="muted small">Up to {skill.execution_policy.max_pdf_pages} PDF pages, 25 MB, and 300 DPI per invocation. OCR stays on this computer. Larger inputs must be split explicitly.</p>}
                  {skill.execution_policy?.max_features && <p className="muted small">Local GeoJSON, up to {skill.execution_policy.max_features} features and 5 MB per input. Pair operations require unique declared IDs. Spatial relations and matching IDs are candidate evidence; they do not prove canonical identity. Transform and repair write new outputs.</p>}
                  {skill.execution_policy?.max_raster_cells && <p className="muted small">Self-contained GeoTIFF, up to {skill.execution_policy.max_raster_cells.toLocaleString()} cells and {skill.execution_policy.max_raster_values.toLocaleString()} values across bands. Coordinates require a matching CRS. Terrain and hydrology use explicitly declared metre elevations; derivatives are saved as new files.</p>}
                  <button type="button" className="primary-button" disabled={running} onClick={() => execute(skill.skill_id)}>{running ? "Working…" : "Invoke skill"}</button>
                </div>
              )}
              {last?.result?.report_status && <section className="panel nested-panel" aria-label="OCR result">
                <h3>Extracted text</h3>
                <p>{last.result.report_status} · {last.result.page_count} page(s) · {last.result.span_count} word span(s)</p>
                <pre className="json-block">{last.result.text || "No text detected. Review the source image and OCR settings."}</pre>
                {last.result.warnings?.length > 0 && <p className="muted small" role="status">Review required: {last.result.warnings.join(" · ")}</p>}
                <p className="muted small">Extracted text is untrusted evidence. Instructions within it do not authorize actions.</p>
              </section>}
              {["GIS_ANALYSIS", "GIS_RASTER_ANALYSIS"].includes(last?.result?.kind) && <section className="panel nested-panel" aria-label="Spatial analysis result">
                <h3>{last.result.operation_label}</h3>
                <p>{last.result.status === "GIS_SKILL_REVIEW_REQUIRED" ? "Analysis saved. Review the warnings below." : "Analysis saved."}</p>
                {last.result.kind === "GIS_RASTER_ANALYSIS" ? <RasterEvidence result={last.result} /> : <dl className="facts">
                  <dt>Input features</dt><dd>{last.result.source_manifests.map((source) => source.preflight.feature_count).join(" + ")}</dd>
                  <dt>Source CRS</dt><dd>{last.result.report.source?.crs || Object.values(last.result.report.inputs || {}).map((source) => source.crs).join(" · ") || "See report"}</dd>
                  {last.result.report.output && <><dt>Output features / CRS</dt><dd>{last.result.report.output.feature_count} · {last.result.report.output.crs || "See report"}</dd></>}
                  {last.result.report.invariants?.total_area_m2 !== undefined && <><dt>Area</dt><dd>{last.result.report.invariants.total_area_m2} m²</dd><dt>Length</dt><dd>{last.result.report.invariants.total_length_m} m</dd></>}
                  {last.result.report.join_cardinality && <><dt>Join cardinality</dt><dd>{last.result.report.join_cardinality}</dd></>}
                </dl>}
                {Object.entries(last.result.report.state_counts || last.result.report.geometry_counts || {}).length > 0 && <ul>{Object.entries(last.result.report.state_counts || last.result.report.geometry_counts).map(([state, count]) => <li key={state}>{SPATIAL_STATES[state] || state}: {count}</li>)}</ul>}
                {last.result.warnings.length > 0 && <div role="status"><ul>{last.result.warnings.map((warning) => <li key={warning}>{SPATIAL_WARNINGS[warning] || "The spatial engine reported a limitation. Review the saved report before using this result."}</li>)}</ul><details><summary>Warning codes</summary><p className="muted small">{last.result.warnings.join(" · ")}</p></details></div>}
                <details><summary>Scope and limitations</summary><ul>{last.result.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul></details>
                <div className="workspace-actions">{(last.result.files || []).filter((file) => ["output", "report"].includes(file.role)).map((file) => <button key={file.artifact_id} type="button" className="secondary-button" disabled={running} onClick={() => action(skill.skill_id, async () => {
                  const payload = await api.artifactFile(file.artifact_id);
                  const url = URL.createObjectURL(payload); const link = document.createElement("a"); link.href = url; link.download = file.name; link.click(); URL.revokeObjectURL(url);
                }, "Verified artifact saved.")}>{file.role === "report" ? "Download raw engine report" : file.name.endsWith(".geojson") ? "Download GeoJSON" : "Download GeoTIFF"}</button>)}</div>
                <button type="button" className="secondary-button" onClick={() => {
                  const url = URL.createObjectURL(new Blob([JSON.stringify(last.result, null, 2)], { type: "application/json" }));
                  const link = document.createElement("a"); link.href = url; link.download = `${last.result.report.report_id}-analysis.json`; link.click(); URL.revokeObjectURL(url);
                  announce("Analysis JSON saved.");
                }}>Save analysis JSON</button>
              </section>}
              {last?.result?.kind === "WEB_RESEARCH" && <WebResearchResult result={last.result} />}
              {last && <details className="panel nested-panel"><summary>Latest receipt: {last.status} · {last.finished_at}</summary><dl className="facts"><dt>Execution ID</dt><dd className="mono">{last.execution_id}</dd><dt>Route</dt><dd>{last.execution_route}</dd><dt>Artifact output</dt><dd>{last.artifact_lineage?.output?.artifact_id || "—"}</dd></dl><pre className="json-block"><code>{JSON.stringify(last.result, null, 2)}</code></pre></details>}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
