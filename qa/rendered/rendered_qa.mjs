// Rendered UI QA (MATRIX-015, MOB F01-F20): drives the real built UI against a real local
// backend at desktop and iPhone widths, saves screenshots, runs axe, and writes a receipt.
// Usage: node rendered_qa.mjs [--python PATH] [--out DIR] [--browser chromium|webkit] [--only-new] [--scenarios a,b]
// --only-new runs just the resilience, streams, gate, lan and clipboard scenarios; --scenarios picks some of them
// (development aids: their receipts are marked partial).
import AxeBuilder from "@axe-core/playwright";
import { chromium, webkit, devices } from "playwright";
import { spawn, execFileSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import http from "node:http";
import { createServer } from "node:net";
import { networkInterfaces, tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..", "..");
const args = process.argv.slice(2);
const argValue = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback);
const scenarioFilter = argValue("--scenarios", "").split(",").filter(Boolean); // resilience,streams,gate,lan,clipboard
const onlyNew = args.includes("--only-new") || scenarioFilter.length > 0;
const wantScenario = (name) => scenarioFilter.length === 0 || scenarioFilter.includes(name);
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

async function startBackend(dataDir, port, extraEnv = {}, host = "127.0.0.1") {
  const child = spawn(python, ["-m", "uvicorn", "backend.app.main:app", "--host", host, "--port", String(port)], {
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
  await page.getByLabel("Message Mucaro").fill(text);
  // The Send button works on every pointer type; Return only sends on a hardware keyboard (checked separately).
  await page.getByRole("button", { name: /^(Send|Queue) message$/ }).click();
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

  // Message interaction contract: native selection plus one Copy/Edit surface on both sides.
  const completedRun = lastRun(page);
  const userMessage = completedRun.locator(".user-bubble");
  const assistantMessage = completedRun.locator(".assistant-bubble");
  const selectionContract = await assistantMessage.locator(".rich-text").evaluate((node) => {
    const style = window.getComputedStyle(node);
    const range = document.createRange();
    range.selectNodeContents(node);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    const selectedText = selection.toString();
    selection.removeAllRanges();
    return {
      userSelect: style.getPropertyValue("user-select"),
      webkitUserSelect: style.getPropertyValue("-webkit-user-select"),
      selectedText,
    };
  });
  check(
    "message text is selectable",
    viewport,
    (selectionContract.userSelect === "text" || selectionContract.webkitUserSelect === "text") &&
      selectionContract.selectedText.trim().length > 0,
    JSON.stringify(selectionContract),
  );
  check(
    "sent message exposes one Copy and one Edit action",
    viewport,
    (await userMessage.getByRole("button", { name: "Copy message text" }).count()) === 1 &&
      (await userMessage.getByRole("button", { name: "Edit message" }).count()) === 1,
  );
  check(
    "received message exposes one Copy and one Edit action",
    viewport,
    (await assistantMessage.getByRole("button", { name: "Copy message text" }).count()) === 1 &&
      (await assistantMessage.getByRole("button", { name: "Edit message" }).count()) === 1,
  );

  await assistantMessage.getByRole("button", { name: "Copy message text" }).click();
  await page.waitForFunction(() => document.querySelector('[role="status"]')?.textContent?.includes("Copied to clipboard."));
  check("copy action reports success", viewport, true);

  await userMessage.getByRole("button", { name: "Edit message" }).click();
  const userEditor = userMessage.getByLabel("Edit message text");
  await userEditor.fill("How does source lineage work? [display edit]");
  await userMessage.getByRole("button", { name: "Save edit" }).click();
  await userMessage.getByText("Edited · original preserved").waitFor();
  check("sent message edit renders explicit provenance marker", viewport, true);

  await assistantMessage.getByRole("button", { name: "Edit message" }).click();
  const assistantEditor = assistantMessage.getByLabel("Edit message text");
  await assistantEditor.fill("Edited assistant display for rendered QA.");
  await assistantMessage.getByRole("button", { name: "Save edit" }).click();
  await assistantMessage.getByText("Edited · original preserved").waitFor();
  check("received message edit renders explicit provenance marker", viewport, true);
  await shot(page, viewport, "02b-message-actions");

  // Reload reproduces the transcript and its display-only revisions (F02).
  const answerText = await lastRun(page).locator(".assistant-bubble").innerText();
  await page.reload();
  await lastRun(page).locator(".assistant-bubble").waitFor();
  check("reload reproduces the transcript (F02)", viewport, (await lastRun(page).locator(".assistant-bubble").innerText()) === answerText);
  check(
    "message display revisions survive reload",
    viewport,
    (await lastRun(page).locator(".user-bubble").getByText("How does source lineage work? [display edit]").count()) === 1 &&
      (await lastRun(page).locator(".assistant-bubble").getByText("Edited assistant display for rendered QA.").count()) === 1,
  );

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
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.locator("#memory-list").getByText("Prefer tables for comparisons", { exact: true }).first().waitFor();
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
  drawerStates.push(await drawerClosed());
  await shot(page, viewport, "16-settings");
  axeRuns.push(await axe(page, viewport, "settings"));

  // Three-region regression: rail navigation must not destroy sibling tab state.
  const tablist = page.getByRole("toolbar", { name: "Open workspace tabs" });
  const navShell = page.locator("#navigation-shell");
  const mobileShellInactive = viewport !== "iphone" || await navShell.evaluate((node) => node.inert === true);
  check("primary rail and contextual sidebar are distinct regions", viewport,
    (await page.locator(".primary-rail").count()) === 1 &&
    (await page.locator(".context-sidebar").count()) === 1 &&
    mobileShellInactive &&
    (viewport === "iphone" || (
      (await page.getByRole("navigation", { name: "Primary" }).count()) === 1 &&
      (await page.getByRole("complementary", { name: "Settings navigation" }).count()) === 1
    )));
  check("visited workspaces have persistent accessible tabs", viewport,
    (await tablist.locator(".workspace-tab").count()) >= 5 &&
    (await tablist.getByRole("button", { name: "Settings", exact: true }).getAttribute("aria-pressed")) === "true");
  await tablist.getByRole("button", { name: "Sources", exact: true }).click();
  await page.getByRole("heading", { name: "Sources" }).waitFor();
  check("source selection survives switching workspace tabs", viewport,
    (await page.locator(".source-row.selected").count()) === 1);
  await tablist.getByRole("button", { name: "Memory", exact: true }).click();
  await page.locator("#memory-list").getByText("Prefer tables for comparisons", { exact: true }).first().waitFor();
  check("memory workspace survives switching tabs", viewport, true);
  await tablist.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByText("What works offline").waitFor();
  if (viewport !== "iphone") {
    const navigation = page.locator(".sidebar");
    const beforeWidth = await navigation.evaluate((el) => el.getBoundingClientRect().width);
    await page.getByRole("button", { name: "Collapse contextual sidebar" }).click();
    await page.getByRole("button", { name: "Expand contextual sidebar" }).waitFor();
    await page.waitForFunction(() => document.querySelector(".sidebar").getBoundingClientRect().width <= 70);
    const collapsedWidth = await navigation.evaluate((el) => el.getBoundingClientRect().width);
    check("collapsing context retains primary rail", viewport,
      collapsedWidth <= 70 && beforeWidth > 200 &&
      (await page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: "Settings", exact: true }).count()) === 1);
    await page.getByRole("button", { name: "Expand contextual sidebar" }).click();
  }
  check("tab close does not switch unrelated active workspace", viewport,
    (await tablist.getByRole("button", { name: "Settings", exact: true }).getAttribute("aria-pressed")) === "true");
  await page.getByRole("button", { name: "Close Sources tab" }).click();
  check("closing inactive tab conserves active tab", viewport,
    (await tablist.getByRole("button", { name: "Sources", exact: true }).count()) === 0 &&
    (await tablist.getByRole("button", { name: "Settings", exact: true }).getAttribute("aria-pressed")) === "true");

  // Imported GUI integration: bundled real owl thumbnail, local palette and truthful UI controls.
  const brand = page.locator(".brand-owl").first();
  check("owl icon uses bundled offline data and is present", viewport,
    (await brand.count()) === 1 &&
    (await brand.getAttribute("src")).startsWith("data:image/webp;base64,"));
  const appearance = page.locator("#settings-appearance");
  check("appearance is a real Settings panel, not a second page", viewport,
    (await appearance.count()) === 1 &&
    (await tablist.getByRole("button", { name: "Appearance" }).count()) === 0);
  await appearance.getByRole("radio", { name: "Dark", exact: true }).check();
  await page.waitForFunction(() => document.documentElement.dataset.mucaroTheme === "dark");
  check("explicit dark mode overrides current system theme", viewport, true);
  await appearance.getByRole("radio", { name: "Light", exact: true }).check();
  await page.waitForFunction(() => document.documentElement.dataset.mucaroTheme === "light");
  check("explicit light mode overrides current system theme", viewport, true);
  await appearance.getByRole("radio", { name: "System", exact: true }).check();

  if (viewport === "iphone") {
    await appearance.getByRole("button", { name: "Español" }).click();
    check("settings locale switches to Spanish without duplicating page identity", viewport,
      (await page.getByRole("heading", { name: "Settings" }).count()) === 1 &&
      (await tablist.getByRole("button", { name: "Ajustes", exact: true }).count()) === 1);
    await appearance.getByRole("button", { name: "English" }).click();
  } else {
    await page.getByRole("navigation", { name: "Primary" })
      .getByRole("button", { name: "Switch language to Spanish" }).click();
    check("rail flag reflects active locale", viewport,
      (await page.getByRole("button", { name: "Cambiar idioma a inglés" }).count()) === 1 &&
      (await tablist.getByRole("button", { name: "Ajustes", exact: true }).count()) === 1);
    await page.getByRole("button", { name: "Cambiar idioma a inglés" }).click();
    await page.getByRole("navigation", { name: "Primary" })
      .getByRole("button", { name: "Múcaro owl" }).click();
    const owl = page.getByRole("region", { name: "Múcaro owl controls" });
    await owl.waitFor();
    check("owl runtime is source-labeled rather than inferred from conversation text", viewport,
      (await owl.locator(".mucaro-pet-state").count()) === 1);
    await owl.getByRole("checkbox", { name: "Float over workspace" }).check();
    check("owl can float with local image and no sprite CDN", viewport,
      (await page.locator(".mucaro-floating-pet").count()) === 1);
    await owl.getByRole("button", { name: "Dock in sidebar" }).click();
    await owl.getByRole("button", { name: "Close owl controls" }).click();
  }
  check("no remote font or hosted-image stylesheet dependency", viewport,
    await page.evaluate(() => {
      const prohibited = new RegExp("fonts[.]googleapis[.]com|_cdn/static");
      return [...document.styleSheets].every((sheet) => !prohibited.test(sheet.href || "")) &&
        performance.getEntriesByType("resource").every((entry) => !prohibited.test(entry.name));
    }));
  await shot(page, viewport, "17-gui-draft-integration");

  if (viewport === "iphone") {
    check("navigation drawer closes after choosing a workspace", viewport, drawerStates.length === 4 && drawerStates.every(Boolean), JSON.stringify(drawerStates));
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
  // The page polls every 15 s; a network "online" event asks for an immediate check, as a phone regaining signal does.
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
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

// ---------------------------------------------------------------------------------------------------------------------
// Phone resilience and phone access (audit findings M1-M7, M21, M25). Every check below failed on the UI before its
// fix. A step that throws is reported as a failed check, so one broken area cannot hide the others.

const skippedNotes = [];
const iphoneProfile = () => ({ ...devices["iPhone 13"], viewport: { width: 390, height: 844 } });
const desktopProfile = () => ({ viewport: { width: 1440, height: 900 } });

async function guarded(label, viewport, body) {
  try {
    await body();
  } catch (err) {
    check(`${label}: scenario ran to completion`, viewport, false, String(err.message || err).split("\n")[0]);
  }
}

// The focused element as a short string; runs inside the page.
const describeActive = () => {
  const el = document.activeElement;
  if (!el) return "none";
  const label = el.getAttribute("aria-label");
  return `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ""}${label ? `[${label}]` : ""}`;
};

// Counts React commits through the DevTools hook (the production build calls it too): an idle UI commits nothing.
const countReactCommits = () => {
  window.__commits = 0;
  window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = {
    supportsFiber: true,
    renderers: new Map(),
    inject(renderer) {
      this.renderers.set(1, renderer);
      return 1;
    },
    onCommitFiberRoot() {
      window.__commits++;
    },
    onCommitFiberUnmount() {},
    onPostCommitFiberRoot() {},
    checkDCE() {},
  };
};

// What iOS reports for a touch screen. Playwright's WebKit emulation may not, and the app's reaction is what is tested.
const forceCoarsePointer = () => {
  const real = window.matchMedia.bind(window);
  window.matchMedia = (query) => {
    const result = real(query);
    if (!/\(\s*pointer\s*:\s*coarse\s*\)/.test(query)) return result;
    return new Proxy(result, {
      get: (target, prop) => (prop === "matches" ? true : typeof target[prop] === "function" ? target[prop].bind(target) : target[prop]),
    });
  };
};

async function ask(page, text, wanted = "COMPLETED") {
  const before = await page.locator("article.run").count();
  await page.getByLabel("Message Mucaro").fill(text);
  await page.getByRole("button", { name: /^(Send|Queue) message$/ }).click();
  await page.waitForFunction(
    ([count, state]) => {
      const runs = document.querySelectorAll("article.run");
      const last = runs[runs.length - 1];
      return runs.length > count && last && last.getAttribute("data-state") === state;
    },
    [before, wanted],
    { timeout: 30000 },
  );
}

const jsonPost = (body) => ({ method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
const appears = (locator, options = { timeout: 8000 }) => locator.waitFor(options).then(() => true, () => false);

async function waitServerRun(base, runId, states, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let state = null;
  while (Date.now() < deadline) {
    state = (await (await fetch(`${base}/work-runs/${runId}/events`)).json()).run.state;
    if (states.includes(state)) return state;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`run ${runId} stayed ${state}, wanted ${states}`);
}

// A conversation whose only run waits for the person's approval (/status is an approval-gated tool).
async function awaitingApprovalRun(base, title) {
  const created = await (await fetch(`${base}/conversations`, jsonPost({ title }))).json();
  const sent = await fetch(
    `${base}/conversations/${created.conversation_id}/messages`,
    jsonPost({ prompt: "/status", mode: "ask", idempotency_key: `qa-${randomBytes(6).toString("hex")}`, options: {} }),
  );
  if (!sent.ok) throw new Error(`send /status: HTTP ${sent.status}`);
  const run = (await sent.json()).run;
  await waitServerRun(base, run.run_id, ["AWAITING_APPROVAL"]);
  return { conversationId: created.conversation_id, runId: run.run_id };
}

async function resilienceScenario(browser, base) {
  // M1 and M2 share one desktop page with a render counter.
  await guarded("typing and health flap (M1, M2)", "desktop", async () => {
    const context = await browser.newContext({ ...desktopProfile(), baseURL: base });
    await context.addInitScript(countReactCommits);
    const page = await context.newPage();
    try {
      await page.goto("/");
      await page.locator("#composer-input").waitFor();
      await ask(page, "How does source lineage work?");

      // M1: the Rename field starts focused and takes real keystrokes. fill() hides the bug: it types nothing.
      await page.getByRole("button", { name: "Conversation menu" }).click();
      await page.getByRole("menuitem", { name: "Rename" }).click();
      const rename = page.getByRole("dialog", { name: "Rename conversation" });
      await rename.waitFor();
      const title = rename.getByLabel("Title");
      const startsInField = await title.evaluate((el) => el === document.activeElement);
      await page.keyboard.press("ControlOrMeta+A");
      await page.keyboard.type("abc", { delay: 60 });
      const typed = await title.inputValue();
      const stillInField = await title.evaluate((el) => el === document.activeElement);
      check("Rename opens with the cursor in the title field (M1)", "desktop", startsInField);
      check("Rename takes keystroke-by-keystroke typing and keeps focus (M1)", "desktop", typed === "abc" && stillInField, `value ${JSON.stringify(typed)}, focus kept: ${stillInField}`);
      await page.keyboard.press("Escape");
      await rename.waitFor({ state: "detached" });

      // M1: a sheet keeps the focus the person gave it while the app re-renders around it.
      await page.getByRole("button", { name: "Conversation menu" }).click();
      await page.getByRole("menuitem", { name: "Background tasks" }).click();
      const tasks = page.getByRole("dialog", { name: "Background tasks" });
      await tasks.locator(".task-row").first().waitFor();
      await tasks.getByRole("button", { name: "View transcript" }).first().focus();
      const focusBefore = await page.evaluate(describeActive);
      await page.waitForTimeout(5200);
      const focusAfter = await page.evaluate(describeActive);
      check("focus inside an open sheet is not reset in the background (M1)", "desktop", focusBefore === focusAfter && focusBefore.startsWith("button"), `${focusBefore} -> ${focusAfter}`);
      await page.keyboard.press("Escape");
      await tasks.waitFor({ state: "detached" });

      // M2: an idle, healthy app does not re-render.
      await page.waitForTimeout(1500);
      const commitsBefore = await page.evaluate(() => window.__commits);
      await page.waitForTimeout(9000);
      const idleCommits = (await page.evaluate(() => window.__commits)) - commitsBefore;
      check("an idle, healthy app commits no renders (M2)", "desktop", idleCommits === 0, `${idleCommits} commits in 9 s`);

      // M2: one failed health poll keeps the conversation on screen and recovers without pressing Retry.
      let failures = 0;
      await context.route("**/health", (route) => {
        if (failures > 0) {
          failures -= 1;
          return route.abort("connectionreset");
        }
        return route.continue();
      });
      failures = 1;
      const banner = page.getByText("Mucaro's local service is not reachable.");
      await page.evaluate(() => window.dispatchEvent(new Event("online"))); // what a phone does when it regains signal
      const shown = await appears(banner);
      const runsWhileOffline = await page.locator("article.run").count();
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      const recovered = shown ? await appears(banner, { state: "hidden", timeout: 8000 }) : false;
      check("one failed health poll keeps the conversation and recovers by itself (M2)", "desktop", shown && runsWhileOffline > 0 && recovered, `banner ${shown ? "shown" : "not shown"}, runs on screen meanwhile: ${runsWhileOffline}, recovered: ${recovered}`);
      await context.unroute("**/health");
    } finally {
      await context.close();
    }
  });

  // M6: a plain-text 500 (a proxy page, a crash) is reported readably.
  await guarded("error text (M6)", "desktop", async () => {
    const context = await browser.newContext({ ...desktopProfile(), baseURL: base });
    const page = await context.newPage();
    try {
      await page.goto("/");
      await page.locator("#composer-input").waitFor();
      await page.route("**/conversations/*/messages", (route) =>
        route.request().method() === "POST" ? route.fulfill({ status: 500, contentType: "text/plain; charset=utf-8", body: "Internal Server Error" }) : route.continue(),
      );
      await page.getByLabel("Message Mucaro").fill("How does source lineage work?");
      await page.getByRole("button", { name: "Send message" }).click();
      const card = page.locator(".error-card[role=alert]").first();
      await card.waitFor({ timeout: 8000 });
      const text = (await card.innerText()).replace(/\s+/g, " ");
      check("a plain-text 500 is reported readably (M6)", "desktop", /Internal Server Error/.test(text) && !/undefined|Body is unusable|already read/i.test(text), text);
    } finally {
      await context.close();
    }
  });

  // M4: touch keyboards have no Shift+Enter, so Return adds a line and the Send button (or Ctrl+Enter) submits.
  await guarded("touch keyboard (M4)", "iphone", async () => {
    const context = await browser.newContext({ ...iphoneProfile(), baseURL: base });
    await context.addInitScript(forceCoarsePointer);
    const page = await context.newPage();
    try {
      await page.goto("/");
      await page.locator("#composer-input").waitFor();
      const box = page.getByLabel("Message Mucaro");
      const hint = await box.getAttribute("enterkeyhint");
      await box.fill("line one");
      await box.press("Enter");
      await page.waitForTimeout(700);
      const afterReturn = await box.inputValue();
      const startedByReturn = await page.locator("article.run").count();
      await box.pressSequentially("line two");
      const multiline = await box.inputValue();
      check("on a touch keyboard Return adds a line and sends nothing (M4)", "iphone", startedByReturn === 0 && afterReturn === "line one\n" && multiline === "line one\nline two", `value ${JSON.stringify(afterReturn)}, runs started by Return: ${startedByReturn}`);
      check("the composer asks a touch keyboard for a Return key, not Send (M4)", "iphone", hint === "enter", `enterkeyhint=${hint}`);
      await box.press("Control+Enter");
      const sent = await appears(page.locator("article.run").first(), { timeout: 10000 });
      check("Ctrl+Enter still sends from a touch device (M4)", "iphone", sent);
    } finally {
      await context.close();
    }
  });

  await guarded("hardware keyboard (M4)", "desktop", async () => {
    const context = await browser.newContext({ ...desktopProfile(), baseURL: base });
    const page = await context.newPage();
    try {
      await page.goto("/");
      await page.locator("#composer-input").waitFor();
      const box = page.getByLabel("Message Mucaro");
      const hint = await box.getAttribute("enterkeyhint");
      await box.fill("How does source lineage work?");
      await box.press("Enter");
      const sent = await appears(page.locator("article.run").first(), { timeout: 10000 });
      check("with a hardware keyboard Return sends (M4)", "desktop", sent && hint === "send", `enterkeyhint=${hint}`);
    } finally {
      await context.close();
    }
  });

  // M21: a malformed percent-escape in the token fragment must not blank the app.
  await guarded("malformed token fragment (M21)", "desktop", async () => {
    const context = await browser.newContext({ ...desktopProfile(), baseURL: base });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (err) => errors.push(String(err)));
    try {
      await page.goto("/#token=%E0%A4%A");
      const loaded = await appears(page.locator("#composer-input"), { timeout: 15000 });
      check("a malformed token fragment does not blank the app (M21)", "desktop", loaded && errors.length === 0, errors.join(" | ") || "loaded");
    } finally {
      await context.close();
    }
  });

  // M5: removing a source, deleting a memory and revoking folder access each ask first.
  await guarded("destructive confirmations (M5)", "desktop", async () => {
    const context = await browser.newContext({ ...desktopProfile(), baseURL: base });
    const page = await context.newPage();
    const grantRoot = mkdtempSync(path.join(tmpdir(), "mucaro-qa-grant-"));
    try {
      await page.goto("/");
      await page.locator("#composer-input").waitFor();
      const nav = page.getByRole("navigation", { name: "Primary" });

      await nav.getByRole("button", { name: "Sources", exact: true }).click();
      await page.locator('input[type="file"]').setInputFiles({ name: "qa-temp-source.txt", mimeType: "text/plain", buffer: Buffer.from("QA temporary source about lineage.") });
      const row = page.locator(".source-row", { hasText: "qa-temp-source.txt" });
      await row.waitFor({ timeout: 15000 });
      await row.click();
      await page.getByRole("button", { name: "Remove source" }).click();
      const removeDialog = page.getByRole("dialog", { name: "Remove this source?" });
      const askedToRemove = await appears(removeDialog, { timeout: 3000 });
      let keptSource = false;
      let removedOnConfirm = false;
      if (askedToRemove) {
        await removeDialog.getByRole("button", { name: "Keep it" }).click();
        await removeDialog.waitFor({ state: "detached" });
        keptSource = (await row.count()) === 1;
        await page.getByRole("button", { name: "Remove source" }).click();
        await removeDialog.getByRole("button", { name: "Remove permanently" }).click();
        removedOnConfirm = await appears(row, { state: "detached", timeout: 10000 });
      }
      check("removing a source asks first: Keep it keeps it, confirming removes it (M5)", "desktop", askedToRemove && keptSource && removedOnConfirm, `asked: ${askedToRemove}, kept after Keep it: ${keptSource}, removed after confirming: ${removedOnConfirm}`);

      // Revoke folder access (the panel lives in a collapsed section of the Sources workspace).
      await page.locator("summary", { hasText: "Add a folder from this Mac" }).click();
      await page.getByLabel("Folder path on the host computer").fill(grantRoot);
      await page.getByRole("button", { name: "Check Access" }).click();
      await page.getByRole("button", { name: "Grant Access", exact: true }).click();
      const grant = page.locator(".grant-row");
      await grant.waitFor({ timeout: 10000 });
      await grant.getByRole("button", { name: "Revoke" }).click();
      const revokeDialog = page.getByRole("dialog", { name: "Revoke folder access?" });
      const askedToRevoke = await appears(revokeDialog, { timeout: 3000 });
      let keptGrant = false;
      let revokedOnConfirm = false;
      if (askedToRevoke) {
        await revokeDialog.getByRole("button", { name: "Keep it" }).click();
        await revokeDialog.waitFor({ state: "detached" });
        keptGrant = (await grant.count()) === 1;
        await grant.getByRole("button", { name: "Revoke" }).click();
        await revokeDialog.getByRole("button", { name: "Revoke access" }).click();
        revokedOnConfirm = await appears(grant, { state: "detached", timeout: 10000 });
      }
      check("revoking folder access asks first: Keep it keeps it, confirming revokes (M5)", "desktop", askedToRevoke && keptGrant && revokedOnConfirm, `asked: ${askedToRevoke}, kept after Keep it: ${keptGrant}, revoked after confirming: ${revokedOnConfirm}`);

      await nav.getByRole("button", { name: "Memory", exact: true }).click();
      await page.getByLabel("Remember").fill("qa temp memory");
      await page.getByRole("button", { name: "Save", exact: true }).click();
      const deleteMemory = page.getByRole("button", { name: "Delete memory: qa temp memory" });
      await deleteMemory.waitFor();
      await deleteMemory.click();
      const memoryDialog = page.getByRole("dialog", { name: "Delete this memory?" });
      const askedToDelete = await appears(memoryDialog, { timeout: 3000 });
      let keptMemory = false;
      let deletedOnConfirm = false;
      if (askedToDelete) {
        await memoryDialog.getByRole("button", { name: "Keep it" }).click();
        await memoryDialog.waitFor({ state: "detached" });
        keptMemory = (await deleteMemory.count()) === 1;
        await deleteMemory.click();
        await memoryDialog.getByRole("button", { name: "Delete memory" }).click();
        deletedOnConfirm = await appears(deleteMemory, { state: "detached", timeout: 10000 });
      }
      check("deleting a memory asks first: Keep it keeps it, confirming deletes it (M5)", "desktop", askedToDelete && keptMemory && deletedOnConfirm, `asked: ${askedToDelete}, kept after Keep it: ${keptMemory}, deleted after confirming: ${deletedOnConfirm}`);
    } finally {
      await context.close();
      rmSync(grantRoot, { recursive: true, force: true });
    }
  });
}

// A reverse proxy in front of the backend that can keep an event stream silent while its socket stays open (what a phone
// that lost its connection looks like) or refuse event streams, so the client's handling can be observed.
async function startStreamProxy(targetPort) {
  const state = { freeze: false, refuse: false, streamRequests: 0 };
  const server = http.createServer((req, res) => {
    const isStream = /\/work-runs\/[^/]+\/events\/stream/.test(req.url);
    if (isStream) state.streamRequests += 1;
    if (isStream && state.refuse) {
      res.writeHead(502, { "content-type": "text/plain" });
      res.end("Bad Gateway");
      return;
    }
    const upstream = http.request(
      { host: "127.0.0.1", port: targetPort, method: req.method, path: req.url, headers: { ...req.headers, host: `127.0.0.1:${targetPort}` } },
      (up) => {
        res.writeHead(up.statusCode, up.headers);
        if (!isStream) {
          up.pipe(res);
          return;
        }
        up.on("data", (chunk) => {
          if (!state.freeze) res.write(chunk);
        });
        // A frozen stream never ends on the client's side either, as on a half-open connection.
        up.on("end", () => {
          if (!state.freeze) res.end();
        });
        res.on("close", () => up.destroy());
      },
    );
    upstream.on("error", () => {
      try {
        res.writeHead(502);
        res.end();
      } catch {
        /* the client is gone */
      }
    });
    req.pipe(upstream);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { state, base: `http://127.0.0.1:${server.address().port}`, close: () => server.close() };
}

async function streamScenario(browser, backendBase, backendPort) {
  const proxy = await startStreamProxy(backendPort);
  const openOn = async (conversationId) => {
    const context = await browser.newContext({ ...desktopProfile(), baseURL: proxy.base });
    await context.addInitScript((id) => localStorage.setItem("mucaro.activeConversation", JSON.stringify(id)), conversationId);
    return context;
  };
  try {
    // M3: the stream goes silent without an error (a half-open socket) while the run needs approval elsewhere.
    await guarded("silent event stream (M3)", "desktop", async () => {
      const { conversationId, runId } = await awaitingApprovalRun(backendBase, "qa silent stream");
      const context = await openOn(conversationId);
      const page = await context.newPage();
      try {
        await page.goto("/");
        await page.locator('article.run[data-state="AWAITING_APPROVAL"]').waitFor({ timeout: 20000 });
        proxy.state.freeze = true;
        // A second device approves while this page's stream is silent.
        const events = (await (await fetch(`${backendBase}/work-runs/${runId}/events`)).json()).events;
        const approval = events.find((event) => event.type === "approval_request").data;
        const approved = await fetch(`${backendBase}/tools/requests/${approval.tool_call_id}/approve`, jsonPost({ arguments_sha256: approval.arguments_sha256 }));
        await waitServerRun(backendBase, runId, ["COMPLETED"]);
        const started = Date.now();
        let seconds = null;
        while (Date.now() - started < 45000) {
          if ((await page.locator("article.run").last().getAttribute("data-state")) === "COMPLETED") {
            seconds = (Date.now() - started) / 1000;
            break;
          }
          await page.waitForTimeout(1000);
        }
        check("a silent event stream is detected and the page catches up by itself (M3)", "desktop", approved.ok && seconds !== null, seconds === null ? "still waiting for approval after 45 s" : `caught up after ${seconds.toFixed(1)} s`);
      } finally {
        proxy.state.freeze = false;
        await context.close();
      }
    });

    // M3b: a stream route that keeps failing is retried with backoff, not in a tight loop.
    await guarded("failing event stream (M3b)", "desktop", async () => {
      const { conversationId } = await awaitingApprovalRun(backendBase, "qa failing stream");
      proxy.state.refuse = true;
      const context = await openOn(conversationId);
      const page = await context.newPage();
      try {
        const first = proxy.state.streamRequests;
        await page.goto("/");
        await page.locator('article.run[data-state="AWAITING_APPROVAL"]').waitFor({ timeout: 20000 });
        await page.waitForTimeout(10000);
        const attempts = proxy.state.streamRequests - first;
        check("a refused event stream is retried with backoff, not in a tight loop (M3b)", "desktop", attempts >= 1 && attempts <= 10, `${attempts} stream attempts in about 10 s`);
      } finally {
        proxy.state.refuse = false;
        await context.close();
      }
    });

    // M3b: after repeated failures the chat says live updates are paused and offers Resume. A fake clock skips the waits.
    await guarded("paused event stream (M3b)", "desktop", async () => {
      const { conversationId } = await awaitingApprovalRun(backendBase, "qa paused stream");
      proxy.state.refuse = true;
      const context = await openOn(conversationId);
      const page = await context.newPage();
      try {
        const first = proxy.state.streamRequests;
        await page.clock.install();
        await page.goto("/");
        await page.locator('article.run[data-state="AWAITING_APPROVAL"]').waitFor({ timeout: 20000 });
        const notice = page.locator(".chat-notice");
        let paused = false;
        for (let step = 0; step < 90 && !paused; step += 1) {
          await page.clock.runFor(5000);
          await page.waitForTimeout(60);
          paused = await notice.isVisible();
        }
        const attempts = proxy.state.streamRequests - first;
        check("repeated stream failures pause live updates with a visible Resume (M3b)", "desktop", paused && attempts <= 12, `paused: ${paused}, ${attempts} attempts before pausing`);
        if (paused) {
          proxy.state.refuse = false;
          const resumedAt = proxy.state.streamRequests;
          await notice.getByRole("button", { name: "Resume" }).click();
          const cleared = await appears(notice, { state: "hidden", timeout: 8000 });
          await page.waitForTimeout(500);
          check("Resume reconnects the stream once the service answers (M3b)", "desktop", cleared && proxy.state.streamRequests > resumedAt, `notice cleared: ${cleared}, stream requests after Resume: ${proxy.state.streamRequests - resumedAt}`);
        }
      } finally {
        proxy.state.refuse = false;
        await context.close();
      }
    });
  } finally {
    proxy.close();
  }
}

const gateHeading = (page) => page.getByRole("heading", { name: "Enter the access token" });
const tokenField = (page) => page.locator("#gate-token"); // getByLabel also matches the section the heading labels
const refusalAlert = (page) => page.getByRole("alert").filter({ hasText: "was not accepted" });
const cleartextNotice = (page) => page.getByText("This connection is not encrypted");

// M7: a phone that opens a token-protected service must be able to enter the token.
async function tokenGateScenario(browser) {
  const token = randomBytes(32).toString("base64url");
  const port = await freePort();
  const base = `http://127.0.0.1:${port}`;
  const backend = await startBackend(mkdtempSync(path.join(tmpdir(), "mucaro-qa-gate-")), port, { MUCARO_AI_OPERATOR_TOKEN: token });
  try {
    if (!(await waitForHealth(base))) throw new Error(`token backend did not start:\n${backend.log()}`);
    await guarded("token gate (M7)", "iphone", async () => {
      const context = await browser.newContext({ ...iphoneProfile(), baseURL: base });
      const page = await context.newPage();
      try {
        await page.goto("/");
        const gated = await appears(gateHeading(page), { timeout: 10000 });
        const unreachable = await page.getByText("Mucaro's local service is not reachable.").isVisible().catch(() => false);
        check("a token-protected service asks for the token instead of saying it is unreachable (M7)", "iphone", gated && !unreachable, `gate shown: ${gated}, "unreachable" banner: ${unreachable}`);
        await shot(page, "iphone", "18-token-gate");
        const field = tokenField(page);
        const attributes = await field.evaluate((el) => ({
          type: el.type,
          capitalize: el.getAttribute("autocapitalize"),
          correct: el.getAttribute("autocorrect"),
          spellcheck: el.getAttribute("spellcheck"),
          fontSize: parseFloat(getComputedStyle(el).fontSize),
          height: Math.round(el.getBoundingClientRect().height),
        }));
        check("the token field is a password field iOS will not capitalize or correct, 16px+ and 44px tall (M7)", "iphone", attributes.type === "password" && attributes.capitalize === "none" && attributes.correct === "off" && attributes.spellcheck === "false" && attributes.fontSize >= 16 && attributes.height >= 44, JSON.stringify(attributes));
        check("the token field has no cleartext warning on this computer's own address (M7)", "iphone", (await cleartextNotice(page).count()) === 0);

        // A wrong token is refused with an explanation, and what was typed stays so a typo can be fixed.
        await field.fill("not-the-token");
        await page.getByRole("button", { name: "Show" }).click();
        const revealed = await field.evaluate((el) => el.type);
        await page.getByRole("button", { name: "Hide" }).click();
        await page.getByRole("button", { name: "Continue" }).click();
        const refused = await appears(refusalAlert(page));
        check("a wrong token is refused with an explanation, and Show reveals what was typed (M7)", "iphone", refused && revealed === "text" && (await field.inputValue()) === "not-the-token", `explained: ${refused}, revealed type: ${revealed}`);
        await shot(page, "iphone", "19-token-refused");

        // The right token opens the app and, by default, lives only as long as this tab.
        await field.fill(token);
        await page.getByRole("button", { name: "Continue" }).click();
        const entered = await appears(page.locator("#composer-input"), { timeout: 15000 });
        const stored = await page.evaluate(() => ({ session: sessionStorage.getItem("mucaro.sessionToken"), local: localStorage.getItem("mucaro.operatorToken") }));
        check("the right token opens the app and is kept for this tab only (M7)", "iphone", entered && stored.session === token && stored.local === null, `app opened: ${entered}, in session storage: ${stored.session === token}, in local storage: ${stored.local !== null}`);
        await page.reload();
        const survived = await appears(page.locator("#composer-input"), { timeout: 15000 });
        check("reloading the tab keeps the session authorized (M7)", "iphone", survived);

        // The computer restarts Mucaro and mints a new token: the next call is refused and the gate comes back.
        await page.route("**/sources", (route) => route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: { status: "ERROR_OPERATOR_AUTH_REQUIRED", detail: "Operator token required for this local action." } }) }));
        await page.getByRole("button", { name: "Open navigation" }).click();
        await page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: "Sources", exact: true }).click();
        const regated = await appears(gateHeading(page));
        const explained = await appears(refusalAlert(page), { timeout: 3000 });
        check("a token that stops working sends the person back to the gate with an explanation (M7)", "iphone", regated && explained, `gate: ${regated}, explained: ${explained}`);
        await page.unroute("**/sources");
        await tokenField(page).fill(token);
        await page.getByRole("button", { name: "Continue" }).click();
        // The person was in Sources when the token stopped working, and that is where they come back to.
        check("entering the token again returns to where the person was (M7)", "iphone", await appears(page.getByRole("heading", { name: "Sources" }), { timeout: 15000 }));
      } finally {
        await context.close();
      }
    });

    await guarded("remember on this device (M7)", "iphone", async () => {
      const context = await browser.newContext({ ...iphoneProfile(), baseURL: base });
      const page = await context.newPage();
      try {
        await page.goto("/");
        await gateHeading(page).waitFor({ timeout: 10000 });
        await tokenField(page).fill(token);
        await page.getByLabel("Remember on this device").check();
        await page.getByRole("button", { name: "Continue" }).click();
        await page.locator("#composer-input").waitFor({ timeout: 15000 });
        const kept = await page.evaluate(() => localStorage.getItem("mucaro.operatorToken"));
        const second = await context.newPage();
        await second.goto("/");
        const noGate = await appears(second.locator("#composer-input"), { timeout: 15000 });
        check("Remember on this device keeps the token for later visits (M7)", "iphone", kept === token && noGate, `stored: ${kept === token}, second page opened without the gate: ${noGate}`);
      } finally {
        await context.close();
      }
    });
  } finally {
    backend.kill("SIGTERM");
  }
}

