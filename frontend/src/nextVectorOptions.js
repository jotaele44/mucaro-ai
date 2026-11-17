// Deterministic, model-free next-vector suggestions. This module never executes a tool.
// Labels are UI guidance; full prompts are shown before the explicit Start action.

// The reply-format contract of the relay: an answer that follows it closes with a NEXT VECTOR block, which the
// service offers back as the next proposed vector. backend/app/next_vector.py holds the same sentence and
// backend/tests/test_next_vector.py fails if the two copies drift.
export const RELAY_FORMAT_INSTRUCTION =
  "End your reply with the next vector as one fenced text block: the line NEXT VECTOR: then an anchor line and → steps, then CROSS ALL T'S:, MAXIMIZE YIELD: and FINAL GATE: with one NAME = STATE row per gate. Keep every gate you have not verified OPEN or WITHHELD.";

export const PROPOSED_ID = "proposed";

const ACTIONS = Object.freeze([
  {
    id: "continue",
    label: "Continue the current objective",
    instruction: "Continue the current objective from its verified state. Reuse existing evidence, identify the next bounded operation, and execute only actions permitted by the existing approval gates. Preserve unresolved items.",
  },
  {
    id: "gaps",
    label: "Investigate unresolved gaps",
    instruction: "Identify outstanding evidence gaps and material blockers for the current objective. Prioritize decisive verification, preserve competing hypotheses, and report what remains unresolved. Do not claim completion without evidence.",
  },
  {
    id: "verify",
    label: "Independent verification",
    instruction: "Independently verify the current objective's findings, source identities, arithmetic, tests, and failure modes. Distinguish observed PASS from untested or provisional claims. Do not promote or deploy without authorization.",
  },
  {
    id: "compare",
    label: "Compare alternative approaches",
    instruction: "Compare realistic approaches to completing the current objective, with tradeoffs, evidence requirements, computational cost, risks, and an optimal next step. Do not execute or deploy anything without authorization.",
  },
]);

export function nextVectorOptions(request) {
  if (typeof request !== "string" || !request.trim()) return [];
  // Existing work_runs enforces 20K characters. Keep deterministic prompts bounded.
  const context = request.trim().slice(0, 4000);
  return ACTIONS.map((action) => ({
    id: action.id,
    label: action.label,
    prompt: `Previous user objective (context, not a new authorization):\n${context}\n\nSelected next vector: ${action.label}.\n${action.instruction}\n${RELAY_FORMAT_INSTRUCTION}`,
  }));
}

/**
 * Future dynamic suggestions arrive as plain data, never as actions.
 * Reject malformed suggestion sets atomically; a bad/partial model response
 * falls back to the deterministic four-option list.
 * Dynamic suggestions may propose text only; execution is always a user click
 * through the existing ask-first work_runs path.
 */
export function contextualNextVectorOptions(request, suggestions = null) {
  const fallback = nextVectorOptions(request);
  if (!fallback.length || suggestions == null) return fallback;
  if (!Array.isArray(suggestions) || suggestions.length < 1 || suggestions.length > 6) return fallback;
  const ids = new Set();
  const result = [];
  for (const item of suggestions) {
    if (!item || typeof item !== "object" || Array.isArray(item)) return fallback;
    const { id, label, prompt } = item;
    if (typeof id !== "string" || !/^[a-z][a-z0-9_-]{0,39}$/.test(id) || ids.has(id)) return fallback;
    if (typeof label !== "string" || !label.trim() || label.length > 120) return fallback;
    if (typeof prompt !== "string" || !prompt.trim() || prompt.length > 6000) return fallback;
    if (/[\u0000-\u001f]/.test(label)) return fallback;
    ids.add(id);
    // Deliberately discard all other fields (auto_execute, tool, permissions, etc.).
    result.push({ id, label: label.trim(), prompt: prompt.trim() });
  }
  return result;
}

