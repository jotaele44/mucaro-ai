// Run only against an isolated local fixture backend.
import { chromium } from "playwright";
import AxeBuilder from "@axe-core/playwright";
import { createHash } from "node:crypto";
import { spawn, execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import path from "node:path";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import assert from "node:assert/strict";
const base = process.env.LEARNING_QA_BASE || "http://127.0.0.1:8765";
const out = process.env.LEARNING_QA_OUT || "/tmp/mucaro-learning-qa";
mkdirSync(out, { recursive: true });
const server = process.env.LEARNING_QA_BASE ? null : spawn(process.env.LEARNING_QA_PYTHON || "python3",
  ["-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "8765"],
  { env: { ...process.env, MUCARO_AI_DATA_DIR: mkdtempSync(path.join(tmpdir(), "mucaro-learning-")), MUCARO_AI_OPERATOR_TOKEN: "" }, stdio: "ignore" });
for (let i = 0; i < 100; i++) {
  try { if ((await fetch(`${base}/health`)).ok) break; } catch {}
  await new Promise((resolve) => setTimeout(resolve, 100));
}
const taskRepo = mkdtempSync(path.join(tmpdir(), "mucaro-consumer-qa-"));
const git = (...args) => execFileSync("git", ["-C", taskRepo, ...args], { encoding: "utf8" }).trim();
git("init", "-q"); writeFileSync(path.join(taskRepo, "review.txt"), "Original source.\n"); git("add", ".");
git("-c", "user.name=QA", "-c", "user.email=qa@example.invalid", "commit", "-qm", "Initial fixture");
const taskBase = git("rev-parse", "HEAD");
const taskBytes = "Original source.\nReviewed update.\n"; writeFileSync(path.join(taskRepo, "review.txt"), taskBytes); git("add", ".");
git("-c", "user.name=QA", "-c", "user.email=qa@example.invalid", "commit", "-qm", "Updated fixture");
const taskHead = git("rev-parse", "HEAD");
const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
const checks = [];
try {
  for (const width of [1440, 393, 430]) {
    const context = await browser.newContext({ viewport: { width, height: 950 } });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    await page.goto(base);
    if (width < 800) await page.getByRole("button", { name: "Open navigation" }).click();
    await page.getByRole("button", { name: "Learning", exact: true }).click();
    await page.getByRole("heading", { name: "Learning", exact: true }).waitFor();
    const fill = (name, value) => page.getByLabel(name, { exact: true }).fill(value);
    const click = (name) => page.getByRole("button", { name, exact: true }).click();
    const check = (name) => page.getByLabel(name, { exact: true }).check();
    await fill("Lesson", `Always retain exact source identifiers in fixture ${width}.`);
    await fill("Source ID", `fixture:${width}`);
    await fill("Original evidence", `Independent QA fixture ${width}; no real training data.`);
    await page.locator("form").filter({ has: page.getByRole("button", { name: "Recognize and route", exact: true }) }).getByLabel("Permitted purpose", { exact: true }).fill("Isolated learning UI verification");
    await page.locator("select[name=kind]").first().selectOption("instruction");
    await check("I authorize storing and reviewing these bytes for this purpose.");
    await click("Recognize and route");
    await page.getByRole("heading", { name: "REVIEW · version 1", exact: true }).waitFor({timeout: 5000}).catch(async (e) => { console.log(await page.locator("body").innerText()); throw e; });
    await page.locator("select[name=decision]").selectOption("accept");
    await fill("Rationale", "Verified fixture rule with explicit bounded scope.");
    await fill("Positive case", "Retains IDs from original bytes.");
    await fill("Negative case", "Rejects invented source IDs.");
    await check("I independently verified the evidence.");
    await check("I reviewed scope, conflicts and authority boundaries.");
    await check("I explicitly approve this exact addition.");
    await click("Record review");
    await click("Apply reviewed artifact");
    await fill("Evaluation evidence / receipt", "Fixture checks passed; no real-world training claim.");
    await check("Positive case passed"); await check("Negative case passed");
    await click("Verify artifact");
    await page.getByRole("heading", { name: "VERIFIED · version 4", exact: true }).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
    const axe = await new AxeBuilder({ page }).include(".learning-workspace").analyze();
    const serious = axe.violations.filter((v) => ["serious", "critical"].includes(v.impact));
    assert.deepEqual(serious.map((v) => v.id), [], `accessibility ${width}`);
    await page.screenshot({ path: `${out}/learning-${width}.png`, fullPage: true });
    await fill("Rollback reason", "End isolated QA fixture"); await click("Roll back");
    await page.getByRole("heading", { name: "ROLLED_BACK · version 5", exact: true }).waitFor();
    const post = async (url, payload) => {
      const response = await page.request.post(`${base}/learning${url}`, { data: payload });
      assert.equal(response.status(), 200); return response.json();
    };
    let skill = await post("/recognize", { text: `Repeatable fixture workflow ${width}.`, source_id: `qa-skill:${width}`,
      source_bytes: "Synthetic evidence for reward-interface checks", permitted_use: true, purpose: "Isolated QA",
      kind: "skill", scope: `qa-${width}` });
    skill = await post(`/${skill.candidate_id}/review`, { version: skill.version, accept: true, rationale: "Fixture review",
      positive_case: "Valid artifact", negative_case: "Invalid artifact denied", conflict_ids: [],
      explicit_approval: true, verified_evidence: true, authority_safe: true });
    skill = await post(`/${skill.candidate_id}/apply`, { version: skill.version });
    skill = await post(`/${skill.candidate_id}/verify`, { version: skill.version, positive_pass: true,
      negative_pass: true, evaluation_receipt: "Synthetic QA receipt" });
    await page.reload();
    if (width < 800) await page.getByRole("button", { name: "Open navigation" }).click();
    await click("Learning");
    await page.getByLabel(/^Review queue/).selectOption(skill.candidate_id);
    await fill("Unique episode ID", `qa-episode-${width}`);
    await fill("Reward (−1 to 1)", "1");
    await fill("Observed outcome evidence", "Synthetic verified fixture replay");
    await check("Authorize this feedback for policy learning");
    await click("Record reward");
    await page.getByText('"episode_id": "qa-episode-' + width + '"', { exact: false }).first().waitFor();
    await click("Evaluate recommendation");
    const recommendation = await (await page.request.get(`${base}/learning/recommend?context=qa-${width}`)).json();
    assert.equal(recommendation.training_episodes, 1);
    assert.deepEqual(recommendation.top_candidates, [skill.candidate_id]);
    assert.equal(recommendation.execution_authorized, false);
    await post(`/${skill.candidate_id}/rollback`, { version: skill.version, reason: "End synthetic QA" });
    let agentSkill = await post("/recognize", { text: `Read-only repository evidence procedure ${width}.`, source_id: `consumer-qa:${width}`,
      source_bytes: "Synthetic UI fixture; no real training-effectiveness claim", permitted_use: true, purpose: "Isolated UI verification",
      kind: "skill", scope: "repository_review" });
    agentSkill = await post(`/${agentSkill.candidate_id}/review`, { version: agentSkill.version, accept: true, rationale: "Fixture review",
      positive_case: "Approved recipe", negative_case: "Unapproved recipe denied", explicit_approval: true, verified_evidence: true, authority_safe: true });
    agentSkill = await post(`/${agentSkill.candidate_id}/apply`, { version: agentSkill.version });
    agentSkill = await post(`/${agentSkill.candidate_id}/verify`, { version: agentSkill.version, positive_pass: true, negative_pass: true,
      evaluation_receipt: "Fixture UI gate" });
    const grant = await page.request.post(`${base}/permissions/directories/grant`, { data: { path: taskRepo, reason: "Isolated read-only UI task" } });
    assert.equal(grant.status(), 200);
    await page.reload();
    if (width < 800) await page.getByRole("button", { name: "Open navigation" }).click();
    await click("Learning");
    await page.getByLabel("Verified procedure", { exact: true }).selectOption(agentSkill.candidate_id);
    await check("I approve this exact procedure and recipe binding."); await click("Bind agent workflow");
    await page.getByRole("button", { name: "Revoke agent binding", exact: true }).waitFor();
    await fill("Repository path", taskRepo); await fill("Exact revision", taskHead); await fill("Comparison revision", taskBase);
    await fill("Repository-relative file", "review.txt"); await fill("Expected file SHA256", createHash("sha256").update(taskBytes).digest("hex"));
    await fill("Unique task key", `consumer-ui-${width}`); await click("Prepare agent run");
    await page.getByRole("heading", { name: "Agent run: PREPARED", exact: true }).waitFor();
    await check("I approve the displayed local read-only plan."); await click("Execute agent run");
    await page.getByRole("heading", { name: "Agent run: COMPLETED", exact: true }).waitFor();
    await fill("Observed run score", "1"); await fill("Verified outcome criteria", "Fixture HEAD, comparison and exact file bytes verified");
    await check("Use this run feedback to rank workflows for this task category."); await click("Learn from executed run");
    const consumerAxe = await new AxeBuilder({ page }).include(".learning-workspace").analyze();
    assert.deepEqual(consumerAxe.violations.filter((v) => ["serious", "critical"].includes(v.impact)).map((v) => v.id), []);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
    await page.screenshot({ path: `${out}/agent-consumer-${width}.png`, fullPage: true });
    await fill("Binding revocation reason", "End isolated consumer UI fixture"); await click("Revoke agent binding");
    assert.deepEqual(errors, []);
    checks.push({ width, lifecycle: "PASS", reward_interface: "PASS", agent_consumer: "PASS", rollback: "PASS", overflow: false, serious_a11y: 0, page_errors: errors });
    await context.close();
  }
  writeFileSync(`${out}/receipt.json`, JSON.stringify({ status: "PASS", scope: "learning fixture UI; chromium desktop/mobile widths", checks }, null, 2));
  console.log(JSON.stringify(checks));
} finally { await browser.close(); server?.kill(); }
