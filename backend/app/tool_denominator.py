from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .runtime_identity import current_git_head
from .tools import TOOL_REGISTRY, ToolDefinition

SCHEMA = "mucaro-flagpole-tool-denominator/1.0"
REPORT_SCHEMA = "mucaro-flagpole-tool-denominator-report/1.0"
DECLARED_PATH = Path("_architecture") / "mucaro_flagpole_tool_denominator_v1.json"
CAPABILITY_STATES = {"PRESENT", "ABSENT"}
DOMAINS = {f"V{index:02d}" for index in range(24)}


def _stable_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _handler_identity(tool: ToolDefinition) -> str:
    return f"{tool.handler.__module__}:{tool.handler.__qualname__}"


def registry_sha256(registry: Mapping[str, ToolDefinition] = TOOL_REGISTRY) -> str:
    return _stable_hash(
        [{**tool.as_dict(), "handler": _handler_identity(tool)} for _, tool in sorted(registry.items())]
    )


def load_declared(repo: Path) -> dict[str, Any]:
    return json.loads((repo / DECLARED_PATH).read_text(encoding="utf-8"))


def validate_tool_denominator(
    declared: dict[str, Any],
    registry: Mapping[str, ToolDefinition] = TOOL_REGISTRY,
) -> list[str]:
    errors: list[str] = []
    if declared.get("schema") != SCHEMA:
        errors.append("SCHEMA_MISMATCH")
    rows = declared.get("required_capabilities")
    supporting = declared.get("supporting_tools")
    if not isinstance(rows, list) or not isinstance(supporting, list):
        return [*errors, "DENOMINATOR_SHAPE"]
    ids = [row.get("id") for row in rows if isinstance(row, dict)]
    if len(ids) != len(set(ids)):
        errors.append("DUPLICATE_CAPABILITY_ID")
    required_tools: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("satisfied_by"), list):
            errors.append("DENOMINATOR_SHAPE")
            continue
        row_id = row.get("id")
        state = row.get("state")
        tools = row["satisfied_by"]
        if state not in CAPABILITY_STATES:
            errors.append(f"UNKNOWN_STATE:{row_id}")
        if row.get("owner_domain") not in DOMAINS:
            errors.append(f"UNKNOWN_DOMAIN:{row_id}")
        if state == "PRESENT" and not tools:
            errors.append(f"PRESENT_WITHOUT_TOOL:{row_id}")
        if state == "ABSENT" and tools:
            errors.append(f"ABSENT_WITH_TOOL:{row_id}")
        required_tools.update(tools)
    supporting_set = set(supporting)
    registered = set(registry)
    for name in sorted({name for name in supporting if supporting.count(name) > 1} | (required_tools & supporting_set)):
        errors.append(f"DOUBLE_CLASSIFIED_TOOL:{name}")
    for name in sorted((required_tools | supporting_set) - registered):
        errors.append(f"UNKNOWN_TOOL:{name}")
    # Forcing function: a newly registered tool fails validation until it is classified here.
    for name in sorted(registered - required_tools - supporting_set):
        errors.append(f"UNCLASSIFIED_TOOL:{name}")
    for name, tool in sorted(registry.items()):
        if not tool.requires_approval:
            errors.append(f"MUTATING_TOOL_WITHOUT_APPROVAL:{name}" if tool.mutates_state else f"APPROVAL_NOT_REQUIRED:{name}")
    return errors


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _strings(node: ast.expr) -> set[str]:
    return {node.value} if isinstance(node, ast.Constant) and isinstance(node.value, str) else set()


def _loop_values(func: ast.AST, name: str) -> set[str]:
    """Literal values a for-loop (or comprehension) inside the test binds to ``name``."""
    values: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            continue
        if not isinstance(node.iter, (ast.Tuple, ast.List, ast.Set)):
            continue
        if isinstance(node.target, ast.Name) and node.target.id == name:
            for item in node.iter.elts:
                values |= _strings(item)
        elif isinstance(node.target, (ast.Tuple, ast.List)):
            for index, part in enumerate(node.target.elts):
                if isinstance(part, ast.Name) and part.id == name:
                    for row in node.iter.elts:
                        if isinstance(row, (ast.Tuple, ast.List)) and index < len(row.elts):
                            values |= _strings(row.elts[index])
    return values


