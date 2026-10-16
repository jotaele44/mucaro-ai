import React, { useCallback, useEffect, useState } from "react";
import { api } from "../../api.js";

export function LearningWorkspace({ announce }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState("");
  const [result, setResult] = useState(null);
  const load = useCallback(async () => setData(await api.learning()), []);
  useEffect(() => { load().catch(setError); }, [load]);
  const rows = Object.values(data?.candidates || {});
  const row = rows.find((item) => item.candidate_id === selected);
  async function act(fn) {
    setBusy(true); setError(null);
    try { const response = await fn(); setResult(response); await load(); announce("Learning receipt saved."); }
    catch (err) { setError(err); }
    finally { setBusy(false); }
  }
  function fields(event) { event.preventDefault(); return Object.fromEntries(new FormData(event.currentTarget)); }
  return <section className="workspace learning-workspace" aria-labelledby="learning-title">
    <header className="workspace-header"><h1 id="learning-title">Learning</h1>
      <p>Recognize lessons, review evidence and retain verified improvements. Model-weight training is not connected.</p></header>
    {error && <p role="alert" className="error-card">{error.status}: {error.message}</p>}
    <form className="inline-form" onSubmit={(event) => { const f = fields(event); act(() => api.learningCapture({
      enabled: f.enabled === "on", purpose: f.purpose })); }}>
      <label><input type="checkbox" name="enabled" defaultChecked={data?.capture?.enabled} key={String(data?.capture?.enabled)} />
        Authorize automatic candidate recognition from future saved runs</label>
      <label className="field">Permitted purpose<input name="purpose" required maxLength={500} /></label>
      <button disabled={busy}>Save capture policy</button>
      <p className="muted small">Saved run evidence stays local. Recognition creates review candidates; it never grants approval.</p>
    </form>
    <fieldset disabled={busy}>
      <legend>Recognize a lesson</legend>
      <form className="stack-form" onSubmit={(event) => { const f = fields(event); act(async () => {
        const candidate = await api.learningRecognize({ ...f, kind: f.kind || null, permitted_use: f.permitted_use === "on" });
        setSelected(candidate.candidate_id); return candidate;
      }); }}>
        <label className="field">Lesson<textarea name="text" required maxLength={800} /></label>
        <label className="field">Source ID<input name="source_id" required maxLength={200} /></label>
        <label className="field">Original evidence<textarea name="source_bytes" required maxLength={100000} /></label>
        <label className="field">Permitted purpose<input name="purpose" required maxLength={500} /></label>
        <label className="field">Destination<select name="kind"><option value="">Recognize automatically</option>
          {Object.keys(data?.owners || {}).map((kind) => <option key={kind}>{kind}</option>)}</select></label>
        <label className="field">Scope<input name="scope" defaultValue="general" required maxLength={100} /></label>
        <label><input type="checkbox" name="permitted_use" required /> I authorize storing and reviewing these bytes for this purpose.</label>
        <button className="primary-button">Recognize and route</button>
      </form>
      <form className="inline-form" onSubmit={(event) => { const f = fields(event); act(() => api.learningScan(f.run_id,
        { purpose: f.purpose, permitted_use: f.permitted_use === "on" })); }}>
        <label className="field">Existing run ID<input name="run_id" required /></label>
        <label className="field">Permitted purpose<input name="purpose" required maxLength={500} /></label>
        <label><input name="permitted_use" type="checkbox" required /> Permit use of this run</label>
        <button>Recognize from run</button>
      </form>
    </fieldset>
    <label className="field">Review queue<select value={selected} onChange={(event) => { setSelected(event.target.value); setResult(null); }}>
      <option value="">Choose a candidate</option>{rows.map((item) => <option value={item.candidate_id} key={item.candidate_id}>
        {item.state} · {item.text_raw.slice(0, 70)}</option>)}</select></label>
    {row && <article key={row.candidate_id}>
      <h2>{row.state} · version {row.version}</h2><p>{row.text_raw}</p>
      <p>Destination: {row.owner || "Unresolved"} · scope: {row.scope}</p>
      <p>Proposed addition: <strong>{row.text_raw}</strong></p>
      <details><summary>Evidence and receipt details</summary><pre className="learning-json" tabIndex={0} aria-label="Learning evidence JSON">{JSON.stringify(row, null, 2)}</pre></details>
      {row.signals.length > 0 && <p role="status">Instruction-like signals require review: {row.signals.join(", ")}</p>}
      <fieldset disabled={busy}>
        <legend>Candidate decision</legend>
        {["REVIEW", "UNRESOLVED"].includes(row.state) && <form className="inline-form" onSubmit={(event) => {
          const f = fields(event); act(() => api.learningAction(selected, "resolve", { ...f, version: row.version })); }}>
          <label className="field">Destination<select name="kind">{Object.keys(data.owners).map((kind) => <option key={kind}>{kind}</option>)}</select></label>
          <label className="field">Scope<input name="scope" defaultValue={row.scope} required /></label>
          <label className="field">Resolution rationale<input name="rationale" required /></label><button>Resolve destination</button>
        </form>}
        {row.state === "REVIEW" && <form className="stack-form" onSubmit={(event) => {
          const f = fields(event); act(() => api.learningAction(selected, "review", { version: row.version,
            accept: f.decision === "accept", rationale: f.rationale, positive_case: f.positive_case, negative_case: f.negative_case,
            conflict_ids: f.conflicts.trim() ? f.conflicts.split(",").map((s) => s.trim()) : [],
            explicit_approval: f.approval === "on", verified_evidence: f.evidence === "on", authority_safe: f.authority === "on" })); }}>
          <label className="field">Decision<select name="decision"><option value="reject">Reject</option><option value="accept">Accept</option></select></label>
          <label className="field">Rationale<textarea name="rationale" required /></label>
          <label className="field">Positive case<textarea name="positive_case" /></label>
          <label className="field">Negative case<textarea name="negative_case" /></label>
          <label className="field">Unresolved conflict IDs (comma separated)<input name="conflicts" /></label>
          <label><input type="checkbox" name="evidence" /> I independently verified the evidence.</label>
          <label><input type="checkbox" name="authority" /> I reviewed scope, conflicts and authority boundaries.</label>
          <label><input type="checkbox" name="approval" /> I explicitly approve this exact addition.</label>
          <button>Record review</button>
        </form>}
        {row.state === "ACCEPTED" && <button onClick={() => act(() => api.learningAction(selected, "apply", { version: row.version }))}>Apply reviewed artifact</button>}
        {row.state === "APPLIED" && <form className="stack-form" onSubmit={(event) => {
          const f = fields(event); act(() => api.learningAction(selected, "verify", { version: row.version,
            positive_pass: f.positive === "on", negative_pass: f.negative === "on", evaluation_receipt: f.receipt })); }}>
          <label className="field">Evaluation evidence / receipt<textarea name="receipt" required /></label>
          <label><input type="checkbox" name="positive" required /> Positive case passed</label>
          <label><input type="checkbox" name="negative" required /> Negative case passed</label>
          <button>Verify artifact</button>
        </form>}
        {["APPLIED", "VERIFIED"].includes(row.state) && <form className="inline-form" onSubmit={(event) => {
          const f = fields(event); act(() => api.learningAction(selected, "rollback", { version: row.version, reason: f.reason })); }}>
          <label className="field">Rollback reason<input name="reason" required /></label><button>Roll back</button>
        </form>}
        {row.state === "VERIFIED" && row.kind === "skill" && <form className="stack-form" onSubmit={(event) => {
          const f = fields(event); act(() => api.learningFeedback({ episode_id: f.episode_id, candidate_id: selected,
            context: row.scope, reward: Number(f.reward), evidence: f.evidence, permitted_use: f.permitted_use === "on" })); }}>
          <h3>Reinforcement feedback</h3>
          <label className="field">Unique episode ID<input name="episode_id" required /></label>
          <label className="field">Reward (−1 to 1)<input name="reward" type="number" min="-1" max="1" step="0.1" required /></label>
          <label className="field">Observed outcome evidence<textarea name="evidence" required /></label>
          <label><input name="permitted_use" type="checkbox" required /> Authorize this feedback for policy learning</label>
          <button>Record reward</button>
          <button type="button" onClick={() => act(() => api.learningRecommend(row.scope))}>Evaluate recommendation</button>
        </form>}
      </fieldset>
    </article>}
    {result && <details open><summary>Latest result</summary><pre className="learning-json" tabIndex={0} aria-label="Learning evidence JSON">{JSON.stringify(result, null, 2)}</pre></details>}
    <details><summary>Audit receipts ({data?.receipts?.length || 0})</summary>
      <pre className="learning-json" tabIndex={0} aria-label="Learning evidence JSON">{JSON.stringify(data?.receipts || [], null, 2)}</pre></details>
    <p className="muted small">Review and verification are operator attestations. Skill artifacts are reusable procedures; they do not install executable code or start worker agents.</p>
  </section>;
}
