import React, { useCallback, useEffect, useState } from "react";
import { api } from "../../api.js";

export function AgentConsumerPanel({ candidates, announce }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [agent, setAgent] = useState("mucaro/agent/repository-review");
  const [bindingId, setBindingId] = useState("");
  const [runId, setRunId] = useState("");
  const [result, setResult] = useState(null);
  const load = useCallback(async () => setData(await api.consumers()), []);
  useEffect(() => { load().catch(setError); }, [load]);
  async function act(fn) {
    setBusy(true); setError(null);
    try { const r = await fn(); setResult(r); await load(); announce(`Agent result: ${r?.state || r?.status || "saved"}.`); }
    catch (err) { setError(err); }
    finally { setBusy(false); }
  }
  function fields(event) { event.preventDefault(); return Object.fromEntries(new FormData(event.currentTarget)); }
  const bindings = Object.values(data?.bindings || {});
  const runs = Object.values(data?.runs || {});
  const active = runs.find((r) => r.run_id === runId);
  const eligible = candidates.filter((r) => r.state === "VERIFIED" && r.kind === "skill" && r.scope === data?.agents?.[agent]?.scope);
  return <section aria-labelledby="consumer-title">
    <h2 id="consumer-title">Agent workflows</h2>
    <p>Bind a verified procedure to a local review agent. Review and approve its exact read-only steps before execution.</p>
    {error && <p role="alert" className="error-card">{error.status}: {error.message}</p>}
    <fieldset disabled={busy}><legend>Bind a procedure</legend>
      <form className="stack-form" onSubmit={(event) => { const f = fields(event); act(async () => {
        const skill = eligible.find((r) => r.candidate_id === f.candidate_id);
        if (!skill) throw new Error("Choose a verified procedure for this agent.");
        const binding = await api.consumerBind({ agent_id: agent, candidate_id: skill.candidate_id,
          candidate_version: skill.version, artifact_sha256: skill.artifact.sha256, recipe_id: f.recipe_id, approved: f.approved === "on" });
        setBindingId(binding.binding_id); return binding;
      }); }}>
        <label className="field">Review agent<select aria-label="Review agent" value={agent} onChange={(e) => setAgent(e.target.value)}>
          {Object.entries(data?.agents || {}).map(([id, a]) => <option key={id} value={id}>{a.label}</option>)}</select></label>
        <label className="field">Verified procedure<select name="candidate_id" aria-label="Verified procedure" required>
          <option value="">Choose a procedure</option>{eligible.map((r) => <option value={r.candidate_id} key={r.candidate_id}>{r.text_raw}</option>)}</select></label>
        <label className="field">Execution recipe<select name="recipe_id" aria-label="Execution recipe" defaultValue="evidence-review/1">
          {Object.entries(data?.recipes || {}).map(([id, r]) => <option key={id} value={id}>{r.label}</option>)}</select></label>
        <label><input type="checkbox" name="approved" required /> I approve this exact procedure and recipe binding.</label>
        <button>Bind agent workflow</button>
      </form>
    </fieldset>
    <fieldset disabled={busy}><legend>Prepare a review run</legend>
      <form className="stack-form" onSubmit={(event) => { const f = fields(event); act(async () => {
        const prepared = await api.consumerPrepare({ ...f, binding_id: bindingId }); setRunId(prepared.run_id); return prepared;
      }); }}>
        <label className="field">Active workflow<select aria-label="Active workflow" value={bindingId} onChange={(e) => setBindingId(e.target.value)} required>
          <option value="">Choose a binding</option>{bindings.filter((b) => b.enabled).map((b) => <option key={b.binding_id} value={b.binding_id}>{data.agents[b.agent_id]?.label} · {data.recipes[b.recipe_id]?.label}</option>)}</select></label>
        <label className="field">Repository path<input name="repo_root" required maxLength={2000} /></label>
        <label className="field">Exact revision<input name="head_sha" required pattern="[0-9a-f]{40}" /></label>
        <label className="field">Comparison revision<input name="base_sha" required pattern="[0-9a-f]{40}" /></label>
        <label className="field">Repository-relative file<input name="file_path" required maxLength={2000} /></label>
        <label className="field">Expected file SHA256<input name="file_sha256" required pattern="[0-9a-f]{64}" /></label>
        <label className="field">Unique task key<input name="idempotency_key" required maxLength={200} /></label>
        <button>Prepare agent run</button>
      </form>
    </fieldset>
    <label className="field">Agent run<select aria-label="Agent run" value={runId} onChange={(e) => setRunId(e.target.value)}>
      <option value="">Choose a run</option>{runs.map((r) => <option key={r.run_id} value={r.run_id}>{r.state} · {r.plan.inputs.file_path}</option>)}</select></label>
    <button type="button" disabled={busy} onClick={() => act(load)}>Refresh agent state</button>
    {active && <article>
      <h3>Agent run: {active.state}</h3>
      <p>{data.agents[active.plan.binding.agent_id]?.label} · {active.plan.inputs.file_path}</p>
      <ol>{active.plan.steps.map((s, i) => <li key={i}>{s.tool_name}</li>)}</ol>
      <details><summary>Exact plan and execution receipt</summary><pre className="learning-json" tabIndex={0} aria-label="Agent execution JSON">{JSON.stringify(active, null, 2)}</pre></details>
      <fieldset disabled={busy}><legend>Run decision</legend>
        {active.state === "PREPARED" && <form className="stack-form" onSubmit={(event) => { const f = fields(event); act(() => api.consumerAction(runId, "execute", {
          version: active.version, plan_sha256: active.plan_sha256, approved: f.approved === "on" })); }}>
          <label><input name="approved" type="checkbox" required /> I approve the displayed local read-only plan.</label>
          <button>Execute agent run</button>
        </form>}
        {["PREPARED", "RUNNING"].includes(active.state) && <form className="inline-form" onSubmit={(event) => { const f = fields(event); act(() => api.consumerAction(runId, "stop", { version: active.version, reason: f.reason })); }}>
          <label className="field">Stop or recovery reason<input name="reason" required /></label><button>Stop / mark interrupted</button>
        </form>}
        {active.state === "COMPLETED" && <form className="stack-form" onSubmit={(event) => { const f = fields(event); act(() => api.consumerAction(runId, "feedback", {
          output_sha256: active.output_sha256, score: Number(f.score), criteria: f.criteria, permitted_use: f.permitted === "on" })); }}>
          <label className="field">Observed run score<input type="number" name="score" required min="-1" max="1" step="0.01" /></label>
          <label className="field">Verified outcome criteria<textarea name="criteria" required maxLength={5000} /></label>
          <label><input type="checkbox" name="permitted" required /> Use this run feedback to rank workflows for this task category.</label>
          <button>Learn from executed run</button>
        </form>}
      </fieldset>
    </article>}
    {bindingId && bindings.find((b) => b.binding_id === bindingId)?.enabled && <form className="inline-form" onSubmit={(event) => { const f = fields(event);
      const binding = bindings.find((b) => b.binding_id === bindingId); act(() => api.consumerRevoke(bindingId, { version: binding.version, reason: f.reason })); }}>
      <label className="field">Binding revocation reason<input name="reason" required /></label><button disabled={busy}>Revoke agent binding</button>
    </form>}
    {result && <details><summary>Agent action result</summary><pre className="learning-json" tabIndex={0} aria-label="Agent action JSON">{JSON.stringify(result, null, 2)}</pre></details>}
    <p className="muted small">These agents execute fixed review recipes. They do not generate code, change repository files, grant directory permissions, or train model weights. A running tool stops at its existing boundary; interrupted steps are never silently retried.</p>
  </section>;
}
