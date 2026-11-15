import test from "node:test";
import assert from "node:assert/strict";
import { nextVectorOptions, contextualNextVectorOptions } from "../src/nextVectorOptions.js";

test("empty context produces no options or implicit execution", () => {
  assert.deepEqual(nextVectorOptions(" "), []);
  assert.deepEqual(nextVectorOptions(null), []);
});

test("four distinct options produce different contextual prompts", () => {
  const options = nextVectorOptions("Investigate the archived records");
  assert.equal(options.length, 4);
  assert.equal(new Set(options.map((item) => item.id)).size, 4);
  assert.equal(new Set(options.map((item) => item.prompt)).size, 4);
  for (const item of options) {
    assert.match(item.prompt, /Investigate the archived records/);
    assert.match(item.prompt, /context, not a new authorization/);
    assert.ok(item.label);
    assert.ok(item.prompt.length < 20000);
  }
});

test("long context is bounded and remains plaintext rather than executable code", () => {
  const malicious = "<script>run()</script>" + "x".repeat(25000);
  const options = nextVectorOptions(malicious);
  assert.equal(options.length, 4);
  assert.ok(options.every((x) => x.prompt.length < 5000));
  assert.ok(options.every((x) => x.prompt.includes("context, not a new authorization")));
});

test("option generation is deterministic and free of network or persistence", () => {
  assert.deepEqual(nextVectorOptions("Check the evidence"), nextVectorOptions("Check the evidence"));
});

test("future suggested options retain text-only fields and deterministic order", () => {
  const suggestions = [
    { id: "check_sources", label: "Check sources", prompt: "Verify original documents", auto_execute: true, permission: "admin" },
    { id: "compare_methods", label: "Compare methods", prompt: "Compare methods and risks", tool: "deploy" },
  ];
  assert.deepEqual(contextualNextVectorOptions("Research question", suggestions), [
    { id: "check_sources", label: "Check sources", prompt: "Verify original documents" },
    { id: "compare_methods", label: "Compare methods", prompt: "Compare methods and risks" },
  ]);
});

test("malformed or conflicting dynamic options fail closed to four static choices", () => {
  const fallback = nextVectorOptions("Research question");
  const cases = [
    [], [{}], [{ id: "x", label: "One", prompt: "a" }, { id: "x", label: "Two", prompt: "b" }],
    [{ id: "X", label: "One", prompt: "a" }],
    [{ id: "ok", label: "One", prompt: "" }],
    [{ id: "ok", label: "One", prompt: "x".repeat(6001) }],
    [{ id: "ok", label: "Bad\nlabel", prompt: "a" }],
    Array.from({ length: 7 }, (_, n) => ({ id: "v" + n, label: "x", prompt: "x" })),
    "not an array",
  ];
  for (const candidate of cases) assert.deepEqual(contextualNextVectorOptions("Research question", candidate), fallback);
});

test("no context never creates dynamic options", () => {
  assert.deepEqual(contextualNextVectorOptions("", [{ id: "x", label: "x", prompt: "x" }]), []);
});
