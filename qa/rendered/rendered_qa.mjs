// Rendered UI QA (MATRIX-015, MOB F01-F20): drives the real built UI against a real local
// backend at desktop and iPhone widths, saves screenshots, runs axe, and writes a receipt.
// Usage: node rendered_qa.mjs [--python PATH] [--out DIR]
import AxeBuilder from "@axe-core/playwright";
import { chromium, webkit, devices } from "playwright";
import { spawn, execFileSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..", "..");
const args = process.argv.slice(2);
const argValue = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback);
const browserName = argValue("--browser", "chromium");
if (!["chromium", "webkit"].includes(browserName)) throw new Error(`Unsupported browser: ${browserName}`);
const browserType = browserName === "webkit" ? webkit : chromium;
const python = argValue("--python", existsSync(path.join(ROOT, ".venv/bin/python")) ? path.join(ROOT, ".venv/bin/python") : "python3");
const head = (() => {
  try {
    return execFileSync("git", ["rev-parse", "HEAD"], { cwd: ROOT }).toString().trim();
  } catch {
    return "UNKNOWN_HEAD";
  }
})();
const dirtyPaths = (() => {
  try {
    return execFileSync("git", ["status", "--porcelain"], { cwd: ROOT }).toString().split("\n").filter(Boolean);
  } catch {
    return ["UNKNOWN"];
  }
})();
const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z");
const outDir = path.resolve(argValue("--out", path.join(ROOT, "data", "exports", "rendered_qa", `${stamp}-${head.slice(0, 12)}`)));
mkdirSync(outDir, { recursive: true });

const checks = [];
const shots = [];
function check(name, viewport, pass, detail = "") {
  checks.push({ name, viewport, pass: Boolean(pass), detail: String(detail).slice(0, 400) });
  console.log(`${pass ? "PASS" : "FAIL"} [${viewport}] ${name}${detail ? ` — ${String(detail).slice(0, 160)}` : ""}`);
}

