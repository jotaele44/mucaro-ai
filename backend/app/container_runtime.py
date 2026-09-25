from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .distribution_policy import DistributionPolicyError, require_distribution_runtime_authorized
from .permission_engine import PermissionEngineError, PermissionRequest, require

CONTAINER_PROTOCOL = "mucaro-container-worker/1.0"
CONTAINER_BINDING_SCHEMA_VERSION = "mucaro-container-binding/1.0"
PINNED_IMAGE_PATTERN = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MAX_OUTPUT_BYTES = 2 * 1024 * 1024
DEFAULT_MEMORY_MB = 1024
DEFAULT_CPUS = 1.0
DEFAULT_PIDS_LIMIT = 128


class ContainerRuntimeError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class ContainerBinding:
    skill_id: str
    image: str

    @property
    def digest(self) -> str:
        validate_pinned_image(self.image)
        payload = json.dumps(
            {"schema_version": CONTAINER_BINDING_SCHEMA_VERSION, "skill_id": self.skill_id, "image": self.image},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def as_dict(self) -> dict[str, str]:
        return {
            "schema_version": CONTAINER_BINDING_SCHEMA_VERSION,
            "skill_id": self.skill_id,
            "image": validate_pinned_image(self.image),
            "binding_sha256": self.digest,
        }


CONTAINER_IMAGE_BINDINGS: dict[str, ContainerBinding] = {}


def validate_pinned_image(image: str) -> str:
    if not isinstance(image, str) or not PINNED_IMAGE_PATTERN.fullmatch(image):
        raise ContainerRuntimeError(
            "ERROR_CONTAINER_IMAGE_NOT_PINNED",
            "container image must use immutable name@sha256:<64 lowercase hex> form",
        )
    return image


def get_container_binding(skill_id: str) -> ContainerBinding:
    binding = CONTAINER_IMAGE_BINDINGS.get(skill_id)
    if binding is None:
        raise ContainerRuntimeError("ERROR_CONTAINER_BINDING_MISSING", skill_id)
    validate_pinned_image(binding.image)
    return binding


def resolve_container_engine(preferred: str | None = None) -> str:
    if preferred:
        resolved = shutil.which(preferred)
        if resolved:
            return resolved
        raise ContainerRuntimeError("ERROR_CONTAINER_ENGINE_UNAVAILABLE", preferred)
    for candidate in ("docker", "podman"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise ContainerRuntimeError("ERROR_CONTAINER_ENGINE_UNAVAILABLE", "docker,podman")


def build_container_command(
    *,
    engine: str,
    image: str,
    network_allowed: bool,
    memory_mb: int,
    cpus: float,
    pids_limit: int,
) -> list[str]:
    validate_pinned_image(image)
    if memory_mb <= 0 or cpus <= 0 or pids_limit <= 0:
        raise ContainerRuntimeError("ERROR_CONTAINER_LIMIT", "container limits must be positive")
    command = [
        engine,
        "run",
        "--rm",
        "--interactive",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--memory={memory_mb}m",
        f"--cpus={cpus}",
        f"--pids-limit={pids_limit}",
        f"--network={'bridge' if network_allowed else 'none'}",
        image,
        "python",
        "-m",
        "backend.app.container_worker_entry",
    ]
    return command


def run_container_worker(
    *,
    project_root: Path,
    skill_id: str,
    image: str,
    arguments: dict[str, Any] | None = None,
    network_allowed: bool = False,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    memory_mb: int = DEFAULT_MEMORY_MB,
    cpus: float = DEFAULT_CPUS,
    pids_limit: int = DEFAULT_PIDS_LIMIT,
    engine: str | None = None,
    distribution_channel: str = "local",
) -> dict[str, Any]:
    try:
        require_distribution_runtime_authorized(
            distribution_channel=distribution_channel,
            runtime="CONTAINER",
            granted_capabilities={"runtime.container"},
            skill_id=skill_id,
        )
    except DistributionPolicyError as exc:
        raise ContainerRuntimeError(exc.status, exc.detail) from exc
    validate_pinned_image(image)
    if timeout_seconds <= 0 or max_output_bytes <= 0:
        raise ContainerRuntimeError("ERROR_CONTAINER_LIMIT", "container limits must be positive")
    engine_path = resolve_container_engine(engine)
    request = {
        "protocol": CONTAINER_PROTOCOL,
        "skill_id": skill_id,
        "arguments": arguments or {},
        "network_allowed": bool(network_allowed),
    }
    request_bytes = json.dumps(request, sort_keys=True).encode("utf-8")
    if len(request_bytes) > max_output_bytes:
        raise ContainerRuntimeError("ERROR_CONTAINER_REQUEST_TOO_LARGE", str(len(request_bytes)))
    command = build_container_command(
        engine=engine_path,
        image=image,
        network_allowed=network_allowed,
        memory_mb=memory_mb,
        cpus=cpus,
        pids_limit=pids_limit,
    )
    for capability in ("subprocess.execute", *(("network.public_http",) if network_allowed else ())):
        try:
            require(
                PermissionRequest(capability=capability, subject=skill_id, target=engine_path, origin="operator"),
                distribution_channel=distribution_channel,
            )
        except PermissionEngineError as exc:
            raise ContainerRuntimeError(exc.status, exc.detail) from exc
    try:
        proc = subprocess.run(
            command,
            input=request_bytes,
            cwd=project_root,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ContainerRuntimeError("ERROR_CONTAINER_TIMEOUT", str(timeout_seconds)) from exc
    except OSError as exc:
        raise ContainerRuntimeError("ERROR_CONTAINER_START", str(exc)) from exc
    if len(proc.stdout) > max_output_bytes or len(proc.stderr) > max_output_bytes:
        raise ContainerRuntimeError(
            "ERROR_CONTAINER_OUTPUT_TOO_LARGE",
            f"stdout={len(proc.stdout)} stderr={len(proc.stderr)}",
        )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip() or proc.stdout.decode("utf-8", errors="replace").strip()
        raise ContainerRuntimeError("ERROR_CONTAINER_FAILED", detail or f"exit={proc.returncode}")
    try:
        payload = json.loads(proc.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContainerRuntimeError("ERROR_CONTAINER_PROTOCOL", str(exc)) from exc
    if not isinstance(payload, dict) or payload.get("protocol") != CONTAINER_PROTOCOL:
        raise ContainerRuntimeError("ERROR_CONTAINER_PROTOCOL", "invalid container response")
    return {
        "status": "CONTAINER_WORKER_OK",
        "protocol": CONTAINER_PROTOCOL,
        "skill_id": skill_id,
        "image": image,
        "image_digest": image.rsplit("@", 1)[1],
        "engine": engine_path,
        "network_allowed": bool(network_allowed),
        "read_only_rootfs": True,
        "cap_drop_all": True,
        "no_new_privileges": True,
        "memory_mb": memory_mb,
        "cpus": cpus,
        "pids_limit": pids_limit,
        "timeout_seconds": timeout_seconds,
        "result": payload.get("result"),
    }
