// Rendered QA for the relay loop (VIDEO_VECTOR_RELAY_V1): drives the built UI against the real service at
// desktop and iPhone widths. The service is started by relay_backend.py, whose answerer is scripted to close
// each cited answer with a NEXT VECTOR block; nothing else is stubbed.
// Usage: node relay_qa.mjs [--python PATH] [--out DIR] [--browser chromium|webkit]
// Needs the built interface (npm run build in frontend/). A pass is rendered evidence for the interface only:
// the block is fixture text, so it says nothing about what a live model writes (row VRL-002).
import { spawn, execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { createServer } from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Resolved the CommonJS way so a Playwright installed here (npm ci) or provided by the environment both work.
const require = createRequire(import.meta.url);
const { chromium, webkit, devices } = require("playwright");
const AxeBuilder = require("@axe-core/playwright").default;

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..", "..");
const args = process.argv.slice(2);
const argValue = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback);
const browserName = argValue("--browser", "chromium");
if (!["chromium", "webkit"].includes(browserName)) throw new Error(`Unsupported browser: ${browserName}`);
const browserType = browserName === "webkit" ? webkit : chromium;
const python = argValue("--python", existsSync(path.join(ROOT, ".venv/bin/python")) ? path.join(ROOT, ".venv/bin/python") : "python3");
if (!existsSync(path.join(ROOT, "frontend", "dist", "index.html"))) throw new Error("Build the interface first: npm run build in frontend/");
const git = (...argv) => {
  try {
    return execFileSync("git", argv, { cwd: ROOT }).toString().trim();
  } catch {
    return "UNKNOWN";
  }
};
const head = git("rev-parse", "HEAD");
const dirtyPaths = git("status", "--porcelain").split("\n").filter(Boolean);
const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z");
const outDir = path.resolve(argValue("--out", path.join(ROOT, "data", "exports", "rendered_qa", `relay-${stamp}-${head.slice(0, 12)}`)));
mkdirSync(outDir, { recursive: true });

const checks = [];
const shots = [];
function check(viewport, name, pass, detail = "") {
  checks.push({ name, viewport, pass: Boolean(pass), detail: String(detail).slice(0, 400) });
  console.log(`${pass ? "PASS" : "FAIL"} [${viewport}] ${name}${detail ? ` — ${String(detail).slice(0, 160)}` : ""}`);
}

async function axe(page, viewport, name) {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const serious = result.violations.filter((violation) => ["serious", "critical"].includes(violation.impact));
  check(viewport, `axe ${name}: no serious/critical WCAG violations`, serious.length === 0,
    serious.map((violation) => `${violation.id}(${violation.nodes.length})`).join(", ") || `${result.violations.length} minor`);
}

