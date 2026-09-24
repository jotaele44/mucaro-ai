"""Release control plane: scope manifest, acceptance ledger, DAG, and certification verdict.

Implements the machine-checkable parts of the release-engineering contract
(`docs/release/MUCARO_RELEASE_ENGINEERING_CONTRACT_V1.md`):

- KERNEL-002 / KERNEL-003: every requirement enumerated from a bound specification
  is classified exactly once; re-enumeration detects silent scope loss.
- KERNEL-004: reproducible current-state snapshot (credential presence only).
- KERNEL-006: acyclic dependency DAG with blocker binding.
- KERNEL-014 / KERNEL-019 / KERNEL-020: contradiction, scope-delta and blocker
  ledgers that may grow but never silently shrink against a frozen checkpoint.
- MATRIX-001..004 and the TERMINAL CERTIFICATION RULE: denominator arithmetic
  and a verdict that stays PROVISIONAL until every certification condition holds.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema


RELEASE_DIR = Path("_architecture") / "release"
FROZEN_DIR = RELEASE_DIR / "frozen"
SCHEMA_DIR = Path("schemas")

LEDGER_FILES: dict[str, tuple[str, str]] = {
    "manifest": ("release_scope_manifest_v1.json", "release_scope_manifest.schema.json"),
    "acceptance": ("acceptance_ledger_v1.json", "release_acceptance_ledger.schema.json"),
    "dag": ("release_dag_v1.json", "release_dag.schema.json"),
    "blockers": ("blocker_ledger_v1.json", "release_blocker_ledger.schema.json"),
    "contradictions": ("contradiction_ledger_v1.json", "release_contradiction_ledger.schema.json"),
    "scope_deltas": ("scope_delta_ledger_v1.json", "release_scope_delta_ledger.schema.json"),
}
SNAPSHOT_FILE = ("current_state_snapshot_v1.json", "release_state_snapshot.schema.json")
FROZEN_KINDS = ("manifest", "blockers", "contradictions", "scope_deltas")

REQUIREMENT_STATES = ("PASS", "FAIL", "OPEN", "BLOCKED", "PROVISIONAL", "NOT_APPLICABLE")
DENOMINATOR_ENTERING_DELTAS = {"REQUIRED_BY_EXISTING_SCOPE", "DEFECT_REVEALED"}

STATUS_CERTIFIED = "MUCARO_AI_RELEASE_GRADE_CERTIFIED"
STATUS_PROVISIONAL = "MUCARO_AI_RELEASE_STATUS_PROVISIONAL"
STATUS_INVALID = "MUCARO_AI_RELEASE_SCOPE_INVALID"

# Credential names whose presence (never value) is recorded in snapshots.
CREDENTIAL_NAMES: tuple[tuple[str, str], ...] = (
    ("ANTHROPIC_API_KEY", "remote model provider (Anthropic)"),
    ("MUCARO_BRAVE_SEARCH_API_KEY", "governed live web search"),
    ("MUCARO_AI_OPERATOR_TOKEN", "local operator token"),
    ("APPLE_DEVELOPER_ID_APPLICATION", "macOS Developer ID code signing identity"),
    ("APPLE_NOTARY_KEYCHAIN_PROFILE", "macOS notarization credentials"),
    ("GITHUB_TOKEN", "hosted governance API access"),
)

_EVIDENCE_TEST_SEPARATOR = "::"


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _error(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def _read_json(path: Path) -> tuple[Any, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError as exc:
        return None, f"json_invalid:{exc.msg}:line{exc.lineno}"
    except OSError as exc:
        return None, str(exc)


def enumerate_bound_requirements(root: Path, spec: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Re-enumerate requirement IDs from a bound specification's bytes."""
    errors: list[dict[str, str]] = []
    path = root / spec["path"]
    if not path.is_file():
        return [], [_error("ERROR_SPEC_FILE_MISSING", f"{spec['spec_id']}:{spec['path']}")]
    actual = sha256_file(path)
    if actual != spec["sha256"]:
        errors.append(
            _error("ERROR_SPEC_HASH_MISMATCH", f"{spec['spec_id']} bound={spec['sha256']} actual={actual}")
        )
    pattern = re.compile(spec["id_pattern"])
    lines = path.read_text(encoding="utf-8").splitlines()
    found: list[dict[str, str]] = []
    for line in lines:
        match = pattern.match(line)
        if match:
            found.append(
                {
                    "requirement_id": f"{spec['id_prefix']}{match.group('id')}",
                    "title": match.group("title").strip(),
                    "source": spec["spec_id"],
                }
            )
    stripped = {line.strip() for line in lines}
    for named in spec.get("extra_named_requirements", []):
        if named["heading"] not in stripped:
            errors.append(_error("ERROR_SPEC_NAMED_REQUIREMENT_MISSING", f"{spec['spec_id']}:{named['heading']}"))
            continue
        found.append({"requirement_id": named["requirement_id"], "title": named["heading"], "source": spec["spec_id"]})
    counts = Counter(row["requirement_id"] for row in found)
    for requirement_id, count in sorted(counts.items()):
        if count > 1:
            errors.append(_error("ERROR_SPEC_DUPLICATE_REQUIREMENT_ID", f"{spec['spec_id']}:{requirement_id} x{count}"))
    return found, errors


