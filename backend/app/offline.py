"""Offline capability manifest (TGT-019).

Every user-facing capability is classified as AVAILABLE_LOCALLY,
UNAVAILABLE_OFFLINE, REQUIRES_REMOTE_SERVICE or LOCAL_DEPENDENCY_MISSING so the
UI can say what works without a network instead of hanging on a request that
cannot complete. A capability whose local package or binary is absent (for
example in a packaged build that does not ship it) is LOCAL_DEPENDENCY_MISSING
even when it would also need the network.
"""

from __future__ import annotations

import importlib.util
from typing import Any

from .config import Settings, settings
from .local_tools import bundled_only, resolve_local_tool
from .model_router import list_model_providers
from .tools import TOOL_REGISTRY

AVAILABLE_LOCALLY = "AVAILABLE_LOCALLY"
UNAVAILABLE_OFFLINE = "UNAVAILABLE_OFFLINE"
REQUIRES_REMOTE_SERVICE = "REQUIRES_REMOTE_SERVICE"

# Independence actions describe what must happen before a release can claim that
# the capability needs no external setup work from the operator.
BUNDLE_LOCAL_COMPONENT = "BUNDLE_LOCAL_COMPONENT"
OPTIONAL_LOCAL_ENGINE = "OPTIONAL_LOCAL_ENGINE"
KEEP_EXPLICIT_NETWORK_GATE = "KEEP_EXPLICIT_NETWORK_GATE"
PLATFORM_ADAPTER_REQUIRED = "PLATFORM_ADAPTER_REQUIRED"

# Capabilities that are not tools:
# (id, description, local dependency, dependency kind, independence actions).
_CORE_CAPABILITIES: tuple[tuple[str, str, str | None, str | None, tuple[str, ...]], ...] = (
    ("ingest.files", "Ingest local files and folders (text, CSV, JSON, HTML, DOCX, email, PDF text layer, ZIP)", None, None, ()),
    ("retrieve.local", "Lexical, BM25, TF-IDF and local hash-embedding retrieval", None, None, ()),
    ("answer.cited", "Cited answers with the deterministic local synthesizer", None, None, ()),
    ("gis.vector_raster", "GIS vector/raster processing with bundled grids (no runtime downloads)", None, None, ()),
    ("ocr.image", "Image OCR through the local tesseract binary", "tesseract", "binary", (BUNDLE_LOCAL_COMPONENT,)),
    ("ocr.pdf_render", "Scanned-PDF page rendering for OCR", "pdftoppm", "binary", (BUNDLE_LOCAL_COMPONENT,)),
    ("memory", "Explicit local memory", None, None, ()),
    ("exports", "Answer-trace, citation and receipt exports", None, None, ()),
    (
        "execution.container",
        "Container-isolated execution for supported desktop development/research workflows",
        "docker,podman",
        "binary_any",
        (PLATFORM_ADAPTER_REQUIRED,),
    ),
)

# Local dependencies used by tools that are otherwise classified by network policy.
# Coding operations intentionally remain Git-backed, but the release target must
# bundle or replace Git so the host does not need developer tooling installed.
_TOOL_LOCAL_DEPENDENCIES: dict[str, tuple[str, str]] = {
    "coding.repo_status": ("binary", "git"),
    "coding.read_file": ("binary", "git"),
    "coding.diff": ("binary", "git"),
    "coding.patch_check": ("binary", "git"),
    "coding.patch_apply": ("binary", "git"),
    "web.render": ("python_module", "playwright"),
}

LOCAL_DEPENDENCY_MISSING = "LOCAL_DEPENDENCY_MISSING"
STATES = (AVAILABLE_LOCALLY, UNAVAILABLE_OFFLINE, REQUIRES_REMOTE_SERVICE, LOCAL_DEPENDENCY_MISSING)


def _module_present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _dependency_present(kind: str | None, dependency: str | None) -> bool:
    if dependency is None:
        return True
    if kind == "python_module":
        return _module_present(dependency)
    if kind == "binary":
        return resolve_local_tool(dependency) is not None
    if kind == "binary_any":
        return any(resolve_local_tool(name) is not None for name in dependency.split(","))
    raise ValueError(f"unsupported local dependency kind: {kind}")


