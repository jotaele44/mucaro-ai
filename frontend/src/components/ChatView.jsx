import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, streamRunEvents } from "../api.js";
import { ACTIVE_STATES, deriveRun, transcriptText } from "../feed.js";
import { Composer } from "./Composer.jsx";
import { EvidencePanel } from "./EvidencePanel.jsx";
import { EditableMessage } from "./MessageActions.jsx";
import { RunBlock } from "./RunBlock.jsx";
import { Sheet } from "./Sheet.jsx";

function ConversationMenu({ conversation, onRename, onDelete, onOpenTasks, announce }) {
  const [open, setOpen] = useState(false);
  const [dialog, setDialog] = useState(null);
  const [title, setTitle] = useState(conversation?.title || "");

  async function copyTranscript() {
    setOpen(false);
    try {
      await navigator.clipboard.writeText(transcriptText(conversation?.turns || []));
      announce("Transcript copied.");
    } catch {
      announce("Copy failed: clipboard access was not allowed.");
    }
  }

  return (
    <div className="menu-wrap">
      <button type="button" className="icon-button" aria-haspopup="menu" aria-expanded={open} aria-label="Conversation menu" onClick={() => setOpen((value) => !value)}>
        ⋯
      </button>
      {open && (
        <ul className="menu" role="menu">
          <li role="none"><button role="menuitem" type="button" onClick={() => { setOpen(false); setTitle(conversation?.title || ""); setDialog("rename"); }}>Rename</button></li>
          <li role="none"><button role="menuitem" type="button" onClick={copyTranscript}>Copy transcript</button></li>
          <li role="none"><button role="menuitem" type="button" onClick={() => { setOpen(false); onOpenTasks(); }}>Background tasks</button></li>
          <li role="none"><button role="menuitem" type="button" className="danger-text" onClick={() => { setOpen(false); setDialog("delete"); }}>Delete…</button></li>
        </ul>
      )}
      {dialog === "rename" && (
        <Sheet title="Rename conversation" onClose={() => setDialog(null)} side="center">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (title.trim()) onRename(title.trim()).then(() => setDialog(null));
            }}
          >
            <label className="field">
              <span>Title</span>
              <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={120} />
            </label>
            <div className="button-row">
              <button type="button" className="secondary-button" onClick={() => setDialog(null)}>Cancel</button>
              <button type="submit" className="primary-button" disabled={!title.trim()}>Save</button>
            </div>
          </form>
        </Sheet>
      )}
      {dialog === "delete" && (
        <Sheet title="Delete conversation?" onClose={() => setDialog(null)} side="center">
          <p>This permanently deletes the conversation and its transcript from this Mac. Sources, memory and receipts are kept. There is no archive or undo.</p>
          <div className="button-row">
            <button type="button" className="secondary-button" onClick={() => setDialog(null)}>Keep it</button>
            <button type="button" className="danger-button" onClick={() => onDelete().then(() => setDialog(null))}>Delete permanently</button>
          </div>
        </Sheet>
      )}
    </div>
  );
}

