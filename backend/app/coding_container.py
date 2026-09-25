from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .coding_workspace import CodingWorkspaceError, inspect_repository, resolve_repository
from .config import Settings, settings
from .permission_engine import PermissionEngineError, PermissionRequest, require
from .container_runtime import ContainerRuntimeError, resolve_container_engine, validate_pinned_image
from .distribution_policy import DistributionPolicyError, require_distribution_runtime_authorized

LOCAL_IMAGE_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

CODING_CONTAINER_SCHEMA_VERSION = "mucaro-coding-container/1.1"
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_OUTPUT_BYTES = 2 * 1024 * 1024
DEFAULT_MEMORY_MB = 2048
DEFAULT_CPUS = 2.0
DEFAULT_PIDS_LIMIT = 256
DEFAULT_WORKSPACE_MB = 1024
MAX_COMMAND_PARTS = 64
MAX_COMMAND_PART_BYTES = 4096
CONTAINER_UID_GID = "65532:65532"


class CodingContainerError(RuntimeError):
    def __init__(self, status: str, detail: str, *, receipt: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.receipt = receipt


@dataclass(frozen=True)
class CodingContainerPlan:
    repo_root: Path
    image: str
    command: tuple[str, ...]
    head_sha: str
    dirty_status: str
    dirty_paths_sha256: str
    timeout_seconds: int
    max_output_bytes: int
    memory_mb: int
    cpus: float
    pids_limit: int
    workspace_mb: int
    engine: str

    @property
    def mount_mode(self) -> str:
        return "read_only_source_ephemeral_workspace"

    @property
    def network_allowed(self) -> bool:
        return False

    @property
    def plan_sha256(self) -> str:
        payload = json.dumps(
            {
                "schema_version": CODING_CONTAINER_SCHEMA_VERSION,
                "repo_root": str(self.repo_root),
                "image": self.image,
                "command": list(self.command),
                "head_sha": self.head_sha,
                "dirty_status": self.dirty_status,
                "dirty_paths_sha256": self.dirty_paths_sha256,
                "mount_mode": self.mount_mode,
                "network_allowed": self.network_allowed,
                "timeout_seconds": self.timeout_seconds,
                "max_output_bytes": self.max_output_bytes,
                "memory_mb": self.memory_mb,
                "cpus": self.cpus,
                "pids_limit": self.pids_limit,
                "workspace_mb": self.workspace_mb,
                "engine": self.engine,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def _positive_int(value: int, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise CodingContainerError("ERROR_CODING_CONTAINER_LIMIT", f"{label} must be a positive integer")
    return value


def _positive_number(value: float, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or float(value) <= 0:
        raise CodingContainerError("ERROR_CODING_CONTAINER_LIMIT", f"{label} must be positive")
    return float(value)


def _validate_coding_image(image: str) -> str:
    """Accept registry digests or exact local Docker/Podman image IDs for coding only."""
    if isinstance(image, str) and LOCAL_IMAGE_ID_PATTERN.fullmatch(image):
        return image
    return validate_pinned_image(image)


def _validate_command(command: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(command, (list, tuple)) or not command or len(command) > MAX_COMMAND_PARTS:
        raise CodingContainerError("ERROR_CODING_CONTAINER_COMMAND", "command must be a non-empty bounded argv array")
    result: list[str] = []
    for part in command:
        if not isinstance(part, str) or not part or "\x00" in part or len(part.encode("utf-8")) > MAX_COMMAND_PART_BYTES:
            raise CodingContainerError("ERROR_CODING_CONTAINER_COMMAND", "command contains an invalid argv element")
        result.append(part)
    return tuple(result)


def _dirty_paths_digest(paths: list[str]) -> str:
    payload = json.dumps(sorted(paths), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_mount_source(repo_root: Path) -> None:
    value = str(repo_root)
    if any(ch in value for ch in (",", "\n", "\r", "\x00")):
        raise CodingContainerError(
            "ERROR_CODING_CONTAINER_MOUNT_PATH",
            "repository path contains characters unsafe for deterministic --mount serialization",
        )


def build_coding_container_plan(
    repo_root: str | Path,
    *,
    image: str,
    command: Sequence[str],
    cfg: Settings = settings,
    network_allowed: bool = False,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    memory_mb: int = DEFAULT_MEMORY_MB,
    cpus: float = DEFAULT_CPUS,
    pids_limit: int = DEFAULT_PIDS_LIMIT,
    workspace_mb: int = DEFAULT_WORKSPACE_MB,
    engine: str | None = None,
) -> CodingContainerPlan:
    try:
        require_distribution_runtime_authorized(
            distribution_channel=cfg.distribution_channel,
            runtime="CONTAINER",
            granted_capabilities={"runtime.container", "subprocess.execute"},
        )
    except DistributionPolicyError as exc:
        raise CodingContainerError(exc.status, exc.detail) from exc
    try:
        require(PermissionRequest(capability="subprocess.execute", subject="coding_container.plan", target=str(repo_root), origin="operator"), cfg=cfg)
    except PermissionEngineError as exc:
        raise CodingContainerError(exc.status, exc.detail) from exc
    try:
        context = resolve_repository(repo_root, cfg)
        state = inspect_repository(context.repo_root, cfg=cfg)
    except CodingWorkspaceError as exc:
        raise CodingContainerError(exc.status, exc.detail) from exc
    _validate_mount_source(context.repo_root)
    try:
        pinned = _validate_coding_image(image)
        engine_path = resolve_container_engine(engine)
    except ContainerRuntimeError as exc:
        raise CodingContainerError(exc.status, exc.detail) from exc
    if network_allowed:
        raise CodingContainerError(
            "ERROR_CODING_CONTAINER_NETWORK_POLICY",
            "coding container network access is disabled in this boundary",
        )
    dirty_paths = [str(path) for path in state.get("dirty_paths") or []]
    return CodingContainerPlan(
        repo_root=context.repo_root,
        image=pinned,
        command=_validate_command(command),
        head_sha=str(state["head_sha"]),
        dirty_status=str(state["dirty_status"]),
        dirty_paths_sha256=_dirty_paths_digest(dirty_paths),
        timeout_seconds=_positive_int(timeout_seconds, "timeout_seconds"),
        max_output_bytes=_positive_int(max_output_bytes, "max_output_bytes"),
        memory_mb=_positive_int(memory_mb, "memory_mb"),
        cpus=_positive_number(cpus, "cpus"),
        pids_limit=_positive_int(pids_limit, "pids_limit"),
        workspace_mb=_positive_int(workspace_mb, "workspace_mb"),
        engine=engine_path,
    )


def build_coding_container_command(plan: CodingContainerPlan) -> list[str]:
    _validate_mount_source(plan.repo_root)
    bootstrap = (
        "set -eu; "
        "cp -a /workspace/source/. /workspace/repo/; "
        "cd /workspace/repo; "
        "exec \"$@\""
    )
    return [
        plan.engine,
        "run",
        "--rm",
        "--interactive",
        "--pull=never",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--network=none",
        "--ipc=none",
        f"--user={CONTAINER_UID_GID}",
        "--workdir=/workspace/repo",
        f"--mount=type=bind,src={plan.repo_root},dst=/workspace/source,readonly",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=256m,uid=65532,gid=65532,mode=1777",
        f"--tmpfs=/workspace/repo:rw,nosuid,nodev,size={plan.workspace_mb}m,uid=65532,gid=65532,mode=0755",
        "--tmpfs=/workspace/out:rw,nosuid,nodev,size=256m,uid=65532,gid=65532,mode=0755",
        f"--memory={plan.memory_mb}m",
        f"--cpus={plan.cpus}",
        f"--pids-limit={plan.pids_limit}",
        "--env=HOME=/tmp/home",
        "--env=TMPDIR=/tmp",
        "--env=XDG_CACHE_HOME=/tmp/cache",
        "--env=PYTHONDONTWRITEBYTECODE=1",
        "--env=NPM_CONFIG_CACHE=/tmp/npm-cache",
        "--entrypoint=/bin/sh",
        plan.image,
        "-c",
        bootstrap,
        "mucaro-coding",
        *plan.command,
    ]


def validate_coding_container_plan(plan: CodingContainerPlan, *, cfg: Settings = settings) -> dict[str, object]:
    try:
        state = inspect_repository(plan.repo_root, cfg=cfg)
    except CodingWorkspaceError as exc:
        raise CodingContainerError(exc.status, exc.detail) from exc
    current_dirty_paths = [str(path) for path in state.get("dirty_paths") or []]
    current_dirty_digest = _dirty_paths_digest(current_dirty_paths)
    head_match = state.get("head_sha") == plan.head_sha
    dirty_match = state.get("dirty_status") == plan.dirty_status and current_dirty_digest == plan.dirty_paths_sha256
    return {
        "schema_version": CODING_CONTAINER_SCHEMA_VERSION,
        "status": "CODING_CONTAINER_PLAN_VALID" if head_match and dirty_match else "CODING_CONTAINER_PLAN_STALE",
        "plan_sha256": plan.plan_sha256,
        "head_match": head_match,
        "dirty_state_match": dirty_match,
        "planned_head_sha": plan.head_sha,
        "current_head_sha": state.get("head_sha"),
        "planned_dirty_status": plan.dirty_status,
        "current_dirty_status": state.get("dirty_status"),
        "source_mount_read_only": True,
        "workspace_ephemeral": True,
        "network_allowed": False,
        "command_argv": list(plan.command),
        "container_argv": build_coding_container_command(plan),
        "limitations": [
            "live execution requires an available Docker or Podman engine and the exact pinned coding image already present locally",
            "the container works on an ephemeral copy; build artifacts are not exported by this execution boundary",
            "the coding image contract must provide /bin/sh and cp",
        ],
        "falsification_criteria": [
            "reject unpinned image identity",
            "reject network-enabled plans",
            "treat HEAD or dirty-state drift as STALE before execution",
            "never expose the host repository as writable inside the container",
            "never pull an image implicitly at execution time",
        ],
    }


def _output_receipt(raw: bytes, *, max_bytes: int) -> dict[str, Any]:
    if len(raw) > max_bytes:
        raise CodingContainerError(
            "ERROR_CODING_CONTAINER_OUTPUT_TOO_LARGE",
            f"container output exceeded {max_bytes} bytes",
        )
    return {
        "byte_size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "text": raw.decode("utf-8", errors="replace"),
    }


def run_coding_container_plan(plan: CodingContainerPlan, *, cfg: Settings = settings) -> dict[str, Any]:
    before = validate_coding_container_plan(plan, cfg=cfg)
    if before["status"] != "CODING_CONTAINER_PLAN_VALID":
        raise CodingContainerError("ERROR_CODING_CONTAINER_PLAN_STALE", "repository state drifted before execution")

    argv = build_coding_container_command(plan)
    # Re-checked at execution: the channel or policy may differ from when the plan was built.
    try:
        require(PermissionRequest(capability="subprocess.execute", subject="coding_container.run", target=argv[0], origin="operator"), cfg=cfg)
    except PermissionEngineError as exc:
        raise CodingContainerError(exc.status, exc.detail) from exc
    started = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            cwd=plan.repo_root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=plan.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CodingContainerError("ERROR_CODING_CONTAINER_TIMEOUT", str(plan.timeout_seconds)) from exc
    except OSError as exc:
        raise CodingContainerError("ERROR_CODING_CONTAINER_START", str(exc)) from exc
    duration_ms = max(0, int((time.monotonic() - started) * 1000))

    stdout = _output_receipt(proc.stdout, max_bytes=plan.max_output_bytes)
    stderr = _output_receipt(proc.stderr, max_bytes=plan.max_output_bytes)
    after = validate_coding_container_plan(plan, cfg=cfg)
    receipt: dict[str, Any] = {
        "schema_version": CODING_CONTAINER_SCHEMA_VERSION,
        "status": "CODING_CONTAINER_EXECUTED_PASS" if proc.returncode == 0 else "CODING_CONTAINER_EXECUTED_FAIL",
        "execution_status": "PASS" if proc.returncode == 0 else "FAIL",
        "plan_sha256": plan.plan_sha256,
        "head_sha": plan.head_sha,
        "dirty_status": plan.dirty_status,
        "dirty_paths_sha256": plan.dirty_paths_sha256,
        "image": plan.image,
        "image_digest": plan.image.rsplit("@", 1)[-1],
        "image_identity_kind": "local_image_id" if LOCAL_IMAGE_ID_PATTERN.fullmatch(plan.image) else "registry_digest",
        "engine": plan.engine,
        "command_argv": list(plan.command),
        "container_argv_sha256": hashlib.sha256(json.dumps(argv, separators=(",", ":")).encode("utf-8")).hexdigest(),
        "exit_code": proc.returncode,
        "duration_ms": duration_ms,
        "stdout": stdout,
        "stderr": stderr,
        "source_mount_read_only": True,
        "workspace_ephemeral": True,
        "network_allowed": False,
        "implicit_image_pull_allowed": False,
        "non_root_uid_gid": CONTAINER_UID_GID,
        "pre_execution_validation": before,
        "post_execution_validation": after,
        "invariants": {
            "head_conserved": after["head_match"] is True,
            "dirty_state_conserved": after["dirty_state_match"] is True,
            "host_source_read_only": True,
            "ephemeral_workspace": True,
            "network_disabled": True,
            "image_immutable": bool("@sha256:" in plan.image or LOCAL_IMAGE_ID_PATTERN.fullmatch(plan.image)),
        },
    }
    receipt["receipt_sha256"] = hashlib.sha256(
        json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    if after["status"] != "CODING_CONTAINER_PLAN_VALID":
        receipt["status"] = "CODING_CONTAINER_SOURCE_DRIFT"
        receipt["execution_status"] = "INVALIDATED"
        raise CodingContainerError(
            "ERROR_CODING_CONTAINER_SOURCE_DRIFT",
            "host repository state changed during isolated execution",
            receipt=receipt,
        )
    return receipt