def load_release_scope(root: Path, release_dir: Path | None = None) -> tuple[dict[str, Any], list[dict[str, str]]]:
    directory = root / (release_dir or RELEASE_DIR)
    loaded: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    for kind, (filename, schema_name) in LEDGER_FILES.items():
        payload, read_error = _read_json(directory / filename)
        if read_error == "missing":
            errors.append(_error("ERROR_SCOPE_FILE_MISSING", f"{kind}:{filename}"))
            continue
        if read_error:
            errors.append(_error("ERROR_SCOPE_JSON_INVALID", f"{kind}:{read_error}"))
            continue
        schema, schema_error = _read_json(root / SCHEMA_DIR / schema_name)
        if schema_error:
            errors.append(_error("ERROR_SCOPE_SCHEMA_UNREADABLE", f"{schema_name}:{schema_error}"))
            continue
        validator = jsonschema.Draft202012Validator(schema)
        schema_errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path))
        if schema_errors:
            for item in schema_errors[:20]:
                location = "/".join(str(part) for part in item.absolute_path)
                errors.append(_error("ERROR_SCOPE_SCHEMA_INVALID", f"{kind}:{location}:{item.message}"))
            continue
        loaded[kind] = payload
    return loaded, errors


def _evidence_ref_errors(root: Path, ref: str) -> list[dict[str, str]]:
    """Evidence refs are `path` or `path::test_name`; both parts must exist."""
    path_part, _, test_name = ref.partition(_EVIDENCE_TEST_SEPARATOR)
    if path_part.startswith(("http://", "https://", "receipt:", "external:")):
        return []
    target = root / path_part
    if not target.exists():
        return [_error("ERROR_EVIDENCE_REF_MISSING", ref)]
    if test_name:
        if not target.is_file():
            return [_error("ERROR_EVIDENCE_REF_MISSING", ref)]
        text = target.read_text(encoding="utf-8", errors="replace")
        if not re.search(rf"^\s*(async\s+)?def {re.escape(test_name)}\(", text, flags=re.MULTILINE):
            return [_error("ERROR_EVIDENCE_TEST_MISSING", ref)]
    return []


def _dag_cycle(nodes: dict[str, dict[str, Any]]) -> list[str] | None:
    visiting: set[str] = set()
    done: set[str] = set()
    stack: list[str] = []

    def visit(node_id: str) -> list[str] | None:
        if node_id in done:
            return None
        if node_id in visiting:
            return stack[stack.index(node_id):] + [node_id]
        visiting.add(node_id)
        stack.append(node_id)
        for dependency in nodes[node_id]["dependencies"]:
            if dependency in nodes:
                cycle = visit(dependency)
                if cycle:
                    return cycle
        stack.pop()
        visiting.discard(node_id)
        done.add(node_id)
        return None

    for node_id in sorted(nodes):
        cycle = visit(node_id)
        if cycle:
            return cycle
    return None


def latest_frozen_checkpoint(root: Path) -> Path | None:
    directory = root / FROZEN_DIR
    if not directory.is_dir():
        return None
    checkpoints = sorted(path for path in directory.iterdir() if path.is_dir())
    return checkpoints[-1] if checkpoints else None


