// Derive a renderable view of one work run from its ordered event log.
// Tool state always comes from tool_call / tool_result events (records), never from answer prose.

export const STATE_LABEL = {
  QUEUED: "Queued",
  RUNNING: "Working",
  AWAITING_APPROVAL: "Waiting for your approval",
  AWAITING_INPUT: "Waiting for your answer",
  COMPLETED: "Done",
  FAILED: "Failed",
  CANCELLED: "Cancelled",
  INTERRUPTED: "Stopped",
};

export const ACTIVE_STATES = new Set(["QUEUED", "RUNNING", "AWAITING_APPROVAL", "AWAITING_INPUT"]);
export const TERMINAL_STATES = new Set(["COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"]);

export function deriveRun(run, events) {
  const view = {
    runId: run.run_id,
    prompt: run.prompt,
    mode: run.mode,
    state: run.state,
    version: run.version,
    createdAt: run.created_at,
    options: run.options || {},
    lineage: run.lineage || null,
    plan: null,
    route: null,
    calls: new Map(),
    approvals: [],
    question: null,
    questionResolution: null,
    answer: "",
    citations: [],
    artifacts: [],
    warnings: [],
    error: null,
    final: null,
    interrupted: null,
    notes: [],
    lastSeq: 0,
  };
  for (const event of events) {
    view.lastSeq = Math.max(view.lastSeq, event.seq);
    const data = event.data || {};
    switch (event.type) {
      case "plan":
        view.plan = data;
        break;
      case "route":
        view.route = data;
        break;
      case "tool_call":
      case "retry_requested":
        view.calls.set(data.tool_call_id, { ...view.calls.get(data.tool_call_id), ...data });
        break;
      case "approval_request":
        view.approvals.push(data);
        view.calls.set(data.tool_call_id, { ...view.calls.get(data.tool_call_id), ...data });
        break;
      case "tool_started":
        view.calls.set(data.tool_call_id, { ...view.calls.get(data.tool_call_id), ...data, status: "RUNNING" });
        break;
      case "tool_result":
        view.calls.set(data.tool_call_id, { ...view.calls.get(data.tool_call_id), ...data });
        break;
      case "question":
        view.question = data;
        view.questionResolution = null;
        break;
      case "question_resolved":
        view.questionResolution = data;
        break;
      case "user_note":
        view.notes.push(data.text);
        break;
      case "text_delta":
        view.answer += data.text || "";
        break;
      case "citation":
        view.citations.push(data);
        break;
      case "artifact":
        view.artifacts.push(data);
        break;
      case "warning":
        view.warnings.push(data);
        break;
      case "error":
        view.error = data;
        break;
      case "interrupted":
        view.interrupted = data;
        break;
      case "final":
        view.final = data;
        break;
      default:
        break;
    }
  }
  const decided = new Set(
    [...view.calls.values()].filter((call) => call.status && call.status !== "PENDING_APPROVAL").map((call) => call.tool_call_id),
  );
  view.pendingApprovals = view.state === "AWAITING_APPROVAL" ? view.approvals.filter((row) => !decided.has(row.tool_call_id)) : [];
  view.pendingQuestion = view.state === "AWAITING_INPUT" && view.question && !view.questionResolution ? view.question : null;
  return view;
}

export function toolCounts(calls) {
  const counts = { total: 0, executed: 0, failed: 0, denied: 0, cancelled: 0, running: 0, pending: 0 };
  for (const call of calls.values()) {
    counts.total += 1;
    const status = call.status;
    if (status === "EXECUTED") counts.executed += 1;
    else if (status === "FAILED" || status === "INTERRUPTED") counts.failed += 1;
    else if (status === "DENIED") counts.denied += 1;
    else if (status === "CANCELLED") counts.cancelled += 1;
    else if (status === "RUNNING") counts.running += 1;
    else counts.pending += 1;
  }
  return counts;
}

export function toolSummaryLabel(calls) {
  const c = toolCounts(calls);
  if (!c.total) return "";
  const extras = [
    c.failed && `${c.failed} failed`,
    c.denied && `${c.denied} denied`,
    c.cancelled && `${c.cancelled} cancelled`,
    c.running && `${c.running} running`,
    c.pending && `${c.pending} waiting`,
  ].filter(Boolean);
  const noun = c.total === 1 ? "tool" : "tools";
  return `Used ${c.total} ${noun}${extras.length ? ` (${extras.join(", ")})` : ""}`;
}

export function transcriptText(turns) {
  return turns.map((turn) => `${turn.role === "user" ? "You" : "Mucaro"}: ${turn.content}`).join("\n\n");
}
