#!/usr/bin/env python3
"""Fail-closed certification of the frozen video-derived UI denominator and source coverage."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
DENOMINATOR = ROOT / "_architecture/ui/video_ui_denominator_v1.json"
COVERAGE = ROOT / "_architecture/ui/video_ui_coverage_v1.json"
FORBIDDEN_PRODUCT_UI = re.compile(r"\b(ChatGPT|Claude)\b", re.I)


class CoverageFailure(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CoverageFailure(f"INVALID_JSON {path}: {exc}") from exc


def certify(denominator_path: Path = DENOMINATOR, coverage_path: Path = COVERAGE) -> dict:
    denominator = load_json(denominator_path)
    coverage = load_json(coverage_path)
    observations = denominator.get("observations")
    rows = coverage.get("coverage")
    if not isinstance(observations, list) or not isinstance(rows, list):
        raise CoverageFailure("DENOMINATOR_OR_COVERAGE_NOT_LIST")
    denominator_ids = [row.get("id") for row in observations]
    coverage_ids = [row.get("observation_id") for row in rows]
    if len(denominator_ids) != len(set(denominator_ids)):
        raise CoverageFailure("DUPLICATE_DENOMINATOR_ID")
    if len(coverage_ids) != len(set(coverage_ids)):
        raise CoverageFailure("DUPLICATE_COVERAGE_ID")
    missing = sorted(set(denominator_ids) - set(coverage_ids))
    extra = sorted(set(coverage_ids) - set(denominator_ids))
    if missing or extra:
        raise CoverageFailure(f"DENOMINATOR_MISMATCH missing={missing} extra={extra}")

    referenced: set[str] = set()
    test_surfaces: set[str] = set()
    for row in rows:
        impl = row.get("implementation")
        regressions = row.get("regression_surfaces")
        if not isinstance(impl, list) or not impl:
            raise CoverageFailure(f"NO_IMPLEMENTATION {row.get('observation_id')}")
        if not isinstance(regressions, list) or not regressions:
            raise CoverageFailure(f"NO_REGRESSION_SURFACE {row.get('observation_id')}")
        for raw in impl:
            path = ROOT / raw
            if not path.is_file():
                raise CoverageFailure(f"MISSING_IMPLEMENTATION_PATH {row.get('observation_id')} {raw}")
            referenced.add(raw)
        for raw in regressions:
            path = ROOT / raw
            if not path.is_file():
                raise CoverageFailure(f"MISSING_REGRESSION_PATH {row.get('observation_id')} {raw}")
            test_surfaces.add(raw)

    ui_files = sorted(
        path for path in referenced
        if path.startswith("frontend/") and (ROOT / path).suffix in {".js", ".jsx", ".css"}
    )
    forbidden = {}
    for raw in ui_files:
        hits = sorted(set(FORBIDDEN_PRODUCT_UI.findall((ROOT / raw).read_text(encoding="utf-8"))))
        if hits:
            forbidden[raw] = hits
    if forbidden:
        raise CoverageFailure(f"PROVIDER_PRODUCT_UI_LEAK {forbidden}")

    declared_count = coverage.get("counts", {}).get("denominator")
    if declared_count != len(denominator_ids):
        raise CoverageFailure(f"DECLARED_COUNT_MISMATCH {declared_count} != {len(denominator_ids)}")
    if coverage.get("counts", {}).get("unmapped") != 0:
        raise CoverageFailure("UNMAPPED_NONZERO")

    return {
        "state": "PASS",
        "claim": "source-coverage-integrity-only",
        "denominator_count": len(denominator_ids),
        "coverage_count": len(coverage_ids),
        "implementation_paths": len(referenced),
        "regression_surfaces": len(test_surfaces),
        "forbidden_product_ui_hits": forbidden,
        "physical_device_certification": coverage.get("global_gates", {}).get("physical_mobile"),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--denominator", type=Path, default=DENOMINATOR)
    parser.add_argument("--coverage", type=Path, default=COVERAGE)
    parser.add_argument("--video-a", type=Path)
    parser.add_argument("--video-b", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = certify(args.denominator, args.coverage)
        denominator = load_json(args.denominator)
        expected = {row["id"]: row["sha256"] for row in denominator.get("recordings", [])}
        source_checks = {}
        for key, path in (("A", args.video_a), ("B", args.video_b)):
            if path is None:
                source_checks[key] = "OPEN_NOT_SUPPLIED"
            else:
                actual = sha256(path)
                source_checks[key] = "PASS" if actual == expected.get(key) else f"FAIL:{actual}"
        if any(str(value).startswith("FAIL") for value in source_checks.values()):
            raise CoverageFailure(f"VIDEO_HASH_MISMATCH {source_checks}")
        result["source_video_hash_checks"] = source_checks
        encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.output:
            if args.output.exists():
                raise CoverageFailure("REFUSE_OVERWRITE_RECEIPT")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded, encoding="utf-8")
        print(encoded, end="")
        return 0
    except CoverageFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
