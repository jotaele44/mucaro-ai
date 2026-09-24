"""Offline capability manifest (TGT-019).

Every user-facing capability is classified as AVAILABLE_LOCALLY,
UNAVAILABLE_OFFLINE or REQUIRES_REMOTE_SERVICE so the UI can say what works
without a network instead of hanging on a request that cannot complete.
"""

from __future__ import annotations

import shutil
from typing import Any

from .model_router import list_model_providers
from .tools import TOOL_REGISTRY

AVAILABLE_LOCALLY = "AVAILABLE_LOCALLY"
UNAVAILABLE_OFFLINE = "UNAVAILABLE_OFFLINE"
REQUIRES_REMOTE_SERVICE = "REQUIRES_REMOTE_SERVICE"

# Capabilities that are not tools: (id, description, local dependency if any). All are local.
_CORE_CAPABILITIES: tuple[tuple[str, str, str | None], ...] = (
    ("ingest.files", "Ingest local files and folders (text, CSV, JSON, HTML, DOCX, email, PDF text layer, ZIP)", None),
    ("retrieve.local", "Lexical, BM25, TF-IDF and local hash-embedding retrieval", None),
    ("answer.cited", "Cited answers with the deterministic local synthesizer", None),
    ("gis.vector_raster", "GIS vector/raster processing with bundled grids (no runtime downloads)", None),
    ("ocr.image", "Image OCR through the local tesseract binary", "tesseract"),
    ("ocr.pdf_render", "Scanned-PDF page rendering for OCR", "pdftoppm"),
    ("memory", "Explicit local memory", None),
    ("exports", "Answer-trace, citation and receipt exports", None),
)


LOCAL_DEPENDENCY_MISSING = "LOCAL_DEPENDENCY_MISSING"
STATES = (AVAILABLE_LOCALLY, UNAVAILABLE_OFFLINE, REQUIRES_REMOTE_SERVICE, LOCAL_DEPENDENCY_MISSING)


def _row(capability: str, description: str, remote: bool, dependency: str | None, dependency_ok: bool, system_offline: bool) -> dict[str, Any]:
    if remote:
        state = UNAVAILABLE_OFFLINE if system_offline else REQUIRES_REMOTE_SERVICE
    else:
        state = AVAILABLE_LOCALLY if dependency_ok else LOCAL_DEPENDENCY_MISSING
    return {
        "capability": capability,
        "description": description,
        "availability_class": REQUIRES_REMOTE_SERVICE if remote else AVAILABLE_LOCALLY,
        "state_now": state,
        "local_dependency": dependency,
        "local_dependency_present": dependency_ok,
    }


def build_offline_manifest(*, system_offline: bool = False) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for capability_id, description, dependency in _CORE_CAPABILITIES:
        present = dependency is None or shutil.which(dependency) is not None
        rows.append(_row(capability_id, description, False, dependency, present, system_offline))
    for tool in TOOL_REGISTRY.values():
        rows.append(_row(f"tool:{tool.name}", tool.description, tool.network != "none", None, True, system_offline))
    for provider in list_model_providers():
        remote = provider.get("locality") == "remote"
        local_server = provider["name"] == "openai_compatible_local"
        row = _row(
            f"model:{provider['name']}",
            provider["description"],
            remote,
            "local model server" if local_server else None,
            provider["status"] == "MODEL_PROVIDER_READY" or remote,
            system_offline,
        )
        row["provider_status"] = provider["status"]
        rows.append(row)
    counts = {state: sum(1 for row in rows if row["state_now"] == state) for state in STATES}
    return {
        "status": "OFFLINE_MANIFEST_OK",
        "system_offline": system_offline,
        "capability_count": len(rows),
        "counts": counts,
        "arithmetic_closes": sum(counts.values()) == len(rows),
        "capabilities": rows,
    }