async function freePort() {
  return new Promise((resolve) => {
    const server = createServer();
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

async function waitForHealth(base, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${base}/health`);
      if (res.ok) return true;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  return false;
}

async function startBackend(dataDir, port, extraEnv = {}) {
  const child = spawn(python, ["-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", String(port)], {
    cwd: ROOT,
    env: { ...process.env, MUCARO_AI_DATA_DIR: dataDir, PYTHONPATH: `${ROOT}:${path.join(ROOT, "backend")}`, MUCARO_AI_LIVE_MODE: "0", ...extraEnv },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let log = "";
  child.stdout.on("data", (chunk) => (log += chunk));
  child.stderr.on("data", (chunk) => (log += chunk));
  child.log = () => log;
  return child;
}

async function seed(base) {
  for (const file of readdirSync(path.join(ROOT, "data", "input")).filter((name) => name.startsWith("sample_"))) {
    const form = new FormData();
    form.append("upload", new Blob([readFileSync(path.join(ROOT, "data", "input", file))]), file);
    const res = await fetch(`${base}/ingest`, { method: "POST", body: form });
    if (!res.ok) throw new Error(`seed ${file}: ${res.status} ${await res.text()}`);
  }
}

async function shot(page, viewport, name) {
  const file = path.join(outDir, `${viewport}-${name}.png`);
  await page.screenshot({ path: file, fullPage: false });
  shots.push({ file: path.relative(ROOT, file), sha256: createHash("sha256").update(readFileSync(file)).digest("hex") });
}

const lastRun = (page) => page.locator("article.run").last();

let expectedRuns = 0;

async function waitRunState(page, states, timeout = 20000) {
  // Waits on the newest run only once it exists, so an earlier completed run cannot satisfy the wait.
  const wanted = Array.isArray(states) ? states : [states];
  await page.waitForFunction(
    ([list, count]) => {
      const runs = document.querySelectorAll("article.run");
      const last = runs[runs.length - 1];
      return runs.length >= count && last && list.includes(last.getAttribute("data-state"));
    },
    [wanted, expectedRuns],
    { timeout },
  );
}

async function send(page, text, { queued = false } = {}) {
  if (!queued) expectedRuns = (await page.locator("article.run").count()) + 1;
  const box = page.getByLabel("Message Mucaro");
  await box.fill(text);
  await box.press("Enter");
}

async function axe(page, viewport, name) {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const serious = result.violations.filter((v) => ["serious", "critical"].includes(v.impact));
  check(`axe ${name}: no serious/critical WCAG violations`, viewport, serious.length === 0, serious.map((v) => `${v.id}(${v.nodes.length})`).join(", ") || `${result.violations.length} minor`);
  return { view: name, viewport, violations: result.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length })) };
}

async function scenario(browser, base, viewport, contextOptions) {
  const context = await browser.newContext({ ...contextOptions, baseURL: base });
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("console", (msg) => msg.type() === "error" && consoleErrors.push(msg.text()));
  page.on("pageerror", (err) => consoleErrors.push(String(err)));
  const axeRuns = [];

  // Loading state: delay /health so the startup state is observable.
  await page.route("**/health", async (route) => {
    await new Promise((r) => setTimeout(r, 600));
    await route.continue().catch(() => {});
  });
  expectedRuns = 0;
  await page.goto("/");
  await page.getByText("Starting…").waitFor({ timeout: 5000 }).then(
    () => check("loading state renders while the service starts", viewport, true),
    () => check("loading state renders while the service starts", viewport, false),
  );
  await shot(page, viewport, "00-loading");
  await page.unrouteAll({ behavior: "ignoreErrors" });

  // Empty state (F01).
  await page.getByRole("heading", { name: "What should Mucaro work on?" }).waitFor();
  check("empty conversation state", viewport, true);
  const send0 = page.getByRole("button", { name: "Send message" });
  check("empty send is disabled (F05)", viewport, await send0.isDisabled());
  await shot(page, viewport, "01-empty");
  axeRuns.push(await axe(page, viewport, "empty"));

  // Ask with citations (F02, TGT-002, TGT-031).
  await send(page, "How does source lineage work?");
  await waitRunState(page, "COMPLETED");
  const citations = lastRun(page).getByRole("button", { name: /Open citation/ });
  check("answer completes with citations", viewport, (await citations.count()) > 0, `${await citations.count()} citations`);
  check("answer text rendered", viewport, (await lastRun(page).locator(".assistant-bubble").count()) === 1);
  await shot(page, viewport, "02-answer");
  axeRuns.push(await axe(page, viewport, "answer"));

  // Reload reproduces the transcript (F02).
  const answerText = await lastRun(page).locator(".assistant-bubble").innerText();
  await page.reload();
  await lastRun(page).locator(".assistant-bubble").waitFor();
  check("reload reproduces the transcript (F02)", viewport, (await lastRun(page).locator(".assistant-bubble").innerText()) === answerText);

  // Evidence sheet (TGT-030), Escape closes and restores focus.
  await citations.first().click();
  const evidence = page.getByRole("dialog", { name: /Evidence \[1\]/ });
  await evidence.waitFor();
  await evidence.getByText("Cited text").waitFor();
  check("citation opens exact source evidence", viewport, await evidence.getByText(/SHA-256|Byte identity/).count() > 0);
  await shot(page, viewport, "03-evidence");
  await page.keyboard.press("Escape");
  await evidence.waitFor({ state: "detached" });
  const focusedName = await page.evaluate(() => document.activeElement?.getAttribute("aria-label") || "");
  check("Escape closes the sheet and returns focus", viewport, focusedName.startsWith("Open citation"), focusedName);

  // Approval-bound action (F07) and tool summary / drawer (F08, F09).
  await send(page, "/status");
  await waitRunState(page, "AWAITING_APPROVAL");
  const approval = page.getByRole("region", { name: "Permission request for source_library.status" });
  check("permission sheet shows the exact tool and arguments", viewport, (await approval.getByText("Argument digest").count()) === 1);
  await shot(page, viewport, "04-approval");
  await approval.getByRole("button", { name: "Allow once" }).click();
  await waitRunState(page, "COMPLETED");
  const summary = lastRun(page).getByRole("button", { name: /Used 1 tool/ });
  check("tool summary counts from records", viewport, (await summary.count()) === 1);
  await shot(page, viewport, "05-tool-done");
  await summary.click();
  const drawer = page.getByRole("dialog", { name: "Tool activity" });
  await drawer.waitFor();
  check("tool drawer lists the executed call", viewport, (await drawer.getByText("EXECUTED").count()) >= 1);
  await shot(page, viewport, "06-tool-drawer");
  await page.keyboard.press("Escape");

  // Failure -> question -> choice receipt (F10, F11, F13, F16).
  await send(page, "/fetch http://127.0.0.1:9/private");
  await waitRunState(page, "AWAITING_APPROVAL");
  await page.getByRole("button", { name: "Allow once" }).click();
  await waitRunState(page, "AWAITING_INPUT");
  const question = page.getByRole("form", { name: "Question from Mucaro" });
  await question.waitFor();
  const submit = question.getByRole("button", { name: "Submit" });
  check("submit disabled until a choice is made (F11)", viewport, await submit.isDisabled());
  check("options carry consequences (F10)", viewport, (await question.getByText(/Successful actions are not repeated/).count()) === 1);
  await shot(page, viewport, "07-question");
  if (viewport === "desktop") {
    // Retry needs a fresh approval and repeats only the failed target (F16).
    await question.getByText("Retry the failed action").click();
    await submit.click();
    await waitRunState(page, "AWAITING_APPROVAL");
    check("retry asks for a fresh approval (F16)", viewport, (await lastRun(page).getByRole("region", { name: /Permission request for web.fetch/ }).count()) === 1);
    await lastRun(page).getByRole("button", { name: "Deny" }).click();
    await waitRunState(page, "COMPLETED");
    check("per-call outcomes stay distinct after retry (F08, F16)", viewport, (await lastRun(page).getByRole("button", { name: /Used 2 tools \(1 failed, 1 denied\)/ }).count()) === 1);
    check("choice receipt persists in the feed (F13)", viewport, (await lastRun(page).getByText("You chose:").count()) === 1);
  } else {
    // A new message while the question is pending queues; the same question stays (F14).
    const before = await question.getAttribute("aria-label");
    await send(page, "Queued while the question is open", { queued: true });
    await page.getByRole("list", { name: "Queued messages" }).waitFor();
    check("pending question survives a new message (F14)", viewport, (await page.getByRole("form", { name: "Question from Mucaro" }).count()) === 1 && before === "Question from Mucaro");
    // Freeform answer tied to the interaction (F12).
    await question.getByText("Other", { exact: true }).click();
    check("submit stays disabled for an empty Other answer (F11)", viewport, await submit.isDisabled());
    await question.getByLabel("Your answer").fill("Skip it for now");
    await submit.click();
    const receiptLine = page.locator(".receipt").filter({ hasText: "Skip it for now" });
    await receiptLine.waitFor();
    check("freeform answer recorded as the choice (F12, F13)", viewport, (await receiptLine.getByText("You chose:").count()) === 1);
    expectedRuns += 1;
    await waitRunState(page, "COMPLETED");
  }
  await shot(page, viewport, "08-receipt");

  // Plan mode executes nothing (F06).
  await page.getByRole("button", { name: /^Mode:/ }).click();
  await page.getByRole("option", { name: /^Plan/ }).click();
  await send(page, "/status");
  await waitRunState(page, "COMPLETED");
  check("plan mode shows the plan and executes nothing (F06)", viewport, (await lastRun(page).getByText("Plan only: nothing was executed.").count()) === 1 && (await lastRun(page).locator(".tool-summary").count()) === 0);
  await page.getByRole("button", { name: /^Mode:/ }).click();
  await page.getByRole("option", { name: /^Ask first/ }).click();

  // Queue while a turn waits; stop the active turn (F03, F04).
  await send(page, "/memory");
  await waitRunState(page, "AWAITING_APPROVAL");
  await send(page, "What is a stable chunk id?", { queued: true });
  const queueList = page.getByRole("list", { name: "Queued messages" });
  await queueList.waitFor();
  check("message queued behind the active turn (F04)", viewport, (await queueList.getByRole("listitem").count()) === 1);
  await queueList.getByRole("button", { name: /^Edit queued message/ }).click();
  await queueList.getByLabel("Edit queued message").fill("What is a stable chunk identifier?");
  await queueList.getByRole("button", { name: "Save" }).click();
  await queueList.getByText("What is a stable chunk identifier?").waitFor();
  check("queued message editable before dispatch (F04)", viewport, true);
  await shot(page, viewport, "09-queue");
  await page.getByRole("button", { name: "Stop the current turn" }).click();
  await page.waitForFunction(() => [...document.querySelectorAll("article.run")].some((node) => node.getAttribute("data-state") === "INTERRUPTED"), null, { timeout: 15000 });
  expectedRuns += 1;
  await waitRunState(page, "COMPLETED", 20000);
  check("stop interrupts and the queued message then runs (F03)", viewport, (await page.locator('article.run[data-state="INTERRUPTED"]').count()) >= 1);
  await shot(page, viewport, "10-after-stop");

  // Keyboard: Shift+Enter inserts a newline and does not send.
  const box = page.getByLabel("Message Mucaro");
  await box.fill("line one");
  await box.press("Shift+Enter");
  await box.type("line two");
  check("Shift+Enter keeps composing (F05)", viewport, (await box.inputValue()) === "line one\nline two");
  await box.fill("");

  // Conversation menu: rename (F17).
  await page.getByRole("button", { name: "Conversation menu" }).click();
  await page.getByRole("menuitem", { name: "Rename" }).click();
  const renameDialog = page.getByRole("dialog", { name: "Rename conversation" });
  await renameDialog.getByLabel("Title").fill(`QA ${viewport} conversation`);
  await renameDialog.getByRole("button", { name: "Save" }).click();
  await page.getByRole("heading", { name: `QA ${viewport} conversation` }).waitFor();
  check("rename from the conversation menu (F17)", viewport, true);

  // Delete asks for confirmation and says there is no archive (F17).
  await page.getByRole("button", { name: "Conversation menu" }).click();
  await page.getByRole("menuitem", { name: "Delete…" }).click();
  const confirm = page.getByRole("dialog", { name: "Delete conversation?" });
  check("delete confirms and states there is no archive (F17)", viewport, (await confirm.getByText(/There is no archive or undo/).count()) === 1);
  await confirm.getByRole("button", { name: "Keep it" }).click();
  await confirm.waitFor({ state: "detached" });

  // Conversation menu and background tasks (F17, F18).
  await page.getByRole("button", { name: "Conversation menu" }).click();
  await page.getByRole("menuitem", { name: "Background tasks" }).click();
  const tasks = page.getByRole("dialog", { name: "Background tasks" });
  await tasks.waitFor();
  await tasks.locator(".task-row").first().waitFor();
  check("background tasks list runs with metrics", viewport, (await tasks.locator(".task-row").count()) >= 4);
  await tasks.getByRole("button", { name: "View transcript" }).first().click();
  await tasks.locator(".transcript li").first().waitFor();
  await shot(page, viewport, "11-tasks");
  await page.keyboard.press("Escape");

  // Workspaces (TGT-003, TGT-032, TGT-033).
  const openNav = async () => {
    if (viewport === "iphone") await page.getByRole("button", { name: "Open navigation" }).click();
  };
  // The phone drawer slides out after a choice; wait for it to leave the screen before any
  // screenshot, and fail if it stays over the workspace.
  const drawerClosed = async () => {
    if (viewport !== "iphone") return true;
    return page
      .waitForFunction(() => document.querySelector(".sidebar").getBoundingClientRect().right <= 0, null, { timeout: 5000 })
      .then(() => true, () => false);
  };
  const drawerStates = [];
  await openNav();
  if (viewport === "iphone") await shot(page, viewport, "12-nav-open");
  await page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: "Sources", exact: true }).click();
  await page.getByRole("heading", { name: "Sources" }).waitFor();
  await page.locator(".source-row").nth(1).waitFor();
  check("sources workspace lists ingested sources", viewport, (await page.locator(".source-row").count()) >= 2);
  await page.locator(".source-row").first().click();
  await page.getByText("SHA-256").first().waitFor();
  drawerStates.push(await drawerClosed());
  await shot(page, viewport, "13-sources");
  axeRuns.push(await axe(page, viewport, "sources"));

  await openNav();
  await page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: "Memory", exact: true }).click();
  await page.getByLabel("Remember").fill("Prefer tables for comparisons");
  await page.getByRole("button", { name: "Save" }).click();
  await page.getByText("Prefer tables for comparisons").waitFor();
  check("memory add is visible with provenance", viewport, (await page.getByText(/preference · from api/).count()) >= 1);
  drawerStates.push(await drawerClosed());
  await shot(page, viewport, "14-memory");

  await openNav();
  await page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: "Provenance", exact: true }).click();
  await page.getByText(/Verified \(\d+ events\)/).waitFor();
  check("provenance shows a verified audit chain", viewport, true);
  drawerStates.push(await drawerClosed());
  await shot(page, viewport, "15-provenance");

  await openNav();
  await page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByText("What works offline").waitFor();
  check("settings shows model choice and offline capabilities", viewport, (await page.getByRole("radio", { name: /openai_compatible_local/ }).count()) === 1);

  const darkTheme = page.getByRole("radio", { name: "Dark" });
  await darkTheme.check();
  await page.waitForFunction(() => document.documentElement.dataset.theme === "dark");
  check("persistent theme applies to the live document", viewport, await darkTheme.isChecked());

  const topK = page.getByLabel("Retrieved chunks (1–20)");
  await topK.fill("7");
  await topK.locator("xpath=ancestor::form").getByRole("button", { name: "Save" }).click();
  await page.waitForFunction(async () => (await (await fetch("/preferences")).json()).values.top_k === 7);
  check("settings form persists bounded retrieval count", viewport, (await topK.inputValue()) === "7");

  drawerStates.push(await drawerClosed());
  await shot(page, viewport, "16-settings");
  axeRuns.push(await axe(page, viewport, "settings"));

  await openNav();
  await page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: "Chat", exact: true }).click();
  drawerStates.push(await drawerClosed());
  await send(page, "Use semantic retrieval");
  await waitRunState(page, "COMPLETED");
  check(
    "chat prompt updates settings without tool execution",
    viewport,
    (await lastRun(page).locator(".settings-receipt").count()) === 1 &&
      (await lastRun(page).locator(".tool-summary").count()) === 0,
  );

  await openNav();
  await page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByText("What works offline").waitFor();
  check(
    "prompt-side settings mutation rehydrates Settings workspace",
    viewport,
    (await page.getByLabel("Strategy").inputValue()) === "semantic",
  );
  drawerStates.push(await drawerClosed());

  if (viewport === "iphone") {
    check("navigation drawer closes after choosing a workspace", viewport, drawerStates.length === 6 && drawerStates.every(Boolean), JSON.stringify(drawerStates));
    // iOS zooms into fields under 16px on focus; touch targets need 44px (Apple HIG).
    const touch = await page.evaluate(() => {
      const visible = (el) => {
        const box = el.getBoundingClientRect();
        return box.width > 0 && box.height > 0 && getComputedStyle(el).visibility !== "hidden";
      };
      const label = (el) => `${el.tagName.toLowerCase()}:${(el.getAttribute("aria-label") || el.textContent || el.name || "").trim().slice(0, 30)}`;
      const fields = [...document.querySelectorAll("input, select, textarea")].filter(
        (el) => visible(el) && !["checkbox", "radio", "file", "hidden"].includes(el.type),
      );
      const targets = [...document.querySelectorAll("button, select, input")].filter(
        (el) => visible(el) && !["checkbox", "radio", "file", "hidden"].includes(el.type),
      );
      return {
        fields: fields.length,
        smallFonts: fields.filter((el) => parseFloat(getComputedStyle(el).fontSize) < 16).map(label),
        targets: targets.length,
        smallTargets: targets.filter((el) => el.getBoundingClientRect().height < 44).map(label),
      };
    });
    check("form fields are at least 16px (no iOS focus zoom)", viewport, touch.fields > 0 && touch.smallFonts.length === 0, touch.smallFonts.join(", ") || `${touch.fields} fields`);
    check("touch targets are at least 44px tall", viewport, touch.targets > 0 && touch.smallTargets.length === 0, touch.smallTargets.join(", ") || `${touch.targets} targets`);
  }

  const layoutOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  check("no horizontal page overflow", viewport, !layoutOverflow);
  check("no console errors", viewport, consoleErrors.length === 0, consoleErrors.join(" | "));
  await context.close();
  return axeRuns;
}

async function offlineScenario(browser, base, viewport, contextOptions, stopBackend) {
  const context = await browser.newContext({ ...contextOptions, baseURL: base });
  const page = await context.newPage();
  await page.goto("/");
  await page.getByRole("heading", { name: "What should Mucaro work on?" }).waitFor();
  await stopBackend();
  await page.getByText("Mucaro's local service is not reachable.").waitFor({ timeout: 10000 }).then(
    () => check("service-unreachable error state", viewport, true),
    () => check("service-unreachable error state", viewport, false),
  );
  await shot(page, viewport, "17-service-unreachable");
  await context.close();
}

// The desktop app opens `/#token=<per-launch token>` on a token-protected service (backend/app/desktop_shell.py).
async function launchTokenScenario(browser, viewport) {
  const token = randomBytes(24).toString("base64url");
  const tokenPort = await freePort();
  const tokenBase = `http://127.0.0.1:${tokenPort}`;
  const tokenBackend = await startBackend(mkdtempSync(path.join(tmpdir(), "mucaro-qa-token-")), tokenPort, { MUCARO_AI_OPERATOR_TOKEN: token });
  try {
    if (!(await waitForHealth(tokenBase))) throw new Error(`token backend did not start:\n${tokenBackend.log()}`);
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, baseURL: tokenBase });
    const page = await context.newPage();
    const listed = page.waitForResponse((res) => new URL(res.url()).pathname === "/conversations");
    await page.goto(`/#token=${token}`);
    const first = await listed;
    check("launch token from the URL fragment authorizes the GUI", viewport, first.status() === 200, `status ${first.status()}`);
    check("launch token is removed from the address bar", viewport, !page.url().includes("token="), page.url());
    const stored = await page.evaluate((value) => ({ session: sessionStorage.getItem("mucaro.launchToken") === value, local: JSON.stringify(localStorage).includes(value) }), token);
    check("launch token kept for this window only (not localStorage)", viewport, stored.session && !stored.local, JSON.stringify(stored));
    const relisted = page.waitForResponse((res) => new URL(res.url()).pathname === "/conversations");
    await page.reload();
    check("reload keeps the launch session authorized", viewport, (await relisted).status() === 200);
    await page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: "Settings", exact: true }).click();
    const managed = await page.getByText("created a fresh token for this launch").isVisible().catch(() => false);
    const tokenField = await page.getByLabel("Token", { exact: true }).count();
    check("Settings shows the launch token as app-managed (no token field)", viewport, managed && tokenField === 0);
    await context.close();

    const wrong = await browser.newContext({ viewport: { width: 1440, height: 900 }, baseURL: tokenBase });
    const wrongPage = await wrong.newPage();
    const refused = wrongPage.waitForResponse((res) => new URL(res.url()).pathname === "/conversations");
    await wrongPage.goto("/#token=not-the-launch-token");
    check("a wrong launch token is refused", viewport, (await refused).status() === 401);
    await wrong.close();
  } finally {
    tokenBackend.kill("SIGTERM");
  }
}