def _parametrize_values(func: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> set[str]:
    """Literal values ``@pytest.mark.parametrize`` passes to the test argument ``name``."""
    values: set[str] = set()
    for decorator in func.decorator_list:
        if not (isinstance(decorator, ast.Call) and _call_name(decorator.func) == "parametrize" and len(decorator.args) >= 2):
            continue
        spec, rows = decorator.args[0], decorator.args[1]
        if isinstance(spec, ast.Constant) and isinstance(spec.value, str):
            names = [part.strip() for part in spec.value.split(",")]
        elif isinstance(spec, (ast.Tuple, ast.List)):
            names = [part.value for part in spec.elts if isinstance(part, ast.Constant) and isinstance(part.value, str)]
        else:
            continue
        if name not in names or not isinstance(rows, (ast.Tuple, ast.List)):
            continue
        index = names.index(name)
        for row in rows.elts:
            if len(names) == 1:
                values |= _strings(row)
            elif isinstance(row, (ast.Tuple, ast.List)) and index < len(row.elts):
                values |= _strings(row.elts[index])
    return values


def _requested_tools(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Tools this test passes to ``request_tool_call``, read from each call's tool-name argument."""
    requested: set[str] = set()
    for node in ast.walk(func):
        if not (isinstance(node, ast.Call) and _call_name(node.func) == "request_tool_call"):
            continue
        argument = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg == "tool_name"), None)
        if argument is None:
            continue
        requested |= _strings(argument)
        if isinstance(argument, ast.Name):
            requested |= _loop_values(func, argument.id) | _parametrize_values(func, argument.id)
    return requested


def runtime_evidence(repo: Path, tool_names: list[str]) -> dict[str, list[str]]:
    """Map each tool to test functions that request it through the governed runtime.

    A test counts for a tool only when one of its ``request_tool_call`` calls names that tool:
    a string literal, or a variable bound by a literal for-loop or ``pytest.mark.parametrize``.
    Other string constants in the test do not count. This shows the tool crosses the approval
    boundary, not that its output is correct.
    """
    evidence: dict[str, list[str]] = {name: [] for name in tool_names}
    tests_dir = repo / "backend" / "tests"
    for path in sorted(tests_dir.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test"):
                continue
            requested = _requested_tools(node)
            for name in tool_names:
                if name in requested:
                    evidence[name].append(f"{path.relative_to(repo).as_posix()}::{node.name}")
    return evidence


def _source_sha(repo: Path) -> str | None:
    # Read .git metadata directly (runtime_identity); no host Git process.
    return current_git_head(repo)


def build_tool_denominator(
    repo: Path,
    declared: dict[str, Any] | None = None,
    registry: Mapping[str, ToolDefinition] = TOOL_REGISTRY,
) -> dict[str, Any]:
    repo = repo.resolve()
    declared = load_declared(repo) if declared is None else declared
    errors = validate_tool_denominator(declared, registry)
    rows = [dict(row) for row in declared.get("required_capabilities") or [] if isinstance(row, dict)]
    required_tools = {name for row in rows for name in row.get("satisfied_by") or []}
    supporting = set(declared.get("supporting_tools") or [])
    names = sorted(registry)
    evidence = runtime_evidence(repo, names)
    tools = []
    for name in names:
        tool = registry[name]
        classification = "REQUIRED" if name in required_tools else "SUPPORTING" if name in supporting else "UNCLASSIFIED"
        tools.append(
            {
                "name": name,
                "mutates_state": tool.mutates_state,
                "requires_approval": tool.requires_approval,
                "handler": _handler_identity(tool),
                "classification": classification,
                "runtime_evidence": evidence[name],
                "evidence_state": "EXERCISED" if evidence[name] else "UNEXERCISED",
            }
        )
    present = sum(1 for row in rows if row.get("state") == "PRESENT")
    absent = sum(1 for row in rows if row.get("state") == "ABSENT")
    unexercised = sum(1 for row in tools if row["evidence_state"] == "UNEXERCISED")
    if errors:
        certification_state = "FAIL"
    elif absent or unexercised:
        certification_state = "OPEN"
    else:
        # Runtime-request evidence is not execution certification.
        certification_state = "PROVISIONAL"
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "declared_schema": declared.get("schema"),
        "domain": "V05",
        "source_sha": _source_sha(repo),
        "registry_sha256": registry_sha256(registry),
        "declared_sha256": _stable_hash(declared),
        "status": "DENOMINATOR_CLOSED" if not errors else "DENOMINATOR_INVALID",
        "certification_state": certification_state,
        "errors": errors,
        "counts": {
            "required_capabilities": len(rows),
            "present": present,
            "absent": absent,
            "registered_tools": len(tools),
            "classified_tools": sum(1 for row in tools if row["classification"] != "UNCLASSIFIED"),
            "mutating_tools": sum(1 for row in tools if row["mutates_state"]),
            "read_only_tools": sum(1 for row in tools if not row["mutates_state"]),
            "exercised_tools": len(tools) - unexercised,
            "unexercised_tools": unexercised,
        },
        "required_capabilities": rows,
        "open_capabilities": [row.get("capability") for row in rows if row.get("state") == "ABSENT"],
        "tools": tools,
    }
    report["report_sha256"] = _stable_hash(report)
    return report
