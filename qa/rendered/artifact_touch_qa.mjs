import { chromium, webkit } from "./node_modules/playwright/index.mjs";
import { spawn, execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, mkdtempSync, mkdirSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const sha = (bytes) => createHash("sha256").update(bytes).digest("hex");
const head = () => execFileSync("git", ["rev-parse", "HEAD"], { cwd: ROOT }).toString().trim();
const sourceHead = head();
const dirtyPaths = execFileSync("git", ["status", "--porcelain"], { cwd: ROOT }).toString().trim();
const frontendInputs = () => execFileSync("git", ["ls-files", "-z", "--", "frontend"], { cwd: ROOT }).toString().split("\0").filter(Boolean).map((file) => ({ file, sha256: sha(readFileSync(path.join(ROOT, file))) }));
const inputs = frontendInputs();
execFileSync(process.execPath, [path.join(ROOT, "frontend/node_modules/vite/bin/vite.js"), "build"], { cwd: path.join(ROOT, "frontend"), stdio: "pipe" });
const data = mkdtempSync(path.join(tmpdir(), "mucaro-artifact-touch-"));
const env = { ...process.env, MUCARO_AI_DATA_DIR: data, MUCARO_AI_LIVE_MODE: "0" };
const python = process.env.MUCARO_PYTHON || "python3";
const fixture = JSON.parse(execFileSync(python, ["-c", `
import json
from backend.app.config import settings as cfg
from backend.app.artifact_identity import create_artifact_record
cfg.ensure_dirs()
raw=b'{"type":"FeatureCollection","features":[]}'
source=create_artifact_record(processed_dir=cfg.processed_dir,artifact_type='SOURCE',payload=raw,logical_identity='authored-touch-fixture',producer='touch-qa',producer_version='1')
output=cfg.export_dir/'touch-fixture.geojson';output.write_bytes(raw)
record=create_artifact_record(processed_dir=cfg.processed_dir,artifact_type='DERIVED',payload=raw,logical_identity='authored-touch-output',producer='touch-qa',producer_version='1',parent_artifact_ids=[source.artifact_id],source_path_or_uri=str(output))
print(json.dumps({'artifact_id':record.artifact_id,'sha256':record.sha256,'bytes':len(raw)}))
`], { cwd: ROOT, env }).toString());
const port = await new Promise((resolve) => { const server = createServer(); server.listen(0, "127.0.0.1", () => { const port = server.address().port; server.close(() => resolve(port)); }); });
const base = `http://127.0.0.1:${port}`;
const child = spawn(python, ["-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", String(port)], { cwd: ROOT, env, stdio: "ignore" });
const checks = [];
const browsers = [];
const receipt = {
  status: "ARTIFACT_TOUCH_QA_FAIL", source_head_sha: sourceHead, worktree_clean: !dirtyPaths, test_script_sha256: sha(readFileSync(fileURLToPath(import.meta.url))), frontend_inputs: inputs, created_utc: new Date().toISOString(),
  fixture, checks,
  limitations: ["Emulated touch events, not physical-device or native-WKWebView proof.", "Authored output fixture; this check does not assess GIS engine correctness or full accessibility."],
};
try {
  const deadline = Date.now() + 30000;
  while (true) {
    try { if ((await fetch(`${base}/health`)).ok) break; } catch {}
    if (Date.now() >= deadline) throw new Error("Touch test service did not start");
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  const servedIndex = Buffer.from(await (await fetch(base)).arrayBuffer());
  receipt.served_index_sha256 = sha(servedIndex);
  if (receipt.served_index_sha256 !== sha(readFileSync(path.join(ROOT, "frontend/dist/index.html")))) throw new Error("Served frontend differs from local build");
  for (const engine of [chromium, webkit]) {
    const browser = await engine.launch({ headless: true }); browsers.push(browser);
    for (const viewport of [{ width: 820, height: 1180 }, { width: 390, height: 844 }]) {
      const context = await browser.newContext({ viewport, hasTouch: true, baseURL: base });
      const page = await context.newPage();
      await page.addInitScript(() => { window.__touchStarts = 0; document.addEventListener("touchstart", () => { window.__touchStarts += 1; }, { passive: true }); });
      let requests = 0;
      page.on("request", (request) => { if (request.url().endsWith(`/artifacts/${fixture.artifact_id}/download`)) requests += 1; });
      await page.goto("/");
      await page.getByRole("heading", { name: "What should Mucaro work on?", exact: true }).waitFor();
      const nav = page.getByRole("button", { name: "Open navigation", exact: true });
      if (await nav.isVisible()) await nav.tap();
      await page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: "Artifacts", exact: true }).tap();
      const row = page.getByRole("list", { name: "Saved output artifacts", exact: true }).getByRole("listitem");
      await row.getByText(fixture.artifact_id, { exact: true }).waitFor();
      await row.getByRole("button", { name: "Inspect lineage", exact: true }).tap();
      await page.getByText(fixture.sha256, { exact: true }).waitFor();
      await page.getByRole("button", { name: "Close artifact details", exact: true }).tap();
      const downloadEvent = page.waitForEvent("download");
      await row.getByRole("button", { name: "Download saved file", exact: true }).tap();
      const download = await downloadEvent;
      const raw = readFileSync(await download.path());
      const touchStarts = await page.evaluate(() => window.__touchStarts);
      const pass = requests === 1 && touchStarts >= 4 && raw.length === fixture.bytes && sha(raw) === fixture.sha256;
      checks.push({ engine: engine.name(), version: browser.version(), viewport, touchStarts, requests, sha256: sha(raw), pass });
      console.log(`${pass ? "PASS" : "FAIL"} ${engine.name()} ${viewport.width}px touches=${touchStarts} downloads=${requests}`);
      await context.close();
    }
    await browser.close();
  }
  if (checks.length !== 4 || checks.some((check) => !check.pass) || head() !== sourceHead || JSON.stringify(inputs) !== JSON.stringify(frontendInputs()) || execFileSync("git", ["status", "--porcelain"], { cwd: ROOT }).toString().trim() !== dirtyPaths) throw new Error("Touch acceptance or source identity failed");
  receipt.status = "ARTIFACT_TOUCH_QA_PASS";
} catch (error) { receipt.error = String(error); }
finally {
  for (const browser of browsers) await browser.close();
  child.kill("SIGTERM");
  const output = path.join(ROOT, "data/exports/release_receipts", `artifact_touch-${sourceHead.slice(0, 12)}.json`);
  mkdirSync(path.dirname(output), { recursive: true });
  writeFileSync(output, `${JSON.stringify(receipt, null, 2)}\n`);
  console.log(`${receipt.status} ${output}`);
}
process.exitCode = receipt.status === "ARTIFACT_TOUCH_QA_PASS" ? 0 : 1;
