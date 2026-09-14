from __future__ import annotations

import json
import sys
from typing import Any

from .config import Settings
from .local_worker import LOCAL_WORKER_PROTOCOL
from .skill_runtime import SKILL_REGISTRY, _validate_definition

MAX_REQUEST_BYTES = 1024 * 1024


def _fail(status: str, detail: str) -> int:
    sys.stderr.write(json.dumps({"status": status, "detail": detail}, sort_keys=True))
    return 2


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        return _fail("ERROR_LOCAL_WORKER_REQUEST_TOO_LARGE", str(len(raw)))
    try:
        request = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _fail("ERROR_LOCAL_WORKER_PROTOCOL", str(exc))
    if not isinstance(request, dict) or request.get("protocol") != LOCAL_WORKER_PROTOCOL:
        return _fail("ERROR_LOCAL_WORKER_PROTOCOL", "invalid request protocol")

    skill_id = request.get("skill_id")
    arguments = request.get("arguments", {})
    network_allowed = request.get("network_allowed") is True
    if not isinstance(skill_id, str) or not skill_id:
        return _fail("ERROR_LOCAL_WORKER_SKILL_ID", "skill_id must be a non-empty string")
    if not isinstance(arguments, dict):
        return _fail("ERROR_LOCAL_WORKER_ARGUMENTS", "arguments must be an object")

    skill = SKILL_REGISTRY.get(skill_id)
    if skill is None:
        return _fail("ERROR_SKILL_NOT_REGISTERED", skill_id)
    try:
        _validate_definition(skill)
    except Exception as exc:
        return _fail("ERROR_LOCAL_WORKER_SKILL_INVALID", str(exc))
    if skill.runtime != "LOCAL_WORKER":
        return _fail("ERROR_LOCAL_WORKER_RUNTIME_MISMATCH", skill.runtime)
    if "network.public_http" in skill.capabilities and not network_allowed:
        return _fail("ERROR_LOCAL_WORKER_NETWORK_DENIED", skill_id)

    cfg = Settings.load()
    try:
        result: Any = skill.handler(arguments, cfg)
    except Exception as exc:
        return _fail("ERROR_LOCAL_WORKER_HANDLER", f"{type(exc).__name__}: {exc}")

    response = {
        "protocol": LOCAL_WORKER_PROTOCOL,
        "status": "PASS",
        "skill_id": skill_id,
        "result": result,
    }
    sys.stdout.write(json.dumps(response, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