def _frozen_comparison_errors(root: Path, loaded: dict[str, Any], checkpoint: Path) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    frozen: dict[str, Any] = {}
    for kind in FROZEN_KINDS:
        payload, read_error = _read_json(checkpoint / LEDGER_FILES[kind][0])
        if read_error:
            errors.append(_error("ERROR_FROZEN_CHECKPOINT_UNREADABLE", f"{checkpoint.name}:{kind}:{read_error}"))
            continue
        frozen[kind] = payload
    if "manifest" in frozen and "manifest" in loaded:
        current_ids = {row["requirement_id"] for row in loaded["manifest"]["requirements"]}
        for row in frozen["manifest"]["requirements"]:
            if row["requirement_id"] not in current_ids:
                errors.append(_error("ERROR_FROZEN_REQUIREMENT_DROPPED", f"{checkpoint.name}:{row['requirement_id']}"))
    if "contradictions" in frozen and "contradictions" in loaded:
        current = {row["contradiction_id"]: row for row in loaded["contradictions"]["contradictions"]}
        for row in frozen["contradictions"]["contradictions"]:
            now = current.get(row["contradiction_id"])
            if now is None:
                errors.append(_error("ERROR_FROZEN_CONTRADICTION_DROPPED", f"{checkpoint.name}:{row['contradiction_id']}"))
            elif row["material"] and not now["material"]:
                errors.append(
                    _error("ERROR_MATERIALITY_REDEFINED", f"{row['contradiction_id']} was material at {checkpoint.name}")
                )
    if "blockers" in frozen and "blockers" in loaded:
        current_ids = {row["blocker_id"] for row in loaded["blockers"]["blockers"]}
        for row in frozen["blockers"]["blockers"]:
            if row["blocker_id"] not in current_ids:
                errors.append(_error("ERROR_FROZEN_BLOCKER_DROPPED", f"{checkpoint.name}:{row['blocker_id']}"))
    if "scope_deltas" in frozen and "scope_deltas" in loaded:
        current_ids = {row["delta_id"] for row in loaded["scope_deltas"]["deltas"]}
        for row in frozen["scope_deltas"]["deltas"]:
            if row["delta_id"] not in current_ids:
                errors.append(_error("ERROR_FROZEN_SCOPE_DELTA_DROPPED", f"{checkpoint.name}:{row['delta_id']}"))
    return errors


def compute_denominator(acceptance: dict[str, Any]) -> dict[str, Any]:
    counts = Counter(row["state"] for row in acceptance["rows"])
    by_scope = Counter(row["scope_state"] for row in acceptance["rows"])
    total = len(acceptance["rows"])
    state_sum = sum(counts.get(state, 0) for state in REQUIREMENT_STATES)
    return {
        "total": total,
        "by_state": {state: counts.get(state, 0) for state in REQUIREMENT_STATES},
        "by_scope": {scope: by_scope.get(scope, 0) for scope in ("IN_SCOPE", "NOT_APPLICABLE", "EXTERNALLY_BLOCKED")},
        "arithmetic_closes": state_sum == total
        and sum(by_scope.values()) == total
        and by_scope.get("IN_SCOPE", 0) + by_scope.get("NOT_APPLICABLE", 0) + by_scope.get("EXTERNALLY_BLOCKED", 0)
        == total,
    }


