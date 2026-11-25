import test from "node:test";
import assert from "node:assert/strict";
import { anyBusy, applyActivity, attentionCount, reconcileSeen, threadMarker } from "../src/threadActivity.js";

const idle = { state: "IDLE", queued: 0, last_finished_run_id: null, last_finished_state: null, last_finished_at: null, next_vector_ready: false };
const row = (id, activity = {}) => ({ conversation_id: id, title: id, turn_count: 2, activity: { ...idle, ...activity } });
const done = (run, extra = {}) => ({ last_finished_run_id: run, last_finished_state: "COMPLETED", last_finished_at: "2026-11-16T13:00:00Z", ...extra });

test("a conversation with no runs, or no activity record, has no marker", () => {
  assert.equal(threadMarker(row("a")), null);
  assert.equal(threadMarker({ conversation_id: "a", title: "a" }), null);
  assert.equal(threadMarker(null), null);
});

test("active states outrank any finished result", () => {
  assert.deepEqual(threadMarker(row("a", { state: "RUNNING", ...done("r1") })), { kind: "working", label: "Working", attention: false });
  assert.equal(threadMarker(row("a", { state: "QUEUED" })).kind, "working");
  assert.deepEqual(threadMarker(row("a", { state: "AWAITING_APPROVAL" })), { kind: "needs-you", label: "Needs your approval", attention: true });
  assert.equal(threadMarker(row("a", { state: "AWAITING_INPUT" })).label, "Has a question for you");
});

test("a finished result is marked until that chat is opened at it", () => {
  const finished = row("a", done("r1"));
  assert.deepEqual(threadMarker(finished, {}), { kind: "result", label: "New result", attention: true });
  assert.equal(threadMarker(finished, { a: "r1" }), null);
  assert.equal(threadMarker(finished, { a: "r0" }).kind, "result"); // a newer result than the one seen
});

test("a waiting vector stays visible after it was seen, without asking for attention again", () => {
  const ready = row("a", done("r1", { next_vector_ready: true }));
  assert.deepEqual(threadMarker(ready, {}), { kind: "vector", label: "New result · next vector ready", attention: true });
  assert.deepEqual(threadMarker(ready, { a: "r1" }), { kind: "vector-seen", label: "Next vector ready", attention: false });
});

test("failed and stopped runs are reported as such, once", () => {
  assert.equal(threadMarker(row("a", done("r1", { last_finished_state: "FAILED" })), {}).kind, "failed");
  assert.equal(threadMarker(row("a", done("r1", { last_finished_state: "INTERRUPTED" })), {}).label, "Stopped");
  assert.equal(threadMarker(row("a", done("r1", { last_finished_state: "FAILED", next_vector_ready: true })), { a: "r1" }), null);
});

test("polling is needed only while some run can still change", () => {
  assert.equal(anyBusy([row("a"), row("b", done("r1"))]), false);
  assert.equal(anyBusy([row("a"), row("b", { state: "AWAITING_APPROVAL" })]), true);
  assert.equal(anyBusy(null), false);
});

test("the attention count leaves out the open chat and quiet markers", () => {
  const rows = [
    row("open", done("r1")),
    row("new", done("r2", { next_vector_ready: true })),
    row("ask", { state: "AWAITING_INPUT" }),
    row("busy", { state: "RUNNING" }),
    row("seen", done("r3", { next_vector_ready: true })),
  ];
  assert.equal(attentionCount(rows, { seen: "r3" }, "open"), 2);
  assert.equal(attentionCount(rows, { seen: "r3" }, null), 3);
});

test("a browser with no record starts from everything finished being seen", () => {
  const rows = [row("a", done("r1")), row("b"), row("c", done("r3", { next_vector_ready: true }))];
  const seeded = reconcileSeen(null, rows, null);
  assert.deepEqual(seeded, { a: "r1", c: "r3" });
  assert.equal(attentionCount(rows, seeded, null), 0);
});

test("opening a chat marks its newest result seen; nothing else changes", () => {
  const rows = [row("a", done("r2")), row("b", done("r9"))];
  const seen = { a: "r1", b: "r8" };
  assert.deepEqual(reconcileSeen(seen, rows, "a"), { a: "r2", b: "r8" });
  assert.equal(reconcileSeen(seen, rows, null), seen); // same object: no re-render, no write
  assert.equal(reconcileSeen({ a: "r2", b: "r8" }, rows, "a").b, "r8");
});

test("records of deleted conversations are dropped", () => {
  assert.deepEqual(reconcileSeen({ a: "r1", gone: "r7" }, [row("a", done("r1"))], null), { a: "r1" });
});

test("applying an unchanged activity map returns the same rows", () => {
  const rows = [row("a", done("r1")), row("b")];
  const same = applyActivity(rows, { a: { ...idle, ...done("r1") } });
  assert.equal(same.rows, rows);
  assert.equal(same.stale, false);
});

test("applying a changed map updates only the changed rows and keeps list fields", () => {
  const rows = [row("a", { state: "RUNNING" }), row("b")];
  const next = applyActivity(rows, { a: { ...idle, ...done("r1", { next_vector_ready: true }) } });
  assert.notEqual(next.rows, rows);
  assert.equal(next.rows[0].activity.next_vector_ready, true);
  assert.equal(next.rows[0].title, "a");
  assert.equal(next.rows[1], rows[1]); // absent from the map: it has no runs, so it keeps its idle record
  assert.equal(next.stale, true); // a run finished: turn count and order are out of date
});

test("a state change without a new result does not ask for the full list", () => {
  const rows = [row("a", { state: "RUNNING", ...done("r1") })];
  const next = applyActivity(rows, { a: { ...idle, state: "AWAITING_APPROVAL", ...done("r1") } });
  assert.equal(next.rows[0].activity.state, "AWAITING_APPROVAL");
  assert.equal(next.stale, false);
});

test("a conversation the list has not seen asks for a full reload", () => {
  assert.equal(applyActivity([row("a")], { z: { ...idle, state: "RUNNING" } }).stale, true);
  assert.deepEqual(applyActivity(null, null), { rows: [], stale: false });
});