function freePort() {
  return new Promise((resolve) => {
    const server = createServer();
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

async function waitForService(base, child) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (child.exitCode !== null) throw new Error(`relay_backend.py exited with code ${child.exitCode}`);
    try {
      if ((await fetch(`${base}/health`)).ok) return;
    } catch {
      /* not listening yet */
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("The service did not start.");
}

const runState = (page, states, count, timeout = 30000) =>
  page.waitForFunction(
    ([list, wanted]) => {
      const runs = document.querySelectorAll("article.run");
      const last = runs[runs.length - 1];
      return runs.length >= wanted && last && list.includes(last.getAttribute("data-state"));
    },
    [states, count],
    { timeout },
  );

async function send(page, text) {
  await page.getByLabel("Message Mucaro").fill(text);
  await page.getByRole("button", { name: /^(Send|Queue) message$/ }).click();
}

async function scenario(base, viewport, contextOptions) {
  const browser = await browserType.launch();
  // Clipboard permissions exist in Chromium only; WebKit reads the clipboard through the page's own gesture.
  const permissions = browserName === "chromium" ? ["clipboard-read", "clipboard-write"] : [];
  const context = await browser.newContext({ ...contextOptions, baseURL: base, permissions });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (err) => errors.push(err.message));
  page.on("console", (msg) => msg.type() === "error" && errors.push(msg.text()));
  const phone = viewport === "iphone";
  const title = `How does source lineage work on ${viewport}?`;
  const openNav = async () => {
    if (phone) await page.getByRole("button", { name: "Open navigation" }).click();
  };
  const row = () => page.locator(".conversation-list li", { hasText: title });
  const clipboard = () => page.evaluate(() => navigator.clipboard.readText()).catch(() => null);
  async function shot(name) {
    const file = path.join(outDir, `${viewport}-${name}.png`);
    await page.screenshot({ path: file });
    shots.push({ file: path.relative(ROOT, file), sha256: createHash("sha256").update(readFileSync(file)).digest("hex") });
  }
  try {
    await page.goto("/");
    await page.getByLabel("Message Mucaro").waitFor();

    // A finished answer that closes with a block: the vector is offered first, as a proposal.
    await send(page, title);
    await runState(page, ["COMPLETED"], 1);
    const chooser = page.getByRole("form", { name: "Suggested next steps" });
    await chooser.waitFor({ timeout: 15000 });
    const radios = chooser.getByRole("radio");
    check(viewport, "relay: picker offers the proposed vector and the four deterministic choices", (await radios.count()) === 5, await radios.count());
    check(viewport, "relay: proposed vector is first and preselected", await radios.first().isChecked());
    const label = await chooser.locator(".next-vector-proposed").innerText();
    check(viewport, "relay: proposed option is labelled with the block's anchor", label.includes("Model-proposed: VERIFY SOURCE LINEAGE CHUNK IDENTITY"), label);
    const provenance = await chooser.locator(".next-vector-provenance").innerText();
    check(viewport, "relay: provenance calls it a proposal and marks its gates unverified",
      provenance.includes("proposal, not an instruction") && provenance.includes("not verified: 1 pass · 1 open · 1 withheld"), provenance);

    // Copy: the block in the answer, and the same text from the picker.
    const copyBlock = page.locator("article.run").last().getByRole("button", { name: "Copy this block" });
    check(viewport, "relay: the answer's block has its own copy control", (await copyBlock.count()) === 1, await copyBlock.count());
    await copyBlock.click();
    const copied = await clipboard();
    if (copied === null) check(viewport, "relay: block copy acknowledged (clipboard not readable in this browser)", await page.getByText("Block copied to clipboard.").count());
    else check(viewport, "relay: copying the block yields exactly the vector text", copied.startsWith("NEXT VECTOR:\nVERIFY SOURCE LINEAGE CHUNK IDENTITY") && copied.endsWith("PRODUCTION = WITHHELD"), copied.slice(0, 60));
    await chooser.getByRole("button", { name: "Copy proposed vector" }).click();
    if (copied !== null) check(viewport, "relay: the picker's copy yields the same text", (await clipboard()) === copied);

    // Selecting and previewing send nothing.
    const before = await page.locator("article.run").count();
    await chooser.getByRole("radio", { name: "Investigate unresolved gaps" }).check();
    await radios.first().check();
    await chooser.getByRole("button", { name: "Preview exact prompt" }).click();
    const preview = await chooser.locator(".next-vector-prompt").innerText();
    check(viewport, "relay: selecting and previewing send nothing", (await page.locator("article.run").count()) === before);
    check(viewport, "relay: exact prompt carries provenance, the block and the reply-format contract",
      preview.startsWith("Model-proposed next vector (written by mock/") && preview.includes("context, not a new authorization") &&
      preview.includes("→ CONFIRM EVERY STABLE CHUNK ID") && preview.trim().endsWith("OPEN or WITHHELD."), preview.slice(0, 90));
    await shot("relay-proposed-vector");
    await axe(page, viewport, "proposed vector");

    // One Start relays it; the operator leaves for a second conversation while it works.
    await chooser.getByRole("button", { name: "Start selected vector" }).click();
    await runState(page, ["RUNNING"], 2, 10000);
    const relayed = await page.locator("article.run").last().locator(".user-bubble").innerText();
    check(viewport, "relay: one Start sent the previewed prompt as the next message", relayed.includes("Model-proposed next vector") && relayed.includes("VERIFY SOURCE LINEAGE CHUNK IDENTITY"), relayed.slice(0, 60));
    check(viewport, "relay: the relay created no approval", (await page.locator(".approval-card").count()) === 0);
    await openNav();
    await page.getByRole("button", { name: "New chat" }).click();
    await send(page, `What is a stable chunk id on ${viewport}?`);
    await openNav();
    await row().locator(".thread-working").waitFor({ timeout: 8000 });
    check(viewport, "threads: the first conversation shows Working while the second is open", true);
    await shot("relay-thread-working");
    if (phone) await page.locator(".nav-scrim").click({ position: { x: 380, y: 400 } });

    // The first conversation finishes in the background and says so without being opened.
    if (phone) {
      await page.locator(".nav-toggle .nav-dot").waitFor({ timeout: 20000 });
      const described = await page.locator("#nav-attention").innerText();
      check(viewport, "threads: the menu control shows a marker with a text equivalent", described.includes("other conversation"), described);
      await openNav();
    }
    await row().locator(".thread-vector").waitFor({ timeout: 20000 });
    const marker = await row().locator(".thread-marker").innerText();
    check(viewport, "threads: a finished background conversation is marked new, with its vector ready", marker.includes("New result · next vector ready"), marker);
    await page.waitForFunction(
      (wanted) => [...document.querySelectorAll(".conversation-list li")].some((item) => item.innerText.includes(wanted) && item.innerText.includes("4 turns")),
      title,
      { timeout: 8000 },
    );
    check(viewport, "threads: the row's turn count is current after the background run", true);
    await shot("relay-thread-ready");
    await axe(page, viewport, "thread markers");

    // Opening it settles the marker; the next proposed vector is waiting.
    await row().getByRole("button").click();
    await runState(page, ["COMPLETED"], 2);
    await chooser.waitFor({ timeout: 15000 });
    const next = await chooser.locator(".next-vector-proposed").innerText();
    check(viewport, "relay: the relayed run produced the next vector, offered first again", /Model-proposed: (CLOSE|CERTIFY|VERIFY) SOURCE LINEAGE CHUNK IDENTITY/.test(next), next);
    await openNav();
    await row().locator(".thread-vector-seen").waitFor({ timeout: 8000 });
    check(viewport, "threads: an opened conversation keeps a quiet 'Next vector ready' marker", true);
    await shot("relay-thread-seen");
    check(viewport, "relay: no page errors", errors.length === 0, errors.join(" | "));
  } catch (err) {
    check(viewport, "relay: scenario completed", false, err.message);
    await shot("relay-failure").catch(() => {});
  } finally {
    await browser.close();
  }
}

const port = await freePort();
const base = `http://127.0.0.1:${port}`;
const service = spawn(python, [path.join(HERE, "relay_backend.py"), "--port", String(port)], { cwd: ROOT, stdio: ["ignore", "inherit", "inherit"] });
let failed = 1;
try {
  await waitForService(base, service);
  await scenario(base, "desktop", { viewport: { width: 1280, height: 860 }, deviceScaleFactor: 2 });
  const { defaultBrowserType, ...iphone } = devices["iPhone 14"];
  await scenario(base, "iphone", iphone);
  failed = checks.filter((row) => !row.pass).length;
} finally {
  service.kill();
}
const receipt = {
  schema_version: "mucaro-rendered-relay-qa/1.0",
  denominator_id: "VIDEO_VECTOR_RELAY_V1",
  status: failed ? "RENDERED_RELAY_QA_FAILED" : "RENDERED_RELAY_QA_OK",
  head,
  dirty_paths: dirtyPaths,
  browser: browserName,
  answerer: "SCRIPTED_FIXTURE (qa/rendered/relay_backend.py); not a live model",
  checks,
  passed: checks.length - failed,
  total: checks.length,
  screenshots: shots,
  created_at: new Date().toISOString(),
};
writeFileSync(path.join(outDir, "relay_qa_receipt.json"), `${JSON.stringify(receipt, null, 2)}\n`);
console.log(`\n${receipt.status} ${receipt.passed}/${receipt.total} checks · ${path.relative(ROOT, outDir)}`);
process.exit(failed ? 1 : 0);