def validate_release_scope(
    root: Path,
    *,
    release_dir: Path | None = None,
    frozen_checkpoint: Path | None = None,
    compare_frozen: bool = True,
) -> dict[str, Any]:
    loaded, errors = load_release_scope(root, release_dir)
    warnings: list[dict[str, str]] = []
    if set(loaded) != set(LEDGER_FILES):
        return {"status": "RELEASE_SCOPE_INVALID", "errors": errors, "warnings": warnings, "denominator": None}

    manifest = loaded["manifest"]
    acceptance = loaded["acceptance"]
    dag = loaded["dag"]
    blockers = {row["blocker_id"]: row for row in loaded["blockers"]["blockers"]}
    deltas = {row["delta_id"]: row for row in loaded["scope_deltas"]["deltas"]}

    # KERNEL-003: re-enumerate every bound specification from its bytes.
    enumerated: dict[str, dict[str, str]] = {}
    for spec in manifest["bound_specifications"]:
        found, spec_errors = enumerate_bound_requirements(root, spec)
        errors.extend(spec_errors)
        if not found and not spec_errors:
            errors.append(_error("ERROR_SPEC_ENUMERATED_ZERO", spec["spec_id"]))
        for row in found:
            enumerated[row["requirement_id"]] = row
    for missing in manifest["missing_specifications"]:
        if missing["blocker_id"] not in blockers:
            errors.append(_error("ERROR_UNKNOWN_BLOCKER", f"missing_spec:{missing['name']}:{missing['blocker_id']}"))

    # KERNEL-002: every requirement classified exactly once.
    requirement_counts = Counter(row["requirement_id"] for row in manifest["requirements"])
    for requirement_id, count in sorted(requirement_counts.items()):
        if count > 1:
            errors.append(_error("ERROR_DUPLICATE_REQUIREMENT", f"{requirement_id} x{count}"))
    requirements = {row["requirement_id"]: row for row in manifest["requirements"]}
    for requirement_id in sorted(set(enumerated) - set(requirements)):
        errors.append(_error("ERROR_SILENT_SCOPE_LOSS", requirement_id))

    # KERNEL-019: scope deltas enter the denominator by classification.
    for delta_id, delta in sorted(deltas.items()):
        enters = delta["classification"] in DENOMINATOR_ENTERING_DELTAS or delta["adopted"]
        if delta["classification"] == "ENHANCEMENT" and delta["adopted"] and not delta.get("adoption_authority"):
            errors.append(_error("ERROR_ENHANCEMENT_ADOPTION_UNAUTHORIZED", delta_id))
        if enters and delta_id not in requirements:
            errors.append(_error("ERROR_SCOPE_DELTA_NOT_IN_DENOMINATOR", delta_id))
        if not enters and delta_id in requirements:
            errors.append(_error("ERROR_ENHANCEMENT_IN_DENOMINATOR", delta_id))
        for parent in delta["parent_requirement_ids"]:
            if parent not in requirements:
                errors.append(_error("ERROR_SCOPE_DELTA_UNKNOWN_PARENT", f"{delta_id}:{parent}"))
    for requirement_id, row in sorted(requirements.items()):
        if requirement_id in enumerated:
            if row["source"] != enumerated[requirement_id]["source"]:
                errors.append(_error("ERROR_REQUIREMENT_SOURCE_DRIFT", requirement_id))
            continue
        if requirement_id in deltas and row["source"] == "SCOPE_DELTA":
            continue
        errors.append(_error("ERROR_UNBOUND_REQUIREMENT", requirement_id))

    for requirement_id, row in sorted(requirements.items()):
        scope = row["scope_state"]
        if scope == "NOT_APPLICABLE" and (not row.get("justification") or not row.get("justification_evidence")):
            errors.append(_error("ERROR_NOT_APPLICABLE_UNJUSTIFIED", requirement_id))
        if scope == "EXTERNALLY_BLOCKED":
            blocker_id = row.get("blocker_id")
            if not blocker_id:
                errors.append(_error("ERROR_EXTERNALLY_BLOCKED_WITHOUT_BLOCKER", requirement_id))
            elif blocker_id not in blockers:
                errors.append(_error("ERROR_UNKNOWN_BLOCKER", f"{requirement_id}:{blocker_id}"))

    # MATRIX-002: one ledger row per requirement, consistent with the manifest.
    dag_nodes = {row["node_id"]: row for row in dag["nodes"]}
    ledger_counts = Counter(row["requirement_id"] for row in acceptance["rows"])
    for requirement_id, count in sorted(ledger_counts.items()):
        if count > 1:
            errors.append(_error("ERROR_DUPLICATE_LEDGER_ROW", f"{requirement_id} x{count}"))
    ledger = {row["requirement_id"]: row for row in acceptance["rows"]}
    for requirement_id in sorted(set(requirements) - set(ledger)):
        errors.append(_error("ERROR_LEDGER_ROW_MISSING", requirement_id))
    for requirement_id in sorted(set(ledger) - set(requirements)):
        errors.append(_error("ERROR_LEDGER_ROW_UNBOUND", requirement_id))
    for requirement_id, row in sorted(ledger.items()):
        manifest_row = requirements.get(requirement_id)
        if manifest_row is None:
            continue
        if row["requirement_text"] != manifest_row["title"] or row["scope_state"] != manifest_row["scope_state"]:
            errors.append(_error("ERROR_LEDGER_DRIFT", requirement_id))
        state = row["state"]
        scope = row["scope_state"]
        if (scope == "NOT_APPLICABLE") != (state == "NOT_APPLICABLE"):
            errors.append(_error("ERROR_STATE_SCOPE_MISMATCH", f"{requirement_id}:{scope}:{state}"))
        if scope == "EXTERNALLY_BLOCKED" and state != "BLOCKED":
            errors.append(_error("ERROR_STATE_SCOPE_MISMATCH", f"{requirement_id}:{scope}:{state}"))
        if state == "BLOCKED":
            blocker_id = row.get("blocker_id")
            if not blocker_id:
                errors.append(_error("ERROR_BLOCKED_WITHOUT_BLOCKER", requirement_id))
            elif blocker_id not in blockers:
                errors.append(_error("ERROR_UNKNOWN_BLOCKER", f"{requirement_id}:{blocker_id}"))
            elif blockers[blocker_id]["status"] != "ACTIVE":
                errors.append(_error("ERROR_BLOCKER_NOT_ACTIVE", f"{requirement_id}:{blocker_id}"))
        if state == "PASS":
            # MATRIX-001: a PASS needs gates and evidence; script success alone is not PASS.
            if not row["positive_gate"] or not row["negative_gate"] or not row["evidence_refs"]:
                errors.append(_error("ERROR_PASS_WITHOUT_EVIDENCE", requirement_id))
        if state in {"PASS", "PROVISIONAL"}:
            refs = [*row["evidence_refs"], row["positive_gate"], row["negative_gate"]]
            for ref in sorted({ref for ref in refs if ref}):
                errors.extend(_evidence_ref_errors(root, ref))
        for dependency in row["dependencies"]:
            if dependency not in requirements:
                errors.append(_error("ERROR_UNKNOWN_DEPENDENCY", f"{requirement_id}:{dependency}"))
        if row.get("dag_node") and row["dag_node"] not in dag_nodes:
            errors.append(_error("ERROR_DAG_NODE_REFERENCE", f"{requirement_id}:{row['dag_node']}"))

    # KERNEL-006: the DAG is acyclic, bound to requirements, and blockers are real.
    for node_id, node in sorted(dag_nodes.items()):
        for dependency in node["dependencies"]:
            if dependency not in dag_nodes:
                errors.append(_error("ERROR_DAG_UNKNOWN_DEPENDENCY", f"{node_id}:{dependency}"))
        for requirement_id in node["requirement_ids"]:
            if requirement_id not in requirements:
                errors.append(_error("ERROR_DAG_UNKNOWN_REQUIREMENT", f"{node_id}:{requirement_id}"))
        if node["state"] == "BLOCKED":
            if not node["blocker"]:
                errors.append(_error("ERROR_DAG_BLOCKED_WITHOUT_BLOCKER", node_id))
            elif node["blocker"] not in blockers:
                errors.append(_error("ERROR_UNKNOWN_BLOCKER", f"{node_id}:{node['blocker']}"))
        if node["state"] == "PASS":
            for dependency in node["dependencies"]:
                if dag_nodes.get(dependency, {}).get("state") not in {"PASS", "SUPERSEDED"}:
                    warnings.append(_error("WARN_DAG_PASS_ON_UNPASSED_DEPENDENCY", f"{node_id}:{dependency}"))
    cycle = _dag_cycle(dag_nodes)
    if cycle:
        errors.append(_error("ERROR_DAG_CYCLE", " -> ".join(cycle)))
    for blocker_id, blocker in sorted(blockers.items()):
        if blocker["blocked_node"] not in dag_nodes:
            errors.append(_error("ERROR_BLOCKER_UNKNOWN_NODE", f"{blocker_id}:{blocker['blocked_node']}"))
        if blocker["status"] == "RESOLVED" and not blocker.get("resolution"):
            errors.append(_error("ERROR_BLOCKER_RESOLUTION_MISSING", blocker_id))

    # KERNEL-014: adjudicated contradictions must say how.
    for row in loaded["contradictions"]["contradictions"]:
        if row["state"] in {"ADJUDICATED", "SUPERSEDED"} and not row["adjudication"].strip():
            errors.append(_error("ERROR_CONTRADICTION_ADJUDICATION_MISSING", row["contradiction_id"]))
        for requirement_id in row.get("related_requirement_ids", []):
            if requirement_id not in requirements:
                errors.append(_error("ERROR_CONTRADICTION_UNKNOWN_REQUIREMENT", f"{row['contradiction_id']}:{requirement_id}"))

    checkpoint = frozen_checkpoint if frozen_checkpoint is not None else (latest_frozen_checkpoint(root) if compare_frozen else None)
    if checkpoint is not None:
        errors.extend(_frozen_comparison_errors(root, loaded, checkpoint))

    denominator = compute_denominator(acceptance)
    if not denominator["arithmetic_closes"]:
        errors.append(_error("ERROR_DENOMINATOR_ARITHMETIC", json.dumps(denominator["by_state"], sort_keys=True)))

    return {
        "status": "RELEASE_SCOPE_VALID" if not errors else "RELEASE_SCOPE_INVALID",
        "errors": errors,
        "warnings": warnings,
        "denominator": denominator,
        "enumerated_requirement_count": len(enumerated),
        "frozen_checkpoint": checkpoint.name if checkpoint is not None else None,
    }


