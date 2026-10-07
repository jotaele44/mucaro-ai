import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, streamRunEvents } from "../api.js";
import { copyFailureMessage, copyText } from "../clipboard.js";
import { ACTIVE_STATES, deriveRun, transcriptText } from "../feed.js";
import { Composer } from "./Composer.jsx";
import { ConfirmSheet } from "./ConfirmSheet.jsx";
import { EvidencePanel } from "./EvidencePanel.jsx";
import { EditableMessage } from "./MessageActions.jsx";
import { RunBlock } from "./RunBlock.jsx";
import { Sheet } from "./Sheet.jsx";

// A stream route that keeps failing is retried with backoff, then paused until the user (or a reconnect) resumes it.
const MAX_STREAM_RETRIES = 10;
const bump = (map, key) => map.set(key, (map.get(key) || 0) + 1);

function ConversationMenu({ conversation, onRename, onUpdate, onDelete, onOpenTasks, announce }) {
  const [open, setOpen] = useState(false);
  const [dialog, setDialog] = useState(null);
  const [title, setTitle] = useState(conversation?.title || "");
  const [renaming, setRenaming] = useState(false);
  const [renameError, setRenameError] = useState(null);
  const [updating, setUpdating] = useState(false);
  const [actionError, setActionError] = useState(null);
  const menuRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    const menu = menuRef.current;
    menu?.querySelector('[role="menuitem"]')?.focus();
    const dismiss = (event) => {
      if (!menu?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open]);

  function menuKey(event) {
    if (!open) return;
    const items = [...menuRef.current.querySelectorAll('[role="menuitem"]')];
    const index = items.indexOf(document.activeElement);
    if (event.key === "Escape") {
      setOpen(false);
      menuRef.current.querySelector('[aria-haspopup="menu"]').focus();
    } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items[next]?.focus();
    } else return;
    event.preventDefault();
  }

  async function organize(changes) {
    if (updating) return;
    setUpdating(true);
    setActionError(null);
    try {
      await onUpdate(changes);
      setOpen(false);
      announce("Conversation updated.");
    } catch (err) {
      setActionError(err.message || "Could not update this conversation.");
    } finally {
      setUpdating(false);
    }
  }

  async function submitRename(event) {
    event.preventDefault();
    const next = title.trim();
    if (!next || renaming) return;
    setRenaming(true);
    setRenameError(null);
    try {
      await onRename(next);
      setDialog(null);
    } catch (err) {
      setRenameError(`${err?.status || "ERROR"}: ${err?.message || "The conversation could not be renamed."}`);
    } finally {
      setRenaming(false);
    }
  }

  async function copyTranscript() {
    setOpen(false);
    const result = await copyText(transcriptText(conversation?.turns || []));
    announce(result.ok ? "Transcript copied." : copyFailureMessage(result));
  }

  return (
    <div className="menu-wrap" ref={menuRef} onKeyDown={menuKey}>
      <button type="button" className="icon-button" aria-haspopup="menu" aria-expanded={open} aria-label="Conversation menu" disabled={updating} onClick={() => setOpen((value) => !value)}>
        ⋯
      </button>
      {open && (
        <ul className="menu" role="menu">
          <li role="none"><button role="menuitem" type="button" disabled={updating} onClick={() => organize({ pinned: !conversation.pinned })}>{conversation.pinned ? "Unpin" : "Pin"}</button></li>
          <li role="none"><button role="menuitem" type="button" disabled={updating} onClick={() => organize({ archived: !conversation.archived })}>{conversation.archived ? "Restore conversation" : "Archive"}</button></li>
          <li role="none"><button role="menuitem" type="button" onClick={() => { setOpen(false); setTitle(conversation?.title || ""); setRenameError(null); setDialog("rename"); }}>Rename</button></li>
          <li role="none"><button role="menuitem" type="button" onClick={copyTranscript}>Copy transcript</button></li>
          <li role="none"><button role="menuitem" type="button" onClick={() => { setOpen(false); onOpenTasks(); }}>Background tasks</button></li>
          <li role="none"><button role="menuitem" type="button" className="danger-text" onClick={() => { setOpen(false); setDialog("delete"); }}>Delete…</button></li>
        </ul>
      )}
      {actionError && <p className="error-text" role="alert">{actionError}</p>}
      {dialog === "rename" && (
        <Sheet title="Rename conversation" onClose={() => setDialog(null)} side="center">
          <form onSubmit={submitRename}>
            <label className="field">
              <span>Title</span>
              <input data-autofocus value={title} onChange={(event) => setTitle(event.target.value)} maxLength={120} />
            </label>
            {renameError && (
              <p className="error-text" role="alert">
                {renameError}
              </p>
            )}
            <div className="button-row">
              <button type="button" className="secondary-button" onClick={() => setDialog(null)}>Cancel</button>
              <button type="submit" className="primary-button" disabled={!title.trim() || renaming}>{renaming ? "Saving…" : "Save"}</button>
            </div>
          </form>
        </Sheet>
      )}
      {dialog === "delete" && (
        <ConfirmSheet title="Delete conversation?" confirmLabel="Delete permanently" onConfirm={onDelete} onClose={() => setDialog(null)}>
          <p>This permanently deletes the conversation and its transcript from the computer that runs Mucaro. Sources, memory and receipts are kept. Archive it instead to keep it available for restoration. Permanent deletion has no undo.</p>
        </ConfirmSheet>
      )}
    </div>
  );
}

