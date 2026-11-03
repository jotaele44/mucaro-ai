// Deterministic, model-free next-vector suggestions. This module never executes a tool.
// Labels are UI guidance; full prompts are shown before the explicit Start action.
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
    prompt: `Previous user objective (context, not a new authorization):\n${context}\n\nSelected next vector: ${action.label}.\n${action.instruction}`,
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