// M7 and M25 as a phone meets them: the service is reached by a non-loopback address, so the server treats the browser
// as a remote client. Needs a non-loopback IPv4 address on this machine; without one it is reported, not skipped silently.
function nonLoopbackAddress() {
  for (const addresses of Object.values(networkInterfaces())) {
    for (const entry of addresses || []) {
      if ((entry.family === "IPv4" || entry.family === 4) && !entry.internal) return entry.address;
    }
  }
  return null;
}

async function lanScenario(browser, address) {
  const token = randomBytes(32).toString("base64url");
  const port = await freePort();
  const base = `http://${address}:${port}`;
  const dataDir = () => mkdtempSync(path.join(tmpdir(), "mucaro-qa-lan-"));

  // A service bound to the network with no token configured: remote clients are refused and told why.
  const open = await startBackend(dataDir(), port, {}, address);
  try {
    if (!(await waitForHealth(base))) throw new Error(`LAN backend did not start:\n${open.log()}`);
    await guarded("network access without a token (M7)", "iphone", async () => {
      const listed = await fetch(`${base}/conversations`);
      const refusal = await listed.json().catch(() => ({}));
      const written = await fetch(`${base}/memory`, jsonPost({ content: "remote write probe", kind: "fact" }));
      check("a remote client is refused when no token is configured, for reads and writes (M7)", "iphone", listed.status === 403 && written.status === 403 && refusal.detail?.status === "ERROR_REMOTE_ACCESS_REQUIRES_TOKEN", `GET /conversations ${listed.status}, POST /memory ${written.status}`);
      const health = await (await fetch(`${base}/health`)).json();
      const config = await (await fetch(`${base}/config`)).json();
      const leaks = ["project_root", "data_dir", "cors_origins"].filter((key) => key in health || key in config);
      check("a remote client does not learn host paths from /health or /config (M7)", "iphone", leaks.length === 0, leaks.join(", ") || "no host paths");
      const context = await browser.newContext({ ...iphoneProfile(), baseURL: base });
      const page = await context.newPage();
      try {
        await page.goto("/");
        const explained = await appears(page.getByRole("heading", { name: "Phone access is turned off" }), { timeout: 10000 });
        const retry = await page.getByRole("button", { name: "Try again" }).isVisible().catch(() => false);
        check("a phone sees why it was refused instead of a dead end (M7)", "iphone", explained && retry, `explained: ${explained}, retry offered: ${retry}`);
        await shot(page, "iphone", "20-phone-access-off");
      } finally {
        await context.close();
      }
    });
  } finally {
    open.kill("SIGTERM");
  }

  // The phone-access configuration: public bind, strong token.
  const lanPort = await freePort();
  const lanBase = `http://${address}:${lanPort}`;
  const protectedBackend = await startBackend(dataDir(), lanPort, { MUCARO_AI_OPERATOR_TOKEN: token, MUCARO_AI_ALLOW_PUBLIC_BIND: "1" }, address);
  try {
    if (!(await waitForHealth(lanBase))) throw new Error(`protected LAN backend did not start:\n${protectedBackend.log()}`);
    await guarded("network access with a token (M7)", "iphone", async () => {
      const without = await fetch(`${lanBase}/conversations`);
      const wrong = await fetch(`${lanBase}/conversations`, { headers: { "X-Mucaro-Token": "wrong" } });
      const right = await fetch(`${lanBase}/conversations`, { headers: { "X-Mucaro-Token": token } });
      check("over the network the token decides: none or wrong is 401, the right one is served (M7)", "iphone", without.status === 401 && wrong.status === 401 && right.status === 200, `none ${without.status}, wrong ${wrong.status}, right ${right.status}`);

      const link = await browser.newContext({ ...iphoneProfile(), baseURL: lanBase });
      const linkPage = await link.newPage();
      try {
        await linkPage.goto(`/#token=${token}`);
        const opened = await appears(linkPage.locator("#composer-input"), { timeout: 15000 });
        check("the phone link with the token in its fragment opens the app (M7)", "iphone", opened && !linkPage.url().includes("token="), `opened: ${opened}, address bar: ${linkPage.url().replace(token, "<token>")}`);
        await linkPage.getByRole("button", { name: "Open navigation" }).click();
        await linkPage.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: "Settings", exact: true }).click();
        const warned = await appears(cleartextNotice(linkPage));
        check("Settings says a network connection over http is not encrypted (M7)", "iphone", warned);
        await shot(linkPage, "iphone", "21-cleartext-notice");
      } finally {
        await link.close();
      }

      const bare = await browser.newContext({ ...iphoneProfile(), baseURL: lanBase });
      const barePage = await bare.newPage();
      try {
        await barePage.goto("/");
        const gated = await appears(gateHeading(barePage), { timeout: 10000 });
        check("the token gate warns that a network connection over http is not encrypted (M7)", "iphone", gated && (await appears(cleartextNotice(barePage), { timeout: 3000 })));
      } finally {
        await bare.close();
      }
    });
  } finally {
    protectedBackend.kill("SIGTERM");
  }

  // A public bind with a short token must refuse to start, in any profile.
  await guarded("weak token on a public bind (M7)", "iphone", async () => {
    const weakPort = await freePort();
    const weak = await startBackend(dataDir(), weakPort, { MUCARO_AI_OPERATOR_TOKEN: "too-short", MUCARO_AI_ALLOW_PUBLIC_BIND: "1" }, address);
    const exited = await Promise.race([new Promise((resolve) => weak.once("exit", (code) => resolve(code))), new Promise((resolve) => setTimeout(() => resolve(null), 20000))]);
    const answered = await fetch(`http://${address}:${weakPort}/health`).then(() => true, () => false);
    if (exited === null) weak.kill("SIGTERM");
    check("a public bind with a short token refuses to start (M7)", "iphone", exited !== null && exited !== 0 && !answered, `exit code ${exited}, answered requests: ${answered}`);
  });
}

