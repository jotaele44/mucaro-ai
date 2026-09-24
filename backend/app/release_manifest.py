"""Final release manifest (MATRIX-020): one frozen record of what a release is made of.

Every entry is either a concrete value with a hash, or an explicit
``{"state": "BLOCKED" | "NOT_APPLICABLE" | "MISSING", ...}`` record that says why it
is absent. Nothing is left implicit, so an incomplete release is visible as such.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .release_scope import LEDGER_FILES, RELEASE_DIR, compute_denominator, load_release_scope, sha256_file

SCHEMA_VERSION = "mucaro-final-release-manifest/1.0"
DEPENDENCY_LOCKS = (
    "pyproject.toml",
    "packaging/macos/requirements-macos-arm64.in",
    "packaging/macos/requirements-macos-arm64.lock",
    "backend/requirements.txt",
    "frontend/package-lock.json",
    "qa/rendered/package-lock.json",
)
BENCHMARK_FIXTURES = (
    "backend/tests/fixtures/retrieval_eval.json",
    "backend/tests/fixtures/retrieval_eval_large.json",
    "backend/tests/fixtures/retrieval_eval_mixed_formats.json",
    "backend/tests/fixtures/answer_trace_eval.json",
    "backend/tests/fixtures/gis_eval.json",
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()


def _file_row(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    if not path.is_file():
        return {"path": relative, "state": "MISSING"}
    return {"path": relative, "sha256": sha256_file(path), "bytes": path.stat().st_size}


def _load(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _receipt_ref(path: Path, root: Path) -> dict[str, Any]:
    payload = _load(path) or {}
    try:
        shown = path.relative_to(root).as_posix()
    except ValueError:
        shown = str(path)
    return {"path": shown, "sha256": sha256_file(path), "receipt_type": payload.get("receipt_type"), "status": payload.get("status")}


def build_final_release_manifest(
    root: Path,
    *,
    head: str,
    macos_build_receipt: Path | None = None,
    macos_e2e_receipt: Path | None = None,
    signing_receipt: Path | None = None,
    notarization_receipt: Path | None = None,
) -> dict[str, Any]:
    loaded, _errors = load_release_scope(root)
    manifest = loaded.get("manifest") or {}
    acceptance = loaded.get("acceptance") or {"rows": []}

    specifications = []
    for spec in manifest.get("bound_specifications", []):
        path = root / spec["path"]
        actual = sha256_file(path) if path.is_file() else None
        specifications.append({"spec_id": spec["spec_id"], "path": spec["path"], "sha256": spec["sha256"], "verified": actual == spec["sha256"]})
    specifications += [{"spec_id": row["name"], "state": "BLOCKED", "blocker": row.get("blocker_id")} for row in manifest.get("missing_specifications", [])]

    receipts_dir = root / "data" / "exports" / "release_receipts"
    head_receipts = sorted(receipts_dir.glob(f"*-{head[:12]}.json")) if receipts_dir.is_dir() else []
    test_receipts = [_receipt_ref(path, root) for path in head_receipts if not path.name.startswith("security_gates-")]
    security_path = receipts_dir / f"security_gates-{head[:12]}.json"
    security = _load(security_path)

    build = _load(macos_build_receipt)
    e2e = _load(macos_e2e_receipt)
    if build and build.get("head_sha") == head and build.get("status") == "MACOS_APP_BUILD_OK":
        artifact = build.get("artifact", {})
        application: dict[str, Any] = {
            "state": "PRESENT",
            "zip": Path(str(artifact.get("zip", ""))).name,
            "zip_sha256": artifact.get("zip_sha256"),
            "bundle_tree_sha256": (build.get("bundle") or {}).get("tree_sha256"),
            "version": build.get("version"),
            "build_number": build.get("build_number"),
            "minimum_os": build.get("minimum_os"),
            "receipt": _receipt_ref(macos_build_receipt, root) if macos_build_receipt else None,
        }
    else:
        application = {"state": "MISSING", "reason": "no MACOS_APP_BUILD_OK receipt for this head"}

    def blocked_unless(path: Path | None, what: str) -> dict[str, Any]:
        if path is not None and path.is_file():
            return {"state": "PRESENT", **_receipt_ref(path, root)}
        return {"state": "BLOCKED", "blocker": "BLK-SIGN-001", "reason": f"{what} requires an Apple Developer ID account"}

    if e2e and e2e.get("head_sha") == head:
        offline = (e2e.get("workflow") or {}).get("offline_manifest") or {}
        capabilities_source = "packaged app offline manifest (macOS E2E receipt)"
    else:
        from .offline import build_offline_manifest

        offline = {}
        for row in build_offline_manifest()["capabilities"]:
            offline.setdefault(row["state_now"], []).append(row["capability"])
        capabilities_source = "source-tree offline manifest (no packaged-app receipt for this head)"

    ledgers = [_file_row(root, (RELEASE_DIR / name).as_posix()) for name, _schema in LEDGER_FILES.values()]
    blockers = loaded.get("blockers", {}).get("blockers", [])
    contradictions = loaded.get("contradictions", {}).get("contradictions", [])

    record = {
        "schema_version": SCHEMA_VERSION,
        "created_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source_revision": head,
        "source_tree_sha": _git(root, "rev-parse", f"{head}^{{tree}}"),
        "worktree_clean": not _git(root, "status", "--porcelain"),
        "dependency_locks": [_file_row(root, path) for path in DEPENDENCY_LOCKS],
        "specifications": specifications,
        "requirement_manifest": {
            **_file_row(root, (RELEASE_DIR / LEDGER_FILES["manifest"][0]).as_posix()),
            "denominator": compute_denominator(acceptance),
        },
        "benchmark_versions": [_file_row(root, path) for path in BENCHMARK_FIXTURES],
        "test_receipts": test_receipts or [{"state": "MISSING", "reason": "no release receipts for this head"}],
        "security_receipts": _receipt_ref(security_path, root) if security else {"state": "MISSING", "reason": "run scripts/certify_security_gates.py"},
        "sbom": (security or {}).get("payload", {}).get("sbom") or {"state": "MISSING"},
        "application_artifact": application,
        "installer": {"state": "NOT_APPLICABLE", "reason": "the distributable is the ditto zip of the .app (no .pkg/.dmg in the release class)"},
        "signing_receipt": blocked_unless(signing_receipt, "Developer ID signing"),
        "notarization_receipt": blocked_unless(notarization_receipt, "notarization and stapling"),
        "provenance": {
            "builder": "scripts/build_macos_app.py",
            "build_host": (build or {}).get("host") or {"state": "MISSING"},
            "build_inputs": (build or {}).get("inputs") or {"state": "MISSING"},
            "e2e_receipt": _receipt_ref(macos_e2e_receipt, root) if e2e and macos_e2e_receipt else {"state": "MISSING"},
        },
        "capabilities": {
            "source": capabilities_source,
            "supported": sorted(offline.get("AVAILABLE_LOCALLY", [])),
            "requires_network": sorted(offline.get("REQUIRES_REMOTE_SERVICE", []) + offline.get("UNAVAILABLE_OFFLINE", [])),
            "unsupported_in_this_build": sorted(offline.get("LOCAL_DEPENDENCY_MISSING", [])),
        },
        "ledgers": ledgers,
        "active_blockers": sorted(row["blocker_id"] for row in blockers if row.get("status") == "ACTIVE"),
        "unresolved_material_contradictions": sorted(row["contradiction_id"] for row in contradictions if row.get("material") and row.get("state") == "UNRESOLVED"),
    }
    missing = [key for key, value in record.items() if isinstance(value, dict) and value.get("state") == "MISSING"]
    record["missing_entries"] = missing
    record["complete_except_blocked"] = not missing and record["worktree_clean"]
    return record


def write_final_release_manifest(root: Path, record: dict[str, Any]) -> tuple[Path, str]:
    out = root / "data" / "exports" / "release_manifests"
    out.mkdir(parents=True, exist_ok=True)
    text = json.dumps(record, indent=2, sort_keys=True) + "\n"
    path = out / f"final_release_manifest-{record['source_revision'][:12]}.json"
    path.write_text(text, encoding="utf-8")
    return path, hashlib.sha256(text.encode("utf-8")).hexdigest()