export function ChatView({ reconnect = 0, conversationId, onConversationCreated, onConversationsChanged, onOpenTasks, announce, modes, defaultOptions }) {
  const [conversation, setConversation] = useState(null);
  const [runs, setRuns] = useState(new Map());
  const [summaries, setSummaries] = useState([]);
  const [mode, setMode] = useState("ask");
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [citation, setCitation] = useState(null);
  const [paused, setPaused] = useState(false);
  const streams = useRef(new Map());
  const failures = useRef(new Map()); // runId -> consecutive stream failures
  const retryTimers = useRef(new Map());
  const handledReconnect = useRef(reconnect);
  const caughtUp = useRef(new Set());
  const refreshRef = useRef(null);
  const feedRef = useRef(null);
  const stick = useRef(true);

  // Drops every open stream and pending retry. A page that was suspended keeps stream objects that are dead
  // but still registered, which would block a re-follow.
  const resetStreams = useCallback(() => {
    for (const controller of streams.current.values()) controller.abort();
    streams.current.clear();
    for (const timer of retryTimers.current.values()) clearTimeout(timer);
    retryTimers.current.clear();
    failures.current.clear();
    setPaused(false);
  }, []);

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
          failures.current.delete(runId); // data is flowing, so the stream route works
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
        .then((closing) => {
          if (!closing) bump(failures.current, runId); // the server closed the stream without an end event
        })
        .catch(() => {
          if (!controller.signal.aborted) bump(failures.current, runId);
        })
        .finally(() => {
          if (streams.current.get(runId) === controller) streams.current.delete(runId);
          if (controller.signal.aborted) return;
          const failed = failures.current.get(runId) || 0;
          if (!failed) {
            refreshRef.current?.().catch(() => {}); // a normal end: load the final state at once
          } else if (failed >= MAX_STREAM_RETRIES) {
            setPaused(true);
          } else {
            // Exponential backoff with jitter, so a stream route that keeps failing is not hammered.
            const delay = Math.min(30000, 500 * 2 ** (failed - 1)) * (0.75 + Math.random() * 0.5);
            clearTimeout(retryTimers.current.get(runId));
            retryTimers.current.set(
              runId,
              setTimeout(() => {
                retryTimers.current.delete(runId);
                refreshRef.current?.().catch(() => {});
              }, delay),
            );
          }
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
    resetStreams();
    setRuns(new Map());
    setSummaries([]);
    setConversation(null);
    setError(null);
    if (!conversationId) return;
    setLoading(true);
    refresh()
      .catch((err) => setError(err))
      .finally(() => setLoading(false));
    return () => resetStreams();
  }, [conversationId, refresh, resetStreams]);

  // The app asks for this after the page was away, regained its network, or the service came back.
  useEffect(() => {
    if (reconnect === handledReconnect.current) return;
    handledReconnect.current = reconnect;
    resetStreams();
    if (conversationId) refreshRef.current?.().catch(() => {});
  }, [reconnect, conversationId, resetStreams]);

  function resume() {
    resetStreams();
    refreshRef.current?.().catch((err) => setError(err));
  }

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

  const empty = !loading && !views.length && !legacyTurns.length;
  return (
    <section className="chat" aria-label="Conversation">
      <header className="chat-header">
        <div className="chat-title">
          <h1>{conversation?.title || "New conversation"}</h1>
          <p className="muted small">{conversation?.archived ? "Archived conversation" : "Local workspace"}{conversation?.pinned ? " · Pinned" : ""} · answers cite your sources</p>
        </div>
        {conversation && (
          <ConversationMenu
            conversation={conversation}
            onRename={async (title) => {
              try {
                const updated = await api.updateConversation(conversation.conversation_id, { title }, conversation.revision ?? 0);
                setConversation(updated);
                await onConversationsChanged();
              } catch (err) {
                if (err.status === "ERROR_CONVERSATION_REVISION_CONFLICT") await refresh();
                throw err;
              }
            }}
            onUpdate={async (changes) => {
              try {
                const updated = await api.updateConversation(conversation.conversation_id, changes, conversation.revision ?? 0);
                setConversation(updated);
                await onConversationsChanged();
              } catch (err) {
                if (err.status === "ERROR_CONVERSATION_REVISION_CONFLICT") await refresh();
                throw err;
              }
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
        {legacyTurns.map((turn, index) => {
          const messageKey = `turn:${turn.turn_id}`;
          const edit = messageEdits[messageKey];
          const question = turn.role === "assistant" ? legacyTurns.slice(0, index).reverse().find((row) => row.role === "user") : null;
          return (
            <div key={turn.turn_id} className={`bubble ${turn.role === "user" ? "user-bubble" : "assistant-bubble"}`}>
              <EditableMessage
                text={String(edit?.content ?? turn.content ?? "")}
                revision={Number(edit?.revision || 0)}
                edited={Boolean(edit)}
                onSave={(content, revision) => saveMessageEdit(messageKey, content, revision)}
                announce={announce}
                allowReadAloud={turn.role === "assistant"}
                file={turn.role === "assistant" ? { title: question?.content || conversation?.title || "", citations: [], records: [] } : null}
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
          />
        ))}
        {error && (
          <p className="error-card" role="alert">
            <strong>{error.category || "ERROR"}</strong> — {error.status}: {error.message}
          </p>
        )}
      </div>

      {paused && (
        <p className="notice chat-notice" role="status">
          Live updates paused after repeated failures.{" "}
          <button type="button" className="ghost-button" onClick={resume}>
            Resume
          </button>
        </p>
      )}

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
