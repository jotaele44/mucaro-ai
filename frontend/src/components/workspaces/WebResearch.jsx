import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api.js";

const SKILL = "mucaro/web-research";
const CAPS = ["filesystem.read", "filesystem.write", "model.invoke"];
const DRAFT = "mucaro.webResearch";
function readDraft() {
  try { const value = JSON.parse(sessionStorage.getItem(DRAFT) || "{}"); return value && typeof value === "object" && !Array.isArray(value) ? value : {}; }
  catch { return {}; }
}
function message(error) {
  try { const parsed = JSON.parse(error.message); return `${parsed.status || error.status}: ${parsed.detail || error.message}`; }
  catch { return error.message || "Research could not complete. Your selected observations are retained."; }
}

export function WebResearchResult({ result, onDownload, busy = false }) {
  const counts = result.comparison.counts;
  const identity = result.comparison.identity;
  const differences = result.comparison.groups.filter((group) => group.a_only_count || group.b_only_count);
  const passages = new Map(result.passages.map((row) => [row.chunk_id, row]));
  const rejected = result.synthesis.citation_status === "CITATION_ERROR";
  return <section className="panel nested-panel" aria-label="Frozen source comparison result">
    <h3>Comparison and synthesis</h3>
    <p className="muted small">{result.synthesis.mode === "extractive" ? "Offline excerpts · no model invoked" : `${result.synthesis.mode} synthesis · review required`} · no URL fetched</p>
    <div className="table-scroll"><table><caption>Passage accounting (duplicates retained)</caption><thead><tr><th scope="col">A</th><th scope="col">B</th><th scope="col">Shared</th><th scope="col">A only</th><th scope="col">B only</th><th scope="col">Union</th><th scope="col">Difference</th></tr></thead><tbody><tr>{[counts.a, counts.b, counts.intersection, counts.a_only, counts.b_only, counts.union, counts.symmetric_difference].map((count, index) => <td key={index}>{count}</td>)}</tr></tbody></table></div>
    <p>{identity.http_content_equal ? "The saved HTTP content has identical bytes." : "The saved HTTP content has different bytes."} {identity.extracted_text_equal ? "Extracted text is identical." : "Extracted text differs."} Real-world identity and the truth of differing claims remain unresolved.</p>
    <details><summary>Differing passages ({differences.length} groups)</summary>
      {!differences.length && <p>No differing passages in the normalized comparison view.</p>}
      <p className="muted small">Whitespace is collapsed for comparison. Expand exact excerpts to inspect original spelling and spacing. Differences can be additions, omissions, repetition changes, or conflicting claims.</p>
      <ol>{differences.slice(0, 20).map((group, index) => <li key={index}><p className="research-quote">{group.normalized}</p><p className="muted small">A: {group.a_occurrences.length} occurrence(s) · B: {group.b_occurrences.length} occurrence(s)</p><details><summary>Exact excerpts for difference {index + 1}</summary>{[...group.a_occurrences, ...group.b_occurrences].slice(0, 5).map((id) => <blockquote className="research-quote" key={id}>{passages.get(id)?.text}<footer className="mono small">{id}</footer></blockquote>)}{group.a_occurrences.length + group.b_occurrences.length > 5 && <p className="muted small">Showing five occurrences. The report retains every occurrence.</p>}</details></li>)}</ol>
      {differences.length > 20 && <p className="muted small">Showing 20 differing groups. Download the report for all {differences.length} groups.</p>}
    </details>
    <h4>{result.synthesis.status === "INSUFFICIENT_CONTEXT" ? "More context needed" : rejected ? "Citation validation failed" : "Source-grounded draft"}</h4>
    {rejected && <p className="error-card" role="alert">This model response has missing or invalid citation references. Review the source comparison; do not use this response as a grounded answer.</p>}
    {rejected ? <details><summary>Rejected model response (unverified)</summary><pre className="json-block">{result.synthesis.answer}</pre></details> : <p className="research-answer">{result.synthesis.answer}</p>}
    <h4>Exact cited passages</h4>
    {!result.citations.length && <p className="muted small">No verified passage citations for this response.</p>}
    {result.citations.map((citation) => <details key={citation.chunk_id} className="panel nested-panel"><summary>Source {result.sources.find((source) => source.snapshot_id === citation.source_id)?.side} · {citation.chunk_id}</summary>
      <blockquote className="research-quote">{citation.text}</blockquote>
      <dl className="facts"><dt>Captured URL</dt><dd>{citation.source_url}</dd><dt>Retrieved</dt><dd>{citation.retrieval_utc}</dd><dt>Snapshot SHA-256</dt><dd className="mono">{citation.snapshot_sha256}</dd><dt>Extracted character offsets</dt><dd>{citation.start}–{citation.end} (Unicode, not HTML bytes)</dd></dl>
    </details>)}
    <details><summary>Scope, warnings, and selection limits</summary><p className="muted small">{result.warnings.join(" · ")}</p><ul>{result.limitations.map((item) => <li key={item}>{item}</li>)}</ul></details>
    {onDownload && <button type="button" className="secondary-button" disabled={busy} onClick={onDownload}>Download verified comparison report</button>}
  </section>;
}

