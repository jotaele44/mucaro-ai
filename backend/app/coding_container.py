from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .coding_workspace import CodingWorkspaceError, resolve_repository
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
    mount_mode: str
    network_allowed: bool
    timeout_seconds: int
    max_output_bytes: int
    memory_mb: int
    cpus: float
    pids_limit: int
    engine: str

    @property
    def plan_sha256(self) -> str:
        payload = json.dumps(
            {
                "schema_version": CODING_CONTAINER_SCHEMA_VERSION,
                "repo_root": str(self.repo_root),
                "image": self.image,
                "command": list(self.command),
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


def build_coding_container_plan(
    repo_root: str | Path,
    *,
    image: str,
    command: Sequence[str],
    cfg: Settings = settings,
    mount_mode: str = "read_only",
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
    except CodingWorkspaceError as exc:
        raise CodingContainerError(exc.status, exc.detail) from exc
    try:
        pinned = validate_pinned_image(image)
        engine_path = resolve_container_engine(engine)
    except ContainerRuntimeError as exc:
        raise CodingContainerError(exc.status, exc.detail) from exc
    if mount_mode not in {"read_only", "read_write"}:
        raise CodingContainerError("ERROR_CODING_CONTAINER_MOUNT_MODE", mount_mode)
    if network_allowed:
        raise CodingContainerError(
            "ERROR_CODING_CONTAINER_NETWORK_POLICY",
            "coding container network access is disabled in the current certified boundary",
        )
    return CodingContainerPlan(
        repo_root=context.repo_root,
        image=pinned,
        command=_validate_command(command),
        mount_mode=mount_mode,
        network_allowed=False,
        timeout_seconds=_positive_int(timeout_seconds, "timeout_seconds"),
        max_output_bytes=_positive_int(max_output_bytes, "max_output_bytes"),
        memory_mb=_positive_int(memory_mb, "memory_mb"),
        cpus=_positive_number(cpus, "cpus"),
        pids_limit=_positive_int(pids_limit, "pids_limit"),
        engine=engine_path,
    )


def build_coding_container_command(plan: CodingContainerPlan) -> list[str]:
    mount_suffix = "ro" if plan.mount_mode == "read_only" else "rw"
    repo_mount = f"{plan.repo_root}:/workspace/repo:{mount_suffix}"
    command = [
        plan.engine,
        "run",
        "--rm",
        "--interactive",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--network=none",
        "--workdir=/workspace/repo",
        f"--mount=type=bind,src={plan.repo_root},dst=/workspace/repo,{mount_suffix}",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=256m",
        "--tmpfs=/workspace/out:rw,nosuid,nodev,size=256m",
        f"--memory={plan.memory_mb}m",
        f"--cpus={plan.cpus}",
        f"--pids-limit={plan.pids_limit}",
        "--env=HOME=/tmp/home",
        "--env=TMPDIR=/tmp",
        plan.image,
        *plan.command,
    ]
    # Keep the canonical bind description in the receipt independently of CLI token rendering.
    del repo_mount
    return command


def execute_coding_container(plan: CodingContainerPlan) -> dict[str, Any]:
    argv = build_coding_container_command(plan)
    try:
        proc = subprocess.run(
            argv,
            cwd=plan.repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=plan.timeout_seconds,
            env={key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "TMPDIR", "TMP", "TEMP"}},
        )
    except subprocess.TimeoutExpired as exc:
        raise CodingContainerError("ERROR_CODING_CONTAINER_TIMEOUT", str(plan.timeout_seconds)) from exc
    except OSError as exc:
        raise CodingContainerError("ERROR_CODING_CONTAINER_START", str(exc)) from exc
    if len(proc.stdout) > plan.max_output_bytes or len(proc.stderr) > plan.max_output_bytes:
        raise CodingContainerError(
            "ERROR_CODING_CONTAINER_OUTPUT_TOO_LARGE",
            f"stdout={len(proc.stdout)} stderr={len(proc.stderr)}",
        )
    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    return {
        "schema_version": CODING_CONTAINER_SCHEMA_VERSION,
        "status": "CODING_CONTAINER_PASS" if proc.returncode == 0 else "CODING_CONTAINER_FAIL",
        "plan_sha256": plan.plan_sha256,
        "repo_root": str(plan.repo_root),
        "image": plan.image,
        "image_digest": plan.image.rsplit("@", 1)[1],
        "engine": plan.engine,
        "command": list(plan.command),
        "mount_mode": plan.mount_mode,
        "network_allowed": False,
        "read_only_rootfs": True,
        "cap_drop_all": True,
        "no_new_privileges": True,
        "timeout_seconds": plan.timeout_seconds,
        "memory_mb": plan.memory_mb,
        "cpus": plan.cpus,
        "pids_limit": plan.pids_limit,
        "returncode": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_sha256": hashlib.sha256(proc.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(proc.stderr).hexdigest(),
        "stdout_bytes": len(proc.stdout),
        "stderr_bytes": len(proc.stderr),
        "limitations": [
            "network is fail-closed to none in this boundary",
            "read_write repository mounts are reserved for separately approved mutation transactions",
            "container image identity is pinned by digest but live engine/image certification remains separate",
        ],
        "falsification_criteria": [
            "reject unpinned images",
            "reject repository paths outside the explicit permission boundary",
            "reject network-enabled coding execution",
            "reject oversized command output rather than truncate it silently",
        ],
    }
