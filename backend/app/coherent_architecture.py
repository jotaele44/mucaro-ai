"""TGT-038 coherent architecture: check the declared matrix against the running code.

`_architecture/coherent_architecture_v1.json` says how each of the 12 TGT-038 categories reaches the common governed
architecture: one canonical surface, shared contract families and tool-registry bridges. This module checks every
claim against the code: references exist, surfaces resolve, contract families and tools are real, and every registered
tool belongs to some category, so a new tool cannot bypass the matrix unnoticed.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from .contracts import CONTRACT_FAMILIES
from .tools import TOOL_REGISTRY

MATRIX_PATH = Path("_architecture") / "coherent_architecture_v1.json"
SCHEMA_VERSION = "mucaro-coherent-architecture/1.0"
STATUS_OK = "COHERENT_ARCHITECTURE_OK"
STATUS_FAILED = "COHERENT_ARCHITECTURE_FAILED"
CATEGORIES = (
    "AI", "FILES", "OCR", "RETRIEVAL", "GIS", "WEB", "CODE", "DATA", "TOOLS", "MEMORY", "EVIDENCE", "ARTIFACTS",
)
SINGLE_BRIDGE_CATEGORIES = ("GIS", "OCR")


def _surface_resolves(project_root: Path, surface: str) -> bool:
    """`path` or `path:symbol`, where symbol is a module attribute or a registered tool name."""
    path, _, symbol = surface.partition(":")
    if not (project_root / path).is_file():
        return False
    if not symbol:
        return True
    if symbol in TOOL_REGISTRY:
        return True
    if not path.endswith(".py"):
        return False
    module_name = path[: -len(".py")].replace("/", ".")
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return False
    return hasattr(module, symbol)


def validate_coherent_architecture(project_root: Path, matrix: dict[str, Any] | None = None) -> dict[str, Any]:
    project_root = project_root.resolve()
    if matrix is None:
        matrix = json.loads((project_root / MATRIX_PATH).read_text(encoding="utf-8"))
    rows = matrix.get("categories") or []
    names = [row.get("category") for row in rows]
    errors: list[str] = []
    if matrix.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version:{matrix.get('schema_version')}")
    if sorted(names) != sorted(CATEGORIES) or len(names) != len(set(names)):
        errors.append(f"categories:{names}")
    mapped: set[str] = set()
    for row in rows:
        category = row.get("category")
        surface = row.get("canonical_surface") or ""
        if not surface or not _surface_resolves(project_root, surface):
            errors.append(f"{category}:canonical_surface_unresolved:{surface}")
        for ref in [*(row.get("implementation_refs") or []), *(row.get("evidence_refs") or [])]:
            if not (project_root / ref).exists():
                errors.append(f"{category}:missing_ref:{ref}")
        if not row.get("evidence_refs"):
            errors.append(f"{category}:no_evidence_refs")
        contracts = row.get("shared_contracts") or []
        if not contracts:
            errors.append(f"{category}:no_shared_contracts")
        for family in contracts:
            if family not in CONTRACT_FAMILIES:
                errors.append(f"{category}:unknown_contract_family:{family}")
        bridges = row.get("tool_registry_bridges") or []
        for bridge in bridges:
            if bridge not in TOOL_REGISTRY:
                errors.append(f"{category}:unknown_tool:{bridge}")
        if len(bridges) != len(set(bridges)):
            errors.append(f"{category}:duplicate_bridge")
        if category in SINGLE_BRIDGE_CATEGORIES and len(bridges) != 1:
            errors.append(f"{category}:expected_exactly_one_bridge:{bridges}")
        mapped.update(bridges)
    unmapped = sorted(set(TOOL_REGISTRY) - mapped)
    for tool in unmapped:
        errors.append(f"unmapped_tool:{tool}")
    return {
        "status": STATUS_FAILED if errors else STATUS_OK,
        "category_count": len(rows),
        "registered_tool_count": len(TOOL_REGISTRY),
        "mapped_tool_count": len(mapped & set(TOOL_REGISTRY)),
        "unmapped_tools": unmapped,
        "errors": errors,
    }
