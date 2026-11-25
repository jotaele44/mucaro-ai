// Thread status for the conversation list: which chats are working, which wait on the person, and which hold a
// result they have not opened. Pure functions over what GET /conversations returns (`row.activity`) and a
// per-browser record of the newest result each chat was opened at. Nothing here sends a message or starts a run.

export const BUSY_STATES = new Set(["QUEUED", "RUNNING", "AWAITING_APPROVAL", "AWAITING_INPUT"]);
const WAITING = { AWAITING_APPROVAL: "Needs your approval", AWAITING_INPUT: "Has a question for you" };
// How often the list is re-read while some chat is busy; an idle workspace is not polled.
export const ACTIVITY_POLL_MS = 3000;

/** The marker for one conversation row, or null when there is nothing to report. */
export function threadMarker(row, seen = {}) {
  const activity = row?.activity;
  if (!activity || typeof activity !== "object") return null;
  if (WAITING[activity.state]) return { kind: "needs-you", label: WAITING[activity.state], attention: true };
  if (activity.state === "RUNNING" || activity.state === "QUEUED") return { kind: "working", label: "Working", attention: false };
  const finished = activity.last_finished_run_id;
  if (!finished) return null;
  const unseen = seen[row.conversation_id] !== finished;
  if (activity.last_finished_state === "FAILED") return unseen ? { kind: "failed", label: "Failed", attention: true } : null;
  if (activity.last_finished_state === "INTERRUPTED") return unseen ? { kind: "result", label: "Stopped", attention: true } : null;
  if (activity.next_vector_ready) {
    return unseen
      ? { kind: "vector", label: "New result · next vector ready", attention: true }
      : { kind: "vector-seen", label: "Next vector ready", attention: false };
  }
  return unseen ? { kind: "result", label: "New result", attention: true } : null;
}

/** True while any conversation has a run that can still change without the person acting in the open chat. */
export function anyBusy(rows) {
  return (rows || []).some((row) => BUSY_STATES.has(row?.activity?.state));
}

/** How many conversations other than the open one want the person's attention (the dot on the menu button). */
export function attentionCount(rows, seen = {}, openId = null) {
  return (rows || []).filter((row) => row.conversation_id !== openId && threadMarker(row, seen)?.attention).length;
}

/**
 * The seen record after the list was loaded. A browser with no record yet (first run after an upgrade) starts
 * from "everything already finished is seen", so old chats do not all light up at once. The open chat is always
 * seen up to its newest result. Returns the same object when nothing changed.
 */
export function reconcileSeen(seen, rows, openId = null) {
  const list = rows || [];
  if (seen == null) {
    const seeded = {};
    for (const row of list) {
      const finished = row?.activity?.last_finished_run_id;
      if (finished) seeded[row.conversation_id] = finished;
    }
    return seeded;
  }
  let next = seen;
  const known = new Set(list.map((row) => row.conversation_id));
  for (const id of Object.keys(seen)) {
    if (!known.has(id)) {
      if (next === seen) next = { ...seen };
      delete next[id]; // the conversation was deleted
    }
  }
  const open = list.find((row) => row.conversation_id === openId);
  const finished = open?.activity?.last_finished_run_id;
  if (finished && next[openId] !== finished) {
    if (next === seen) next = { ...seen };
    next[openId] = finished;
  }
  return next;
}

/**
 * Rows with a freshly read activity map applied. Returns the same array when nothing changed, so polling a
 * quiet list causes no re-render. `stale` is true when the list itself must be read again: the map names a
 * conversation the list does not have, or a run finished, which changed that chat's turn count and order.
 */
export function applyActivity(rows, activity) {
  const list = rows || [];
  const map = activity && typeof activity === "object" ? activity : {};
  const ids = new Set(list.map((row) => row.conversation_id));
  let stale = Object.keys(map).some((id) => !ids.has(id));
  let changed = false;
  const next = list.map((row) => {
    // A conversation with no runs is absent from the map and keeps the idle activity it was listed with.
    const fresh = map[row.conversation_id];
    if (!fresh || JSON.stringify(fresh) === JSON.stringify(row.activity)) return row;
    changed = true;
    if (fresh.last_finished_run_id !== row.activity?.last_finished_run_id) stale = true;
    return { ...row, activity: fresh };
  });
  return { rows: changed ? next : list, stale };
}