def _row(
    capability: str,
    description: str,
    remote: bool,
    dependency: str | None,
    dependency_ok: bool,
    system_offline: bool,
    *,
    dependency_kind: str | None = None,
    independence_actions: tuple[str, ...] = (),
) -> dict[str, Any]:
    if not dependency_ok:
        state = LOCAL_DEPENDENCY_MISSING
    elif remote:
        state = UNAVAILABLE_OFFLINE if system_offline else REQUIRES_REMOTE_SERVICE
    else:
        state = AVAILABLE_LOCALLY
    return {
        "capability": capability,
        "description": description,
        "availability_class": REQUIRES_REMOTE_SERVICE if remote else AVAILABLE_LOCALLY,
        "state_now": state,
        "local_dependency": dependency,
        "local_dependency_kind": dependency_kind,
        "local_dependency_present": dependency_ok,
        "runtime_network_required": remote,
        "independence_actions": list(independence_actions),
        "operator_external_setup_required": bool(dependency and not dependency_ok and not bundled_only()),
        "release_packaging_residue": bool(dependency and not dependency_ok and independence_actions),
        "resolution_policy": "BUNDLED_ONLY" if bundled_only() else "BUNDLE_THEN_HOST_DEVELOPMENT_FALLBACK",
    }


def build_offline_manifest(*, system_offline: bool = False, cfg: Settings = settings) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for capability_id, description, dependency, dependency_kind, actions in _CORE_CAPABILITIES:
        present = _dependency_present(dependency_kind, dependency)
        rows.append(
            _row(
                capability_id,
                description,
                False,
                dependency,
                present,
                system_offline,
                dependency_kind=dependency_kind,
                independence_actions=actions,
            )
        )
    for tool in TOOL_REGISTRY.values():
        dependency_kind, dependency = _TOOL_LOCAL_DEPENDENCIES.get(tool.name, (None, None))
        present = _dependency_present(dependency_kind, dependency)
        actions: tuple[str, ...] = ()
        if dependency is not None:
            actions += (BUNDLE_LOCAL_COMPONENT,)
        if tool.network != "none":
            actions += (KEEP_EXPLICIT_NETWORK_GATE,)
        rows.append(
            _row(
                f"tool:{tool.name}",
                tool.description,
                tool.network != "none",
                dependency,
                present,
                system_offline,
                dependency_kind=dependency_kind,
                independence_actions=actions,
            )
        )
    for provider in list_model_providers(cfg):
        remote = provider.get("locality") == "remote"
        local_server = provider["name"] == "openai_compatible_local"
        if local_server:
            dependency, dependency_kind = "local model server", "local_service"
            present = provider["status"] == "MODEL_PROVIDER_READY"
            actions = (OPTIONAL_LOCAL_ENGINE,)
        else:
            dependency = provider.get("optional_dependency")
            dependency_kind = "python_module" if dependency else None
            present = provider.get("dependency_available") is not False
            actions = ((BUNDLE_LOCAL_COMPONENT,) if dependency else ()) + ((KEEP_EXPLICIT_NETWORK_GATE,) if remote else ())
        row = _row(
            f"model:{provider['name']}",
            provider["description"],
            remote,
            dependency,
            present,
            system_offline,
            dependency_kind=dependency_kind,
            independence_actions=actions,
        )
        row["provider_status"] = provider["status"]
        rows.append(row)
    counts = {state: sum(1 for row in rows if row["state_now"] == state) for state in STATES}
    independence = {
        "target": "ZERO_MANDATORY_EXTERNAL_RUNTIME_WORK_EXCEPT_EXPLICIT_NETWORK_OPERATIONS",
        "bundle_candidates": sorted(
            row["capability"] for row in rows if BUNDLE_LOCAL_COMPONENT in row["independence_actions"]
        ),
        "optional_local_engines": sorted(
            row["capability"] for row in rows if OPTIONAL_LOCAL_ENGINE in row["independence_actions"]
        ),
        "network_exceptions": sorted(
            row["capability"] for row in rows if KEEP_EXPLICIT_NETWORK_GATE in row["independence_actions"]
        ),
        "operator_external_setup_required_now": sorted(
            row["capability"] for row in rows if row["operator_external_setup_required"]
        ),
    }
    return {
        "status": "OFFLINE_MANIFEST_OK",
        "system_offline": system_offline,
        "capability_count": len(rows),
        "counts": counts,
        "arithmetic_closes": sum(counts.values()) == len(rows),
        "independence": independence,
        "capabilities": rows,
    }
