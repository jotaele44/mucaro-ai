from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .coding_workspace import CodingWorkspaceError, inspect_repository, resolve_repository
from .config import Settings, settings
from .container_runtime import ContainerRuntimeError, resolve_container_engine, validate_pinned_image

CODING_CONTAINER_SCHEMA_VERSION = "mucaro-coding-container/1.0"
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_OUTPUT_BYTES = 2 * 1024 * 1024
DEFAULT_MEMORY_MB = 2048
DEFAULT_CPUS = 2.0
DEFAULT_PIDS_LIMIT = 256
MAX_COMMAND_PARTS = 64
MAX_COMMAND_PART_BYTES = 4096


class CodingContainerError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


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
    engine: str

    @property
    def mount_mode(self) -> str:
        return "read_only"

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
    engine: str | None = None,
) -> CodingContainerPlan:
    try:
        context = resolve_repository(repo_root, cfg)
        state = inspect_repository(context.repo_root, cfg=cfg)
    except CodingWorkspaceError as exc:
        raise CodingContainerError(exc.status, exc.detail) from exc
    try:
        pinned = validate_pinned_image(image)
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
        engine=engine_path,
    )


def build_coding_container_command(plan: CodingContainerPlan) -> list[str]:
    return [
        plan.engine,
        "run",
        "--rm",
        "--interactive",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--network=none",
        "--workdir=/workspace/repo",
        f"--mount=type=bind,src={plan.repo_root},dst=/workspace/repo,ro",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=256m",
        "--tmpfs=/workspace/out:rw,nosuid,nodev,size=256m",
        f"--memory={plan.memory_mb}m",
        f"--cpus={plan.cpus}",
        f"--pids-limit={plan.pids_limit}",
        "--env=HOME=/tmp/home",
        "--env=TMPDIR=/tmp",
        "--env=XDG_CACHE_HOME=/tmp/cache",
        "--env=PYTHONDONTWRITEBYTECODE=1",
        "--env=NPM_CONFIG_CACHE=/tmp/npm-cache",
        plan.image,
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
        "repository_read_only": True,
        "network_allowed": False,
        "command_argv": list(plan.command),
        "container_argv": build_coding_container_command(plan),
        "limitations": [
            "this function validates and emits an execution plan; it does not launch the container",
            "writable repository mounts are intentionally unavailable in this boundary",
            "live container image/engine execution remains separately uncertified",
        ],
        "falsification_criteria": [
            "reject unpinned image identity",
            "reject network-enabled plans",
            "treat HEAD or dirty-state drift as STALE before execution",
            "never convert this read-only plan into a writable mount",
        ],
    }