def compute_release_status(root: Path, *, release_dir: Path | None = None, compare_frozen: bool = True) -> dict[str, Any]:
    """Apply the TERMINAL CERTIFICATION RULE; anything short of every condition stays PROVISIONAL."""
    validation = validate_release_scope(root, release_dir=release_dir, compare_frozen=compare_frozen)
    if validation["status"] != "RELEASE_SCOPE_VALID":
        return {
            "status": STATUS_INVALID,
            "release_status_line": "MUCARO AI RELEASE STATUS = PROVISIONAL (release scope invalid)",
            "validation": validation,
            "conditions": {},
            "residue": {},
        }
    loaded, _ = load_release_scope(root, release_dir)
    manifest = loaded["manifest"]
    rows = loaded["acceptance"]["rows"]
    in_scope = [row for row in rows if row["scope_state"] == "IN_SCOPE"]
    residue = {
        state.lower(): sorted(row["requirement_id"] for row in in_scope if row["state"] == state)
        for state in ("FAIL", "OPEN", "PROVISIONAL", "BLOCKED")
    }
    residue["externally_blocked"] = sorted(
        row["requirement_id"] for row in rows if row["scope_state"] == "EXTERNALLY_BLOCKED"
    )
    residue["unresolved_material_contradictions"] = sorted(
        row["contradiction_id"]
        for row in loaded["contradictions"]["contradictions"]
        if row["material"] and row["state"] == "UNRESOLVED"
    )
    residue["active_blockers"] = sorted(
        row["blocker_id"] for row in loaded["blockers"]["blockers"] if row["status"] == "ACTIVE"
    )
    residue["open_specification_bindings"] = sorted(
        [row["name"] for row in manifest["missing_specifications"]]
        + [row["spec_id"] for row in manifest["bound_specifications"] if row["binding"] != "BOUND"]
    )
    conditions = {
        "defined_release_scope": True,
        "bound_specification": not residue["open_specification_bindings"],
        "complete_requirement_denominator": validation["denominator"]["arithmetic_closes"],
        "zero_in_scope_fail": not residue["fail"],
        "zero_in_scope_open": not residue["open"],
        "zero_in_scope_provisional": not residue["provisional"],
        "zero_required_blocked_gates": not residue["blocked"] and not residue["externally_blocked"],
        "zero_material_unresolved_contradictions": not residue["unresolved_material_contradictions"],
        "zero_active_blockers": not residue["active_blockers"],
    }
    certified = all(conditions.values())
    return {
        "status": STATUS_CERTIFIED if certified else STATUS_PROVISIONAL,
        "release_status_line": "MUCARO AI RELEASE-GRADE CERTIFIED" if certified else "MUCARO AI RELEASE STATUS = PROVISIONAL",
        "validation": validation,
        "denominator": validation["denominator"],
        "conditions": conditions,
        "residue": residue,
    }