const dataDir = mkdtempSync(path.join(tmpdir(), "mucaro-qa-"));
const port = await freePort();
const base = `http://127.0.0.1:${port}`;
if (!existsSync(path.join(ROOT, "frontend", "dist", "index.html"))) {
  console.error("frontend/dist missing: run `npm run build` in frontend/ first");
  process.exit(2);
}
const backend = await startBackend(dataDir, port);
let status = "RENDERED_QA_FAIL";
const axeResults = [];
let browserVersion = null;
try {
  if (!(await waitForHealth(base))) throw new Error(`backend did not start:\n${backend.log()}`);
  await seed(base);
  const browser = await browserType.launch();
  browserVersion = browser.version();
  axeResults.push(...(await scenario(browser, base, "desktop", { viewport: { width: 1440, height: 900 } })));
  const iphone = devices["iPhone 13"];
  axeResults.push(...(await scenario(browser, base, "iphone", { ...iphone, viewport: { width: 390, height: 844 } })));
  await launchTokenScenario(browser, "desktop");
  const headers = (await fetch(`${base}/`)).headers;
  check("security headers served with the UI", "desktop", headers.get("x-frame-options") === "DENY" && (headers.get("content-security-policy") || "").includes("frame-ancestors 'none'"));
  await offlineScenario(browser, base, "desktop", { viewport: { width: 1440, height: 900 } }, async () => {
    backend.kill("SIGTERM");
    await new Promise((r) => setTimeout(r, 800));
  });
  await browser.close();
  status = checks.every((row) => row.pass) ? "RENDERED_QA_PASS" : "RENDERED_QA_FAIL";
} catch (err) {
  check("harness completed", "all", false, err.stack || String(err));
} finally {
  if (!backend.killed) backend.kill("SIGTERM");
}

const receipt = {
  schema_version: "mucaro-rendered-qa/1.0",
  status,
  head_sha: head,
  worktree_clean: dirtyPaths.length === 0,
  dirty_paths: dirtyPaths,
  created_utc: new Date().toISOString(),
  browser: `${browserName} ${browserVersion}`,
  viewports: { desktop: "1440x900", iphone: "390x844 (iPhone 13 profile, touch, DPR 3)" },
  checks,
  passed: checks.filter((row) => row.pass).length,
  failed: checks.filter((row) => !row.pass).length,
  axe: axeResults,
  screenshots: shots,
  limitations: [
    `Rendered in Playwright ${browserName}; this does not execute the packaged WKWebView window.`,
    "iPhone width is emulated on a desktop browser engine, not rendered by iOS Safari on a phone.",
  ],
};
writeFileSync(path.join(outDir, "receipt.json"), `${JSON.stringify(receipt, null, 2)}\n`);
console.log(`${status} checks=${receipt.passed}/${checks.length} receipt=${path.relative(ROOT, path.join(outDir, "receipt.json"))}`);
process.exit(status === "RENDERED_QA_PASS" ? 0 : 1);