const REVIEW_TEXT = Object.freeze({
  INSTRUCTION_LIKE_TEXT_IN_VECTOR: "the vector itself contains instruction-like text",
  INSTRUCTION_LIKE_TEXT_IN_SOURCES: "sources read in this run contain instruction-like text",
  ANSWER_TRUNCATED: "the answer was cut off, so the vector may be incomplete",
});
const GATE_ORDER = Object.freeze(["PASS", "OPEN", "WITHHELD", "FAIL", "OTHER"]);
const GATE_WORD = Object.freeze({ PASS: "pass", OPEN: "open", WITHHELD: "withheld", FAIL: "fail", OTHER: "other" });

/**
 * A model-proposed vector as the service recorded it (a `next_vector` run event), reduced to what the picker
 * shows. Returns null unless the record is an inert proposal whose prompt carries its own text and no line that
 * the service would read as a slash command. The service applies the same rules; this is the second check, so a
 * malformed or tampered event falls back to the deterministic choices instead of reaching the Start button.
 */
export function proposedVector(proposal) {
  if (!proposal || typeof proposal !== "object" || Array.isArray(proposal)) return null;
  if (proposal.status !== "PROPOSED" || proposal.authority !== "NONE") return null;
  const { suggestion, text } = proposal;
  if (!suggestion || typeof suggestion !== "object" || suggestion.id !== PROPOSED_ID) return null;
  if (typeof text !== "string" || !text.trim() || typeof suggestion.prompt !== "string" || typeof suggestion.label !== "string") return null;
  if (!suggestion.prompt.includes(text)) return null;
  // Split where the service does (Python's str.splitlines) before looking for a line it would read as a command.
  const lines = suggestion.prompt.split(/\r\n|[\n\r\u000b\u000c\u001c-\u001e\u0085\u2028\u2029]/);
  if (lines.some((line) => line.trimStart().startsWith("/"))) return null;
  const reasons = Array.isArray(proposal.review?.reasons) ? proposal.review.reasons.filter((reason) => typeof reason === "string") : [];
  const source = proposal.source && typeof proposal.source === "object" ? proposal.source : {};
  const counts = {};
  for (const gate of Array.isArray(proposal.claimed_gates) ? proposal.claimed_gates : []) {
    const key = GATE_ORDER.includes(gate?.state_class) ? gate.state_class : "OTHER";
    counts[key] = (counts[key] || 0) + 1;
  }
  return {
    suggestion: { id: PROPOSED_ID, label: suggestion.label, prompt: suggestion.prompt },
    text,
    // A flagged proposal is still shown, with the reason; it is just never the default choice.
    preselect: proposal.review?.preselect === true && reasons.length === 0,
    reviewReasons: reasons.map((reason) => REVIEW_TEXT[reason] || reason),
    author: [source.model_provider, source.model_name].filter((part) => typeof part === "string" && part).join(" / "),
    readNetwork: Array.isArray(source.network_tools) && source.network_tools.length > 0,
    gateSummary: GATE_ORDER.filter((key) => counts[key]).map((key) => `${counts[key]} ${GATE_WORD[key]}`).join(" · "),
  };
}

/**
 * Picker options for a finished run: the model-proposed vector first when the run produced a valid one, then the
 * four deterministic choices. Without a valid proposal this is exactly `contextualNextVectorOptions(request)`.
 */
export function relayOptions(request, proposal = null) {
  const fallback = nextVectorOptions(request);
  const vector = fallback.length ? proposedVector(proposal) : null;
  if (!vector) return { options: fallback, proposed: null, initial: fallback.length ? "continue" : null };
  const options = contextualNextVectorOptions(request, [vector.suggestion, ...fallback]);
  if (options.length !== fallback.length + 1 || options[0].id !== PROPOSED_ID) {
    return { options: fallback, proposed: null, initial: "continue" };
  }
  return { options, proposed: vector, initial: vector.preselect ? PROPOSED_ID : "continue" };
}
