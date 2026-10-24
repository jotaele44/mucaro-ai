from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .coding_certification import CodingCertificationError, load_coding_regression_manifest
from .coding_workspace import CodingWorkspaceError, inspect_repository
from .config import Settings, settings
from .container_runtime import ContainerRuntimeError, resolve_container_engine, validate_pinned_image

CODING_READINESS_SCHEMA_VERSION = "mucaro-coding-readiness/1.0"
LOCAL_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")


class CodingReadinessError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _identity_row(name: str, value: Any, valid: bool, *, required: bool = True) -> dict[str, Any]:
    return {
        "name": name,
        "value": value,
        "required": required,
        "valid": valid,
        "state": "PASS" if valid else "BLOCKED",
    }


def build_coding_readiness(
    repo_root: str | Path,
    *,
    image: str | None = None,
    engine: str | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    """Build an exact-head, non-executing readiness receipt for coding certification.

    This deliberately stops before container execution.  A READY result means that
    the required identities and local runtime prerequisites are present; it is not a
    test, lint, type, build, or security result.
    """

    try:
        repository = inspect_repository(repo_root, cfg=cfg)
    except CodingWorkspaceError as exc:
        raise CodingReadinessError(exc.status, exc.detail) from exc

    try:
        manifest = load_coding_regression_manifest(cfg)
    except CodingCertificationError as exc:
        raise CodingReadinessError(exc.status, exc.detail) from exc

    engine_value: str | None = None
    engine_error: dict[str, str] | None = None
    try:
        engine_value = resolve_container_engine(engine)
    except ContainerRuntimeError as exc:
        engine_error = {"status": exc.status, "detail": exc.detail}

    image_valid = False
    image_error: dict[str, str] | None = None
    if image is not None:
        try:
            if LOCAL_IMAGE_ID.fullmatch(image):
                image_valid = True
            else:
                validate_pinned_image(image)
                image_valid = True
        except ContainerRuntimeError as exc:
            image_error = {"status": exc.status, "detail": exc.detail}
    else:
        image_error = {
            "status": "ERROR_CODING_IMAGE_REQUIRED",
            "detail": "Provide an immutable name@sha256:<digest> image or an exact local sha256 image ID.",
        }

    source_head = repository.get("head_sha")
    identity = [
        _identity_row("SOURCE_HEAD", source_head, isinstance(source_head, str) and bool(SHA40.fullmatch(source_head))),
        _identity_row("MANIFEST_SHA256", manifest.get("sha256"), bool(manifest.get("sha256"))),
        _identity_row("CONTAINER_ENGINE", engine_value, engine_value is not None),
        _identity_row("CODING_IMAGE", image, image_valid),
    ]
    blockers = [row["name"] for row in identity if row["required"] and not row["valid"]]
    requirements = manifest["payload"]["requirements"]
    state = "CODING_READINESS_READY" if not blockers else "CODING_READINESS_BLOCKED"
    payload: dict[str, Any] = {
        "schema_version": CODING_READINESS_SCHEMA_VERSION,
        "status": state,
        "execution_status": "NOT_EXECUTED",
        "repo_root": repository.get("repo_root"),
        "source": {
            "head_sha": source_head,
            "branch": repository.get("branch"),
            "dirty_status": repository.get("dirty_status"),
            "dirty_paths": repository.get("dirty_paths", []),
            "dirty_path_count": repository.get("dirty_path_count"),
        },
        "manifest": {
            "path": manifest["path"],
            "sha256": manifest["sha256"],
            "schema_version": manifest["payload"].get("schema_version"),
            "requirement_count": len(requirements),
            "requirement_ids": [row["id"] for row in requirements],
        },
        "runtime": {
            "engine": engine_value,
            "engine_error": engine_error,
            "image": image,
            "image_error": image_error,
            "network_allowed": False,
            "implicit_image_pull_allowed": False,
        },
        "identity": identity,
        "blockers": blockers,
        "invariants": {
            "identity_accounting_closed": len(identity) == 4,
            "manifest_requirement_accounting_closed": len(requirements) == len({row["id"] for row in requirements}),
            "execution_not_claimed": True,
            "network_disabled": True,
            "implicit_image_pull_disabled": True,
            "ready_requires_no_blockers": state == "CODING_READINESS_READY" or bool(blockers),
        },
        "limitations": [
            "readiness does not execute any test, lint, type, build, security, or benchmark command",
            "a READY receipt still requires the live suite to produce terminal execution receipts",
            "the exact image digest must be present locally before the coding runner uses --pull=never",
        ],
        "falsification_criteria": [
            "missing or malformed source HEAD blocks readiness",
            "missing container engine blocks readiness",
            "missing or mutable image identity blocks readiness",
            "readiness must never be translated into execution PASS",
        ],
    }
    payload["receipt_sha256"] = _hash(payload)
    return payload
