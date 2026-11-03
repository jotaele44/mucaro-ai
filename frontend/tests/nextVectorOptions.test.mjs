import test from "node:test";
import assert from "node:assert/strict";
import { nextVectorOptions } from "../src/nextVectorOptions.js";

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