export function ChatView({ conversationId, onConversationCreated, onConversationsChanged, onOpenTasks, announce, modes, defaultOptions }) {
  const [conversation, setConversation] = useState(null);
  const [runs, setRuns] = useState(new Map());
  const [summaries, setSummaries] = useState([]);
  const [mode, setMode] = useState("ask");
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [citation, setCitation] = useState(null);
  const streams = useRef(new Map());
  const caughtUp = useRef(new Set());
  const refreshRef = useRef(null);
  const feedRef = useRef(null);
  const stick = useRef(true);

  const loadRun = useCallback(async (runId) => {
    const payload = await api.runEvents(runId);
    setRuns((previous) => new Map(previous).set(runId, { run: payload.run, events: payload.events }));
    return payload.run;
  }, []);

  const follow = useCallback(
    (runId, after) => {
      if (streams.current.has(runId)) return;
      const controller = new AbortController();
      streams.current.set(runId, controller);
      streamRunEvents(
        runId,
        after,
        (event) => {
          setRuns((previous) => {
            const entry = previous.get(runId);
            if (!entry || entry.events.some((row) => row.seq === event.seq)) return previous;
            return new Map(previous).set(runId, { run: entry.run, events: [...entry.events, event] });
          });
          if (["approval_request", "question", "final", "interrupted", "error"].includes(event.type)) {
            loadRun(runId).catch(() => {});
          }
        },
        controller.signal,
      )
        .catch(() => {})
        .finally(() => {
          streams.current.delete(runId);
          if (!controller.signal.aborted) refreshRef.current?.().catch(() => {});
        });
    },
    [loadRun],
  );

  const refresh = useCallback(async () => {
    if (!conversationId) return;
    const [conv, runList] = await Promise.all([api.conversation(conversationId), api.conversationRuns(conversationId)]);
    setConversation(conv);
    setSummaries(runList.runs);
    const started = runList.runs.filter((row) => row.state !== "QUEUED" && row.state !== "CANCELLED");
    const loaded = await Promise.all(started.map((row) => api.runEvents(row.run_id)));
    setRuns(new Map(loaded.map((payload) => [payload.run.run_id, { run: payload.run, events: payload.events }])));
    for (const payload of loaded) {
      const finished = payload.events.some((event) => ["final", "cancelled", "interrupted"].includes(event.type));
      // Active runs are followed live; a terminal run missing its closing event is caught up once.
      const catchUp = !finished && !caughtUp.current.has(payload.run.run_id);
      if (catchUp) caughtUp.current.add(payload.run.run_id);
      if (ACTIVE_STATES.has(payload.run.state) || catchUp) {
        const lastSeq = payload.events.length ? payload.events[payload.events.length - 1].seq : 0;
        follow(payload.run.run_id, lastSeq);
      }
    }
    onConversationsChanged();
  }, [conversationId, follow, onConversationsChanged]);
  refreshRef.current = refresh;

  useEffect(() => {
    for (const controller of streams.current.values()) controller.abort();
    streams.current.clear();
    setRuns(new Map());
    setSummaries([]);
    setConversation(null);
    setError(null);
    if (!conversationId) return;
    setLoading(true);
    refresh()
      .catch((err) => setError(err))
      .finally(() => setLoading(false));
    return () => {
      for (const controller of streams.current.values()) controller.abort();
      streams.current.clear();
    };
  }, [conversationId, refresh]);

  const views = useMemo(
    () =>
      [...runs.values()]
        .map((entry) => deriveRun(entry.run, entry.events))
        .sort((a, b) => (a.createdAt < b.createdAt ? -1 : 1)),
    [runs],
  );
  const legacyTurns = (conversation?.turns || []).filter((turn) => !turn.work_run_id);
  const messageEdits = conversation?.message_edits || {};
  const activeRun = views.find((view) => ["RUNNING", "AWAITING_APPROVAL", "AWAITING_INPUT"].includes(view.state));
  const queued = summaries.filter((row) => row.state === "QUEUED");
  const lastStatus = activeRun ? activeRun.state : views.length ? views[views.length - 1].state : null;

  useEffect(() => {
    const node = feedRef.current;
    if (node && stick.current) node.scrollTop = node.scrollHeight;
  }, [views, legacyTurns.length]);

  useEffect(() => {
    if (lastStatus === "AWAITING_APPROVAL") announce("Waiting for your approval.");
    else if (lastStatus === "AWAITING_INPUT") announce("Mucaro has a question for you.");
    else if (lastStatus === "COMPLETED") announce("Answer complete.");
    else if (lastStatus === "FAILED") announce("The turn failed.");
  }, [lastStatus, announce]);

  function onScroll() {
    const node = feedRef.current;
    if (node) stick.current = node.scrollHeight - node.scrollTop - node.clientHeight < 80;
  }

  async function send(prompt) {
    setError(null);
    try {
      let id = conversationId;
      const creating = !id;
      if (creating) id = (await api.createConversation(prompt.slice(0, 60))).conversation_id;
      const key = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
      const result = await api.sendMessage(id, { prompt, mode, idempotency_key: key, options: defaultOptions });
      // Switch to a new conversation only after its first run exists, so the reload sees and follows it.
      if (creating) onConversationCreated(id);
      else {
        stick.current = true;
        if (result.run.state !== "QUEUED") {
          await loadRun(result.run.run_id);
          follow(result.run.run_id, 0);
        }
        const runList = await api.conversationRuns(id);
        setSummaries(runList.runs);
      }
      announce(result.run.state === "QUEUED" ? "Message queued." : "Message sent.");
    } catch (err) {
      setError(err);
      throw err;
    }
  }

  async function stop() {
    if (!activeRun) return;
    try {
      await api.stopRun(activeRun.runId);
      announce("Stopping.");
      await refresh();
    } catch (err) {
      setError(err);
    }
  }

  async function cancelQueued(runId) {
    try {
      await api.stopRun(runId);
      await refresh();
    } catch (err) {
      setError(err);
    }
  }

  async function saveMessageEdit(messageKey, content, expectedRevision) {
    if (!conversationId) throw new Error("Conversation is not available.");
    const result = await api.editConversationMessage(conversationId, messageKey, content, expectedRevision);
    await refresh();
    announce("Message updated. Original preserved in provenance.");
    return result.message_edit;
  }

  async function branchFromRun(runId, prompt, branchMode, options) {
    if (!conversationId) throw new Error("Conversation is not available.");
    const idempotencyKey = `branch-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const result = await api.branchRun(conversationId, runId, {
      prompt,
      mode: branchMode || "ask",
      options: options || defaultOptions,
      idempotency_key: idempotencyKey,
    });
    await onConversationsChanged();
    onConversationCreated(result.conversation.conversation_id);
    announce("Branch created. Original conversation preserved.");
    return result;
  }

  const empty = !loading && !views.length && !legacyTurns.length;
  return (
    <section className="chat" aria-label="Conversation">
      <header className="chat-header">
        <div className="chat-title">
          <h1>{conversation?.title || "New conversation"}</h1>
          <p className="muted small">
            {conversation?.branch_origin
              ? `Semantic branch · source run ${conversation.branch_origin.source_run_id} · original preserved`
              : "Local workspace · answers cite your sources"}
          </p>
        </div>
        {conversation && (
          <ConversationMenu
            conversation={conversation}
            onRename={async (title) => {
              await api.renameConversation(conversation.conversation_id, title);
              await refresh();
            }}
            onDelete={async () => {
              await api.deleteConversation(conversation.conversation_id);
              onConversationCreated(null);
              onConversationsChanged();
            }}
            onOpenTasks={onOpenTasks}
            announce={announce}
          />
        )}
      </header>

      <div className="feed" ref={feedRef} onScroll={onScroll}>
        {loading && <p className="muted center">Loading conversation…</p>}
        {empty && (
          <div className="empty-state">
            <h2>What should Mucaro work on?</h2>
            <p className="muted">Ask about your sources, or use a command such as <code>/status</code> or <code>/fetch https://…</code>. Actions always ask before they run unless you choose Auto for local read-only tools.</p>
          </div>
        )}
        {legacyTurns.map((turn) => {
          const messageKey = `turn:${turn.turn_id}`;
          const edit = messageEdits[messageKey];
          return (
            <div key={turn.turn_id} className={`bubble ${turn.role === "user" ? "user-bubble" : "assistant-bubble"}`}>
              <EditableMessage
                text={String(edit?.content ?? turn.content ?? "")}
                revision={Number(edit?.revision || 0)}
                edited={Boolean(edit)}
                onSave={(content, revision) => saveMessageEdit(messageKey, content, revision)}
                announce={announce}
                allowReadAloud={turn.role === "assistant"}
              />
            </div>
          );
        })}
        {views.map((view) => (
          <RunBlock
            key={view.runId}
            view={view}
            onChanged={() => loadRun(view.runId).then(refresh)}
            onOpenCitation={setCitation}
            announce={announce}
            messageEdits={messageEdits}
            onEditMessage={saveMessageEdit}
            onBranchRun={branchFromRun}
          />
        ))}
        {error && (
          <p className="error-card" role="alert">
            <strong>{error.category || "ERROR"}</strong> — {error.status}: {error.message}
          </p>
        )}
      </div>

      <Composer
        conversationId={conversationId}
        modes={modes}
        mode={mode}
        onModeChange={setMode}
        onSend={send}
        onStop={stop}
        activeRun={Boolean(activeRun)}
        queued={queued}
        onCancelQueued={cancelQueued}
        onEditQueued={async (run, prompt) => {
          await api.editQueuedRun(run.run_id, prompt, run.version);
          const runList = await api.conversationRuns(conversationId);
          setSummaries(runList.runs);
          announce("Queued message updated.");
        }}
        disabled={false}
      />
      {citation && <EvidencePanel citation={citation} onClose={() => setCitation(null)} />}
    </section>
  );
}
