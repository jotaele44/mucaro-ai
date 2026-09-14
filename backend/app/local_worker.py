from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

LOCAL_WORKER_PROTOCOL = "mucaro-local-worker/1.0"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_OUTPUT_BYTES = 1024 * 1024
DEFAULT_MAX_MEMORY_MB = 512
DEFAULT_MAX_CPU_SECONDS = 30


class LocalWorkerError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _minimal_environment(project_root: Path) -> dict[str, str]:
    env: dict[str, str] = {
        "MUCARO_AI_ROOT": str(project_root),
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for key in ("PATH", "SYSTEMROOT", "WINDIR", "HOME", "TMPDIR", "TEMP", "TMP"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def _resource_limiter(max_memory_mb: int, max_cpu_seconds: int):
    if os.name != "posix":
        return None
    try:
        import resource
    except ImportError:
        return None

    def apply_limits() -> None:
        memory_bytes = max_memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (max_cpu_seconds, max_cpu_seconds))

    return apply_limits


def run_local_worker(
    *,
    project_root: Path,
    skill_id: str,
    arguments: dict[str, Any] | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    max_memory_mb: int = DEFAULT_MAX_MEMORY_MB,
    max_cpu_seconds: int = DEFAULT_MAX_CPU_SECONDS,
    network_allowed: bool = False,
) -> dict[str, Any]:
    if timeout_seconds <= 0 or max_output_bytes <= 0 or max_memory_mb <= 0 or max_cpu_seconds <= 0:
        raise LocalWorkerError("ERROR_LOCAL_WORKER_LIMIT", "worker limits must be positive")
    request = {
        "protocol": LOCAL_WORKER_PROTOCOL,
        "skill_id": skill_id,
        "arguments": arguments or {},
        "network_allowed": bool(network_allowed),
    }
    request_bytes = json.dumps(request, sort_keys=True).encode("utf-8")
    if len(request_bytes) > max_output_bytes:
        raise LocalWorkerError("ERROR_LOCAL_WORKER_REQUEST_TOO_LARGE", str(len(request_bytes)))

    preexec_fn = _resource_limiter(max_memory_mb, max_cpu_seconds)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "backend.app.local_worker_entry"],
            input=request_bytes,
            cwd=project_root,
            env=_minimal_environment(project_root),
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            preexec_fn=preexec_fn,
        )
    except subprocess.TimeoutExpired as exc:
        raise LocalWorkerError("ERROR_LOCAL_WORKER_TIMEOUT", str(timeout_seconds)) from exc
    except OSError as exc:
        raise LocalWorkerError("ERROR_LOCAL_WORKER_START", str(exc)) from exc

    if len(proc.stdout) > max_output_bytes or len(proc.stderr) > max_output_bytes:
        raise LocalWorkerError("ERROR_LOCAL_WORKER_OUTPUT_TOO_LARGE", f"stdout={len(proc.stdout)} stderr={len(proc.stderr)}")
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip() or proc.stdout.decode("utf-8", errors="replace").strip()
        raise LocalWorkerError("ERROR_LOCAL_WORKER_FAILED", detail or f"exit={proc.returncode}")
    try:
        payload = json.loads(proc.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalWorkerError("ERROR_LOCAL_WORKER_PROTOCOL", str(exc)) from exc
    if not isinstance(payload, dict) or payload.get("protocol") != LOCAL_WORKER_PROTOCOL:
        raise LocalWorkerError("ERROR_LOCAL_WORKER_PROTOCOL", "invalid worker response")
    return {
        "status": "LOCAL_WORKER_OK",
        "protocol": LOCAL_WORKER_PROTOCOL,
        "skill_id": skill_id,
        "network_allowed": bool(network_allowed),
        "timeout_seconds": timeout_seconds,
        "max_output_bytes": max_output_bytes,
        "max_memory_mb": max_memory_mb,
        "max_cpu_seconds": max_cpu_seconds,
        "posix_resource_limits": preexec_fn is not None,
        "result": payload.get("result"),
    }