export function WebResearch({ announce }) {
  const [captures, setCaptures] = useState([]);
  const [page, setPage] = useState(null);
  const [mount, setMount] = useState(null);
  const [allowed, setAllowed] = useState([]);
  const [values, setValues] = useState(() => { const saved = readDraft(); return { a: typeof saved.a === "string" ? saved.a : "", b: typeof saved.b === "string" ? saved.b : "", a_sha256: typeof saved.a_sha256 === "string" ? saved.a_sha256 : "", b_sha256: typeof saved.b_sha256 === "string" ? saved.b_sha256 : "", question: typeof saved.question === "string" ? saved.question : "Compare these sources.", synthesis: ["extractive", "mock", "openai_compatible_local", "anthropic"].includes(saved.synthesis) ? saved.synthesis : "extractive" }; });
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const inFlight = useRef(false);
  const load = useCallback(async () => {
    const [collection, mounts, history] = await Promise.all([api.captures(), api.skillMounts(), api.skillExecutions()]);
    setCaptures(collection.captures); setPage(collection);
    setMount(mounts.mounts.find((row) => row.skill_id === SKILL) || null);
    setResult(history.executions.slice().reverse().find((row) => row.skill_id === SKILL && row.status === "PASS")?.result || null);
  }, []);
  useEffect(() => { load().catch(setError); }, [load]);
  useEffect(() => { try { sessionStorage.setItem(DRAFT, JSON.stringify(values)); } catch {} }, [values]);
  async function action(fn) {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError(null);
    try { await fn(); }
    catch (err) { setError(err); }
    finally { inFlight.current = false; setBusy(false); }
  }
  const change = (key) => (event) => setValues((old) => ({ ...old, [key]: event.target.value }));
  const a = captures.find((row) => row.snapshot_id === values.a);
  const b = captures.find((row) => row.snapshot_id === values.b);
  const staleBinding = (a && a.snapshot_sha256 !== values.a_sha256) || (b && b.snapshot_sha256 !== values.b_sha256);
  return <section aria-label="Compare frozen web sources">
    <p>Choose two saved observations, inspect their differences, and build a cited draft. Capture bytes are verified before use. Offline excerpts are the default.</p>
    <p className="muted small">To capture a page, use the conversation’s /fetch command and approve its tool call. The URL is not fetched again during comparison.</p>
    <button type="button" className="secondary-button" disabled={busy} onClick={() => action(load)}>Refresh saved captures</button>
    {!page && !error && <p role="status">Loading saved captures…</p>}
    {page?.total === 0 && <p className="empty-state muted">No indexed web captures yet. Capture a page or recover a saved observation first.</p>}
    {page && <p className="muted small">Showing {captures.length} of {page.total} indexed observations. Metadata is listed here; comparison verifies the selected bytes.</p>}
    {page?.next_offset !== null && page?.next_offset !== undefined && <button type="button" className="secondary-button" disabled={busy} onClick={() => action(async () => { const next = await api.captures({ offset: page.next_offset, revision: page.revision }); setCaptures((old) => [...old, ...next.captures]); setPage(next); })}>Load more captures</button>}
    {mount?.state !== "ACTIVE" ? <fieldset className="capability-fieldset" disabled={busy}><legend>Activate governed research</legend><p className="muted small">Allow reading saved captures, writing comparison reports, and invoking the model you explicitly select. Extractive mode makes no model call. Remote model mode sends the selected passages and question to that provider.</p>
      {CAPS.map((capability) => <label className="option compact-option" key={capability}><input type="checkbox" checked={allowed.includes(capability)} onChange={(event) => setAllowed((old) => event.target.checked ? [...old, capability] : old.filter((item) => item !== capability))} />Allow {capability}</label>)}
      <button type="button" className="secondary-button" disabled={busy || CAPS.some((cap) => !allowed.includes(cap))} onClick={() => action(async () => { if (!mount) await api.mountSkill(SKILL); await api.activateSkill(SKILL, { allowed_capabilities: allowed, policy_id: "operator-web-research" }); await load(); announce?.("Research skill activated."); })}>Activate research skill</button>
    </fieldset> : <p className="muted small">Research skill active. <button type="button" className="secondary-button" disabled={busy} onClick={() => action(async () => { await api.deactivateSkill(SKILL); await load(); announce?.("Research deactivated. Saved results retained."); })}>Deactivate research</button></p>}
    <form aria-busy={busy} onSubmit={(event) => { event.preventDefault(); action(async () => {
      if (!a || !b || staleBinding) throw new Error("Select both captures from the current list and review their byte bindings.");
      const response = await api.executeSkill(SKILL, { a_id: a.snapshot_id, a_sha256: values.a_sha256, b_id: b.snapshot_id, b_sha256: values.b_sha256, question: values.question, synthesis: values.synthesis });
      setResult(response.result); announce?.("Comparison saved. Review source differences and citation status.");
    }); }}>
      {["a", "b"].map((side) => <label className="field" key={side}>Source {side.toUpperCase()}<select required value={values[side]} disabled={busy} onChange={(event) => { const id = event.target.value; setValues((old) => ({ ...old, [side]: id, [`${side}_sha256`]: captures.find((row) => row.snapshot_id === id)?.snapshot_sha256 || "" })); }}><option value="">Choose a saved capture</option>{captures.map((capture) => <option value={capture.snapshot_id} key={capture.snapshot_id}>{capture.final_url} · {capture.retrieval_utc} · {capture.snapshot_id}</option>)}</select></label>)}
      {staleBinding && <p className="error-card" role="alert">A selected capture’s recorded SHA-256 changed. Its earlier binding is retained. Review the collection, then explicitly reselect the observation to accept a new binding.</p>}
      {(values.a || values.b) && <details><summary>Selected capture bindings</summary><dl className="facts">{["a", "b"].map((side) => <React.Fragment key={side}><dt>Source {side.toUpperCase()} ID / SHA-256</dt><dd className="mono">{values[side] || "Not selected"}<br />{values[`${side}_sha256`] || "Not selected"}</dd></React.Fragment>)}</dl></details>}
      <label className="field">Research question<textarea value={values.question} onChange={change("question")} disabled={busy} required maxLength={2000} /></label>
      <label className="field">Synthesis mode<select value={values.synthesis} onChange={change("synthesis")} disabled={busy}><option value="extractive">Offline excerpts (no model)</option><option value="mock">Mock model (offline test)</option><option value="openai_compatible_local">Local model (requires running server)</option><option value="anthropic">Anthropic (sends passages to provider)</option></select></label>
      {values.a && values.a === values.b && <p className="error-card">Choose distinct observations. A repeated capture is not independent corroboration.</p>}
      <button type="submit" className="primary-button" disabled={busy || mount?.state !== "ACTIVE" || !a || !b || staleBinding || a.snapshot_id === b.snapshot_id || !values.question.trim()}>{busy ? "Working on research…" : "Compare and synthesize"}</button>
    </form>
    {error && <p role="alert" className="error-card">{message(error)}</p>}
    {result && <><p className="muted small">Saved result for: {result.question}. Changing selections or the question does not change this saved result.</p><WebResearchResult result={result} busy={busy} onDownload={() => action(async () => { const blob = await api.artifactFile(result.report_artifact_id); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = "mucaro-web-comparison.json"; link.click(); URL.revokeObjectURL(url); announce?.("Verified comparison report downloaded."); })} /></>}
  </section>;
}
