// Pet activity may only reflect observed local backend health and run state.
// Visual/manual interactions (drag, open, dock) are not agent reasoning evidence.
export const PET_RUNTIME_STATES = Object.freeze({
  RUNNING: "WORK",
  AWAITING_APPROVAL: "NOTIFY",
  AWAITING_INPUT: "NOTIFY",
  COMPLETED: "HAPPY",
  FAILED: "NOTIFY",
  INTERRUPTED: "NOTIFY",
  CANCELLED: "IDLE",
  QUEUED: "IDLE",
});
export function derivePetRuntime(healthState, observation, now = Date.now()) {
  if (healthState === "error") return { state: "NOTIFY", label: "Local service unreachable", source: "health" };
  if (healthState !== "ok") return { state: "IDLE", label: "Waiting for local service", source: "health" };
  const key = observation?.runState;
  const id = observation?.runId;
  // Terminal indications expire; never display an old success as live activity.
  // An active run does not expire merely because its latest state was unchanged.
  if (!["RUNNING", "AWAITING_APPROVAL", "AWAITING_INPUT", "QUEUED"].includes(key)
    && typeof observation?.observedAt === "number"
    && Number.isFinite(observation.observedAt)
    && now - observation.observedAt > 20_000)
    return { state: "IDLE", label: "Ready", source: "local" };
  if (typeof id !== "string" || !id.trim() || !Object.hasOwn(PET_RUNTIME_STATES, key))
    return { state: "IDLE", label: "Ready", source: "local" };
  const labels = {
    RUNNING: "Run in progress", AWAITING_APPROVAL: "Run awaiting approval",
    AWAITING_INPUT: "Run awaiting input", COMPLETED: "Run completed",
    FAILED: "Run failed", INTERRUPTED: "Run interrupted",
    CANCELLED: "Run cancelled", QUEUED: "Run queued",
  };
  return { state: PET_RUNTIME_STATES[key], label: labels[key], source: "observed-run", runId: id };
}
export function observationSignature(observation) {
  return typeof observation?.runId === "string" && typeof observation?.runState === "string"
    ? observation.runId + ":" + observation.runState : null;
}

export function isReportableRunObservation(runId, runState, streamedRunIds) {
  if (typeof runId !== "string" || !runId.trim() || !Object.hasOwn(PET_RUNTIME_STATES, runState)) return false;
  // Hydrated terminal history is evidence of past activity, not a fresh runtime event.
  return ["RUNNING", "AWAITING_APPROVAL", "AWAITING_INPUT"].includes(runState)
    || Boolean(streamedRunIds?.has(runId));
}
