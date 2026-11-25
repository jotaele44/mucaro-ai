import test from "node:test";
import assert from "node:assert/strict";
import { bboxPreview, execView, familyOf, gisView, ocrView, prRows, repoStatusView } from "../src/resultViews.js";

const gisResult = {
  status: "GIS_INSPECT_OK",
  source: { crs: "EPSG:4326", crs_status: "DEFAULTED_EPSG4326", feature_count: 2, sha256: "a".repeat(64) },
  geometry_counts: { Point: 1, Polygon: 1 },
  features: [
    { feature_index: 0, feature_id: "p1", geometry_type: "Point", geometry_status: "VALID", bbox: [-66.8, 17.9, -66.8, 17.9], warnings: [] },
    { feature_index: 1, feature_id: "a1", geometry_type: "Polygon", geometry_status: "VALID", bbox: [-67, 18, -66, 18.5], warnings: ["W"] },
  ],
  warnings: [],
};

test("tools map to the workspace family that shows them", () => {
  assert.equal(familyOf("gis.inspect"), "gis");
  assert.equal(familyOf("ocr.analyze_image"), "ocr");
  assert.equal(familyOf("coding.exec"), "code");
  assert.equal(familyOf("github.pr_view"), "pr");
  assert.equal(familyOf("coding.push"), null, "mutating coding tools are not shown as read-only code results");
  assert.equal(familyOf("web.fetch"), null);
});

test("a GIS report becomes CRS, counts and a features table", () => {
  const view = gisView(gisResult);
  assert.equal(view.crs, "EPSG:4326");
  assert.equal(view.featureCount, 2);
  assert.deepEqual(view.counts, [["Point", 1], ["Polygon", 1]]);
  assert.deepEqual(view.features.map((f) => [f.id, f.geometry, f.status]), [["p1", "Point", "VALID"], ["a1", "Polygon", "VALID"]]);
  assert.equal(gisView({ status: "ERROR" }), null, "a result without features is not drawn as GIS");
});

test("the bounding-box preview keeps relative position, points as dots and north up", () => {
  const preview = bboxPreview(gisView(gisResult).features, 320, 200, 8);
  assert.deepEqual(preview.bounds, [-67, 17.9, -66, 18.5]);
  const [point, box] = preview.shapes;
  assert.equal(point.kind, "point");
  assert.equal(box.kind, "box");
  assert.ok(box.y < point.cy, "the polygon to the north is drawn above the point to the south");
  for (const shape of preview.shapes) {
    const [x, y] = shape.kind === "point" ? [shape.cx, shape.cy] : [shape.x, shape.y];
    assert.ok(x >= 0 && x <= 320 && y >= 0 && y <= 200);
  }
  assert.equal(bboxPreview([{ bbox: null }]), null);
});

test("an OCR report becomes text and spans with low confidence counted", () => {
  const view = ocrView({
    status: "OCR_ANALYSIS_OK",
    text: { raw: "Hel1o", normalized: "Hel1o", canonical: "Hello" },
    engine: { name: "tesseract", version: "5.3" },
    source: { width: 100, height: 40 },
    spans: [
      { canonical_text: "Hello", confidence: 91, confidence_state: "HIGH", line_num: 1 },
      { canonical_text: "wor1d", confidence: 30, confidence_state: "LOW", line_num: 1 },
    ],
  });
  assert.equal(view.text, "Hello");
  assert.equal(view.lowConfidence, 1);
  assert.equal(view.engine, "tesseract 5.3");
  assert.equal(view.size, "100×40");
});

test("repository status and execution receipts become reviewable facts", () => {
  const repo = repoStatusView({ branch: "main", head_sha: "0123456789abcdef", dirty_status: "DIRTY", staged_files: ["a"], unstaged_files: [], untracked_files: ["b"] });
  assert.deepEqual([repo.branch, repo.head, repo.staged, repo.untracked], ["main", "0123456789ab", ["a"], ["b"]]);
  const run = execView({ exit_code: 1, duration_ms: 42, command_argv: ["pytest", "-q"], stdout: { text: "1 failed", truncated: false }, stderr: { text: "", truncated: true } });
  assert.deepEqual([run.exitCode, run.passed, run.command, run.stdout, run.truncated], [1, false, "pytest -q", "1 failed", true]);
  assert.equal(execView({ status: "X" }), null);
});

test("pull requests become rows, and only https links survive", () => {
  const rows = prRows({
    pull_requests: [
      { number: 7, title: "Fix", state: "OPEN", draft: false, head_sha: "abcdef1234", head_ref: "fix", base_ref: "main", url: "https://github.com/o/r/pull/7" },
      { number: 8, title: "WIP", state: "OPEN", draft: true, url: "javascript:alert(1)" },
    ],
  });
  assert.deepEqual(rows.map((r) => [r.number, r.state, r.head, r.url]), [[7, "open", "abcdef1", "https://github.com/o/r/pull/7"], [8, "draft", null, null]]);
  assert.deepEqual(prRows({ pull_request: { number: 9, title: "One", state: "MERGED" } }).map((r) => r.state), ["merged"]);
});