// M25: an http://<network address> page has no navigator.clipboard, so Copy needs the selection-based fallback.
async function clipboardScenario(browser, base) {
  const withoutClipboardApi = () => {
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    window.__copied = [];
    document.addEventListener(
      "copy",
      () => {
        const el = document.activeElement;
        window.__copied.push(el && typeof el.value === "string" ? el.value.slice(el.selectionStart, el.selectionEnd) : String(getSelection()));
      },
      true,
    );
  };
  const copyStatus = async (page) => {
    await page.waitForFunction(() => /^Cop/.test(document.querySelector('div.sr-only[role="status"]')?.textContent || ""), null, { timeout: 5000 }).catch(() => {});
    return ((await page.locator('div.sr-only[role="status"]').textContent()) || "").trim();
  };
  await guarded("copy without the clipboard API (M25)", "desktop", async () => {
    const context = await browser.newContext({ ...desktopProfile(), baseURL: base });
    await context.addInitScript(withoutClipboardApi);
    const page = await context.newPage();
    try {
      await page.goto("/");
      await page.locator("#composer-input").waitFor();
      await ask(page, "How does source lineage work?");
      await page.locator("article.run").last().locator(".assistant-bubble").getByRole("button", { name: "Copy message text" }).click();
      const status = await copyStatus(page);
      const copied = await page.evaluate(() => window.__copied);
      check("Copy works where navigator.clipboard does not exist, as over http on a network (M25)", "desktop", /^Copied/.test(status) && copied.length === 1 && copied[0].length > 20, `"${status}", ${copied.length} copy event(s) of ${copied[0]?.length ?? 0} characters`);
    } finally {
      await context.close();
    }
  });
  await guarded("copy refused by the browser (M25)", "desktop", async () => {
    const context = await browser.newContext({ ...desktopProfile(), baseURL: base });
    await context.addInitScript(() => {
      Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
      document.execCommand = () => false;
      try {
        Object.defineProperty(window, "isSecureContext", { value: false, configurable: true });
        window.__insecureForced = true;
      } catch {
        window.__insecureForced = false;
      }
    });
    const page = await context.newPage();
    try {
      await page.goto("/");
      await page.locator("#composer-input").waitFor();
      await ask(page, "How does source lineage work?");
      await page.locator("article.run").last().locator(".assistant-bubble").getByRole("button", { name: "Copy message text" }).click();
      const status = await copyStatus(page);
      const forced = await page.evaluate(() => window.__insecureForced);
      const explains = forced ? /secure connection/.test(status) && !/not allowed/.test(status) : true;
      check("a refused copy says why instead of blaming a permission (M25)", "desktop", /^Copy failed/.test(status) && explains, `"${status}"`);
    } finally {
      await context.close();
    }
  });
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
  if (!onlyNew) {
    axeResults.push(...(await scenario(browser, base, "desktop", { viewport: { width: 1440, height: 900 } })));
    const iphone = devices["iPhone 13"];
    axeResults.push(...(await scenario(browser, base, "iphone", { ...iphone, viewport: { width: 390, height: 844 } })));
    await launchTokenScenario(browser, "desktop");
    const headers = (await fetch(`${base}/`)).headers;
    check("security headers served with the UI", "desktop", headers.get("x-frame-options") === "DENY" && (headers.get("content-security-policy") || "").includes("frame-ancestors 'none'"));
  }
  if (wantScenario("resilience")) await resilienceScenario(browser, base);
  if (wantScenario("streams")) await streamScenario(browser, base, port);
  if (wantScenario("gate")) await tokenGateScenario(browser);
  if (wantScenario("lan")) {
    const lanAddress = nonLoopbackAddress();
    if (lanAddress) await lanScenario(browser, lanAddress);
    else skippedNotes.push("Network-address checks (remote-client refusal, phone link, cleartext notice, weak-token refusal) did not run: this machine has no non-loopback IPv4 address.");
  }
  if (wantScenario("clipboard")) await clipboardScenario(browser, base);
  if (!onlyNew) {
    await offlineScenario(browser, base, "desktop", { viewport: { width: 1440, height: 900 } }, async () => {
      backend.kill("SIGTERM");
      await new Promise((r) => setTimeout(r, 800));
    });
  }
  await browser.close();
  status = checks.every((row) => row.pass) ? (onlyNew ? "RENDERED_QA_PARTIAL" : "RENDERED_QA_PASS") : "RENDERED_QA_FAIL";
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
    "Phone access is exercised through a non-loopback address of this machine, not from a physical phone on a real network.",
    ...skippedNotes,
    ...(onlyNew ? ["Development run (--only-new): the base desktop and iPhone scenarios did not run, so this is not a complete receipt."] : []),
  ],
};
writeFileSync(path.join(outDir, "receipt.json"), `${JSON.stringify(receipt, null, 2)}\n`);
console.log(`${status} checks=${receipt.passed}/${checks.length} receipt=${path.relative(ROOT, path.join(outDir, "receipt.json"))}`);
process.exit(status === "RENDERED_QA_FAIL" ? 1 : 0);
