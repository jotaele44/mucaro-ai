import React, { useState } from "react";
import { api } from "../api.js";
import { STATE_LABEL, toolSummaryLabel } from "../feed.js";
import { EditableMessage } from "./MessageActions.jsx";
import { Sheet } from "./Sheet.jsx";

const OUTCOME_TEXT = {
  ACTIONS_COMPLETED: "Actions finished. Open the tool summary for each result.",
  PLAN_ONLY: "Plan only: nothing was executed.",
  STOPPED_BY_USER_CHOICE: "Stopped at your choice; nothing else ran.",
  QUESTION_DISMISSED: "The question was dismissed, so no further actions ran.",
  CANCELLED_BEFORE_START: "Cancelled before it started.",
};

function Json({ value }) {
  return (
    <pre className="json-block">
      <code>{JSON.stringify(value ?? {}, null, 2)}</code>
    </pre>
  );
}

function ApprovalCard({ request, onDecided, announce }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function decide(allow) {
    setBusy(true);
    setError(null);
    try {
      if (allow) await api.approveTool(request.tool_call_id, request.arguments_sha256);
      else await api.denyTool(request.tool_call_id, request.arguments_sha256);
      announce(`${allow ? "Allowed" : "Denied"} ${request.tool_name}.`);
      onDecided();
    } catch (err) {
      setError(`${err.status}: ${err.message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card approval-card" aria-label={`Permission request for ${request.tool_name}`}>
      <p className="card-kicker">Permission needed</p>
      <h3 className="card-title">{request.tool_name}</h3>
      <details open>
        <summary>Arguments</summary>
        <Json value={request.arguments} />
        <p className="muted small">Argument digest {request.arguments_sha256?.slice(0, 12)}… — your decision applies to exactly these arguments, once.</p>
      </details>
      {error && <p className="error-text" role="alert">{error}</p>}
      <div className="button-row">
        <button type="button" className="secondary-button" disabled={busy} onClick={() => decide(false)}>
          Deny
        </button>
        <button type="button" className="primary-button" disabled={busy} onClick={() => decide(true)}>
          Allow once
        </button>
      </div>
    </section>
  );
}

function QuestionCard({ question, onAnswered, announce }) {
  const [choice, setChoice] = useState(null);
  const [other, setOther] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const usingOther = choice === "__other__";
  const valid = choice && (!usingOther || other.trim());

  async function submit(event) {
    event.preventDefault();
    if (!valid || busy) return;
    setBusy(true);
    setError(null);
    try {
      const payload = usingOther
        ? { freeform_text: other.trim(), expected_version: question.version }
        : { option_id: choice, expected_version: question.version };
      await api.resolveInteraction(question.interaction_id, payload);
      announce("Answer submitted.");
      onAnswered();
    } catch (err) {
      setError(`${err.status}: ${err.message}`);
      setBusy(false);
    }
  }

  return (
    <form className="card question-card" onSubmit={submit} aria-label="Question from Mucaro">
      <fieldset disabled={busy}>
        <legend className="card-title">{question.question}</legend>
        {question.options.map((option) => (
          <label key={option.option_id} className={`option ${choice === option.option_id ? "selected" : ""}`}>
            <input type="radio" name={`q-${question.interaction_id}`} value={option.option_id} checked={choice === option.option_id} onChange={() => setChoice(option.option_id)} />
            <span>
              <strong>{option.label}</strong>
              {option.consequence && <span className="muted small block">{option.consequence}</span>}
            </span>
          </label>
        ))}
        {question.allow_freeform && (
          <label className={`option ${usingOther ? "selected" : ""}`}>
            <input type="radio" name={`q-${question.interaction_id}`} value="__other__" checked={usingOther} onChange={() => setChoice("__other__")} />
            <span className="grow">
              <strong>Other</strong>
              {usingOther && (
                <textarea className="other-input" aria-label="Your answer" value={other} onChange={(event) => setOther(event.target.value)} rows={2} />
              )}
            </span>
          </label>
        )}
      </fieldset>
      {error && <p className="error-text" role="alert">{error}</p>}
      <div className="button-row">
        <button type="submit" className="primary-button" disabled={!valid || busy}>
          {busy ? "Submitting…" : "Submit"}
        </button>
      </div>
    </form>
  );
}

function ToolDrawer({ calls, onClose }) {
  return (
    <Sheet title="Tool activity" onClose={onClose}>
      <p className="muted small">States come from the tool call records, not from the assistant's text.</p>
      <ul className="tool-list">
        {[...calls.values()].map((call) => (
          <li key={call.tool_call_id} className="tool-row">
            <div className="tool-row-head">
              <span className={`status-chip status-${(call.status || "PENDING").toLowerCase()}`}>{call.status || "PENDING"}</span>
              <strong>{call.tool_name}</strong>
              {call.retry_of && <span className="muted small">retry of {call.retry_of.slice(0, 16)}…</span>}
            </div>
            <details>
              <summary>Input</summary>
              <Json value={call.arguments} />
            </details>
            {call.error && (
              <p className="error-text">
                {call.error.category}: {call.error.status} {call.error.detail || ""}
              </p>
            )}
            {call.result && (
              <details>
                <summary>Output ({call.result_status})</summary>
                <Json value={call.result} />
              </details>
            )}
          </li>
        ))}
      </ul>
    </Sheet>
  );
}

function hostOf(url) {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

function Citations({ citations, onOpenCitation }) {
  if (!citations.length) return null;
  return (
    <div className="citations" aria-label="Citations">
      {citations.map((citation) => (
        <button
          key={`${citation.chunk_id}-${citation.citation_order}`}
          type="button"
          className="citation-chip"
          onClick={() => onOpenCitation(citation)}
          aria-label={`Open citation ${citation.citation_order}: ${citation.source_url || citation.source_id}`}
        >
          [{citation.citation_order}] {citation.source_url ? hostOf(citation.source_url) : citation.source_id.slice(0, 14)}
          {citation.trust === "UNTRUSTED_CONTENT" && <span className="sr-only"> (untrusted content)</span>}
        </button>
      ))}
    </div>
  );
}

export function RunBlock({ view, onChanged, onOpenCitation, announce, messageEdits = {}, onEditMessage }) {
  const [toolsOpen, setToolsOpen] = useState(false);
  const toolLabel = toolSummaryLabel(view.calls);
  const planSteps = view.plan?.steps || [];
  const userMessageKey = `run:${view.runId}:user`;
  const assistantMessageKey = `run:${view.runId}:assistant`;
  const userEdit = messageEdits[userMessageKey];
  const assistantEdit = messageEdits[assistantMessageKey];
  const compactCompleted = view.state === "COMPLETED" && Boolean(view.answer) && !view.error && !view.interrupted;

  return (
    <article className="run" aria-busy={view.state === "RUNNING"} data-state={view.state}>
      <div className="bubble user-bubble">
        <EditableMessage
          text={String(userEdit?.content ?? view.prompt ?? "")}
          revision={Number(userEdit?.revision || 0)}
          edited={Boolean(userEdit)}
          onSave={(content, revision) => onEditMessage(userMessageKey, content, revision)}
          announce={announce}
        >
          {view.mode !== "ask" && <span className="mode-tag">{view.mode}</span>}
        </EditableMessage>
      </div>

      <div className="assistant-block">
        {compactCompleted ? (
          <details className="run-receipt">
            <summary>
              <span className="status-chip status-completed">{STATE_LABEL[view.state] || view.state}</span>
              <span>Run details</span>
              {(planSteps.length > 0 || toolLabel) && (
                <span className="muted small">
                  {planSteps.length > 0 ? `${planSteps.length} plan step${planSteps.length === 1 ? "" : "s"}` : ""}
                  {planSteps.length > 0 && toolLabel ? " · " : ""}
                  {toolLabel || ""}
                </span>
              )}
            </summary>
            {(planSteps.length > 0 || view.route) && (
              <div className="run-receipt-body">
                {planSteps.length > 0 && (
                  <ol>
                    {planSteps.map((step, index) => (
                      <li key={index}>
                        {step.step}
                        {step.tool && step.requires_approval && <span className="muted small"> — needs approval</span>}
                      </li>
                    ))}
                  </ol>
                )}
                {view.route && (
                  <p className="muted small">
                    Model: {view.route.selected_provider || "none"} ({view.route.selection_reason || view.route.status})
                  </p>
                )}
                {toolLabel && (
                  <button type="button" className="tool-summary" onClick={() => setToolsOpen(true)} aria-haspopup="dialog">
                    {toolLabel}
                  </button>
                )}
              </div>
            )}
          </details>
        ) : (
          <>
            <div className="run-status">
              <span className={`status-chip status-${view.state.toLowerCase()}`}>{STATE_LABEL[view.state] || view.state}</span>
              {view.state === "RUNNING" && <span className="spinner" aria-hidden="true" />}
              {(planSteps.length > 0 || view.route) && (
                <details className="plan-details">
                  <summary>Plan · {planSteps.length} step{planSteps.length === 1 ? "" : "s"}</summary>
                  <ol>
                    {planSteps.map((step, index) => (
                      <li key={index}>
                        {step.step}
                        {step.tool && step.requires_approval && <span className="muted small"> — needs approval</span>}
                      </li>
                    ))}
                  </ol>
                  {view.route && (
                    <p className="muted small">
                      Model: {view.route.selected_provider || "none"} ({view.route.selection_reason || view.route.status})
                    </p>
                  )}
                </details>
              )}
            </div>

            {toolLabel && (
              <button type="button" className="tool-summary" onClick={() => setToolsOpen(true)} aria-haspopup="dialog">
                {toolLabel}
              </button>
            )}
          </>
        )}

        {view.pendingApprovals.map((request) => (
          <ApprovalCard key={request.tool_call_id} request={request} onDecided={onChanged} announce={announce} />
        ))}

        {view.pendingQuestion && <QuestionCard question={view.pendingQuestion} onAnswered={onChanged} announce={announce} />}
        {view.question && view.questionResolution && (
          <p className="receipt">
            <span className="muted">{view.question.question}</span>
            <br />
            You chose:{" "}
            <strong>
              {view.questionResolution.freeform_text ||
                view.question.options.find((option) => option.option_id === view.questionResolution.option_id)?.label ||
                view.questionResolution.status}
            </strong>
          </p>
        )}

        {view.answer && (
          <div className="bubble assistant-bubble">
            <EditableMessage
              text={String(assistantEdit?.content ?? view.answer ?? "")}
              revision={Number(assistantEdit?.revision || 0)}
              edited={Boolean(assistantEdit)}
              onSave={(content, revision) => onEditMessage(assistantMessageKey, content, revision)}
              announce={announce}
              allowReadAloud
            />
          </div>
        )}
        <Citations citations={view.citations} onOpenCitation={onOpenCitation} />

        {view.warnings.map((warning, index) => (
          <p key={index} className="notice" role="note">
            {warning.category === "UNTRUSTED_CONTENT"
              ? "Some sources contain instruction-like text. It was treated as data only and nothing was executed."
              : `${warning.status}${warning.detail ? `: ${warning.detail}` : ""}`}
          </p>
        ))}
        {view.error && (
          <p className="error-card" role="alert">
            <strong>{view.error.category}</strong> — {view.error.status}
            {view.error.detail ? `: ${view.error.detail}` : ""}
          </p>
        )}
        {view.interrupted && (
          <p className="notice">
            Stopped ({view.interrupted.reason}).
            {view.interrupted.in_flight_at_stop?.length > 0 && " An action was already running when you stopped; its result is recorded in Tool activity."}
          </p>
        )}
        {view.final && !view.answer && view.final.outcome !== "ANSWERED" && (
          <p className="outcome">{OUTCOME_TEXT[view.final.outcome] || `Outcome: ${view.final.outcome}`}</p>
        )}
      </div>
      {toolsOpen && <ToolDrawer calls={view.calls} onClose={() => setToolsOpen(false)} />}
    </article>
  );
}