def _run(root: Path, args: list[str]) -> tuple[bool, str]:
    try:
        proc = subprocess.run(args, cwd=root, text=True, capture_output=True, check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return proc.returncode == 0, proc.stdout.rstrip()


def capture_state_snapshot(
    root: Path,
    *,
    tests: dict[str, Any] | None = None,
    build: dict[str, Any] | None = None,
    connected_resources: list[str] | None = None,
    known_boundaries: list[str] | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """KERNEL-004 snapshot. Records credential presence only, never values."""
    env = os.environ if environ is None else environ
    head_ok, head = _run(root, ["git", "rev-parse", "HEAD"])
    branch_ok, branch = _run(root, ["git", "rev-parse", "--abbrev-ref", "HEAD"])
    status_ok, porcelain = _run(root, ["git", "status", "--porcelain"])
    node_version = None
    if shutil.which("node"):
        node_ok, node_out = _run(root, ["node", "--version"])
        node_version = node_out if node_ok else None
    locks = []
    for relative, kind in (
        ("pyproject.toml", "top_level_pins_transitive_unpinned"),
        ("backend/requirements.txt", "top_level_pins_transitive_unpinned"),
        ("frontend/package-lock.json", "full_transitive_lock"),
    ):
        path = root / relative
        locks.append({"path": relative, "sha256": sha256_file(path) if path.is_file() else None, "lock_kind": kind})
    from .release_receipts import list_release_receipts

    receipts = [
        {key: row.get(key) for key in ("receipt_id", "receipt_type", "status", "head_sha", "valid")}
        for row in list_release_receipts(root)
    ]
    return {
        "schema_version": "mucaro-release-state-snapshot/1.0",
        "captured_utc": _utc_now(),
        "repository": {
            "path": str(root),
            "head_sha": head if head_ok else None,
            "branch": branch if branch_ok else None,
            "dirty_paths": [line[3:] for line in porcelain.splitlines()] if status_ok and porcelain else [],
        },
        "runtime": {
            "python": sys.version.split()[0],
            "python_executable_kind": "virtualenv" if sys.prefix != sys.base_prefix else "system",
            "node": node_version,
            "os": platform.system(),
            "os_release": platform.release(),
            "architecture": platform.machine(),
        },
        "dependency_locks": locks,
        "tests": tests or {"recorded": False},
        "build": build or {"frontend_dist_present": (root / "frontend" / "dist" / "index.html").is_file()},
        "credentials": [
            {"name": name, "present": bool(env.get(name)), "purpose": purpose} for name, purpose in CREDENTIAL_NAMES
        ],
        "receipts": receipts,
        "connected_resources": connected_resources or [],
        "known_boundaries": known_boundaries or [],
    }


def write_state_snapshot(root: Path, snapshot: dict[str, Any], output: Path | None = None) -> Path:
    schema, _ = _read_json(root / SCHEMA_DIR / SNAPSHOT_FILE[1])
    jsonschema.Draft202012Validator(schema).validate(snapshot)
    target = output or (root / RELEASE_DIR / SNAPSHOT_FILE[0])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def freeze_release_scope(root: Path, label: str) -> dict[str, Any]:
    """Write an immutable checkpoint used to detect silent scope loss later."""
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z(-[a-z0-9-]+)?", label):
        raise ValueError("freeze label must look like 20260924T200000Z or 20260924T200000Z-name")
    validation = validate_release_scope(root)
    if validation["status"] != "RELEASE_SCOPE_VALID":
        raise ValueError("cannot freeze an invalid release scope")
    target = root / FROZEN_DIR / label
    if target.exists():
        raise ValueError(f"frozen checkpoint already exists: {label}")
    target.mkdir(parents=True)
    hashes = {}
    for kind in FROZEN_KINDS:
        filename = LEDGER_FILES[kind][0]
        source = root / RELEASE_DIR / filename
        shutil.copyfile(source, target / filename)
        hashes[filename] = sha256_file(target / filename)
    index = {"label": label, "frozen_utc": _utc_now(), "files": hashes, "denominator": validation["denominator"]}
    (target / "checkpoint.json").write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return index
