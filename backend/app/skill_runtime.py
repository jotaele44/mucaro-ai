from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable

from .capability_policy import CapabilityPolicy, RUNTIME_CAPABILITY, evaluate_capabilities
from .config import Settings, settings
from .utils import read_json, stable_id, utc_now, write_json

SKILL_MOUNTS_NAME = "skill_mounts.json"
SKILL_EXECUTIONS_NAME = "skill_execution_log.json"

ALLOWED_RUNTIMES = {"IN_PROCESS", "LOCAL_WORKER", "CONTAINER", "SERVICE", "FEDERATION_WORKER"}
ALLOWED_CAPABILITIES = {
    "filesystem.read",
    "filesystem.write",
    "network.public_http",
    "database.read",
    "database.write",
    "subprocess.execute",
    "model.invoke",
}


class SkillRuntimeError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class SkillDefinition:
    skill_id: str
    version: str
    description: str
    runtime: str
    capabilities: tuple[str, ...]
    input_schema: str
    output_schema: str
    handler: Callable[[dict[str, Any], Settings], dict[str, Any]]

    @property
    def digest(self) -> str:
        canonical = json.dumps(
            {
                "skill_id": self.skill_id,
                "version": self.version,
                "description": self.description,
                "runtime": self.runtime,
                "capabilities": sorted(self.capabilities),
                "input_schema": self.input_schema,
                "output_schema": self.output_schema,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(canonical).hexdigest()}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "description": self.description,
            "runtime": self.runtime,
            "capabilities": list(self.capabilities),
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "digest": self.digest,
        }


def _health_skill(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    del arguments
    cfg.ensure_dirs()
    return {
        "status": "INTELLIGENCE_PLANE_HEALTH_OK",
        "repo_id": "mucaro-ai",
        "membership_status": "PROVISIONAL",
        "role": "INTELLIGENCE_PLANE_CANDIDATE",
        "baseline_required": False,
        "lockstep_participant": True,
    }


SKILL_REGISTRY: dict[str, SkillDefinition] = {
    "mucaro/intelligence-plane-health": SkillDefinition(
        skill_id="mucaro/intelligence-plane-health",
        version="1.0.0",
        description="Return the local Mucaro Intelligence Plane candidate descriptor and health state.",
        runtime="IN_PROCESS",
        capabilities=(),
        input_schema="mucaro.skill-input/1.0",
        output_schema="mucaro.intelligence-plane-health/1.0",
        handler=_health_skill,
    ),
}


def _validate_definition(skill: SkillDefinition) -> None:
    if skill.runtime not in ALLOWED_RUNTIMES:
        raise SkillRuntimeError("ERROR_SKILL_RUNTIME_INVALID", skill.runtime)
    unknown = sorted(set(skill.capabilities) - ALLOWED_CAPABILITIES)
    if unknown:
        raise SkillRuntimeError("ERROR_SKILL_CAPABILITY_UNKNOWN", ",".join(unknown))


def _mounts_path(cfg: Settings) -> Any:
    return cfg.processed_dir / SKILL_MOUNTS_NAME


def _executions_path(cfg: Settings) -> Any:
    return cfg.processed_dir / SKILL_EXECUTIONS_NAME


def _load_mounts(cfg: Settings) -> list[dict[str, Any]]:
    return read_json(_mounts_path(cfg), [])


def _write_mounts(mounts: list[dict[str, Any]], cfg: Settings) -> None:
    write_json(_mounts_path(cfg), mounts)


def _load_executions(cfg: Settings) -> list[dict[str, Any]]:
    return read_json(_executions_path(cfg), [])


def _append_execution(event: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    events = _load_executions(cfg)
    events.append(event)
    write_json(_executions_path(cfg), events)
    return event


def list_skills() -> dict[str, Any]:
    skills = []
    for skill in SKILL_REGISTRY.values():
        _validate_definition(skill)
        skills.append(skill.as_dict())
    return {"status": "SKILLS_OK", "skills": sorted(skills, key=lambda item: item["skill_id"])}


def list_skill_mounts(cfg: Settings = settings) -> dict[str, Any]:
    return {"status": "SKILL_MOUNTS_OK", "mounts": _load_mounts(cfg)}


def mount_skill(
    skill_id: str,
    *,
    cfg: Settings = settings,
    mounted_by: str = "operator",
) -> dict[str, Any]:
    cfg.ensure_dirs()
    skill = SKILL_REGISTRY.get(skill_id)
    if skill is None:
        raise SkillRuntimeError("ERROR_SKILL_NOT_REGISTERED", skill_id)
    _validate_definition(skill)
    mounts = _load_mounts(cfg)
    existing = [item for item in mounts if item.get("skill_id") == skill_id]
    if existing:
        current = existing[0]
        if current.get("digest") == skill.digest:
            return {"status": "SKILL_ALREADY_MOUNTED", "mount": current}
        raise SkillRuntimeError("ERROR_SKILL_MOUNT_CONFLICT", skill_id)
    mounted_at = utc_now()
    mount = {
        "mount_id": stable_id("skillmount", skill_id, skill.version, skill.digest, mounted_at),
        "skill_id": skill_id,
        "version": skill.version,
        "digest": skill.digest,
        "runtime": skill.runtime,
        "capabilities": list(skill.capabilities),
        "state": "MOUNTED",
        "mounted_by": mounted_by,
        "mounted_at": mounted_at,
        "activated_by": None,
        "activated_at": None,
        "policy_decision": None,
    }
    mounts.append(mount)
    _write_mounts(mounts, cfg)
    return {"status": "SKILL_MOUNTED", "mount": mount}


def activate_skill(
    skill_id: str,
    *,
    allowed_capabilities: set[str] | None = None,
    policy: CapabilityPolicy | None = None,
    cfg: Settings = settings,
    activated_by: str = "operator",
) -> dict[str, Any]:
    allowed_capabilities = allowed_capabilities or set()
    skill = SKILL_REGISTRY.get(skill_id)
    if skill is None:
        raise SkillRuntimeError("ERROR_SKILL_NOT_REGISTERED", skill_id)
    _validate_definition(skill)

    runtime_capability = RUNTIME_CAPABILITY[skill.runtime]
    declared = set(skill.capabilities) | {runtime_capability}
    if policy is None:
        policy = CapabilityPolicy.build(
            policy_id="legacy-activation-compatibility",
            allowed=set(allowed_capabilities) | {runtime_capability},
        )
    decision = evaluate_capabilities(
        policy=policy,
        declared_capabilities=declared,
        requested_capabilities=declared,
    )
    if not decision.authorized:
        raise SkillRuntimeError(
            "ERROR_SKILL_CAPABILITY_NOT_AUTHORIZED",
            "; ".join(decision.reasons) or skill_id,
        )

    mounts = _load_mounts(cfg)
    matches = [(index, item) for index, item in enumerate(mounts) if item.get("skill_id") == skill_id]
    if not matches:
        raise SkillRuntimeError("ERROR_SKILL_NOT_MOUNTED", skill_id)
    index, mount = matches[0]
    if mount.get("digest") != skill.digest:
        raise SkillRuntimeError("ERROR_SKILL_DIGEST_MISMATCH", skill_id)

    decision_payload = decision.as_dict()
    if mount.get("state") == "ACTIVE" and mount.get("policy_decision") == decision_payload:
        return {"status": "SKILL_ALREADY_ACTIVE", "mount": mount, "policy_decision": decision_payload}

    activated_at = utc_now()
    was_active = mount.get("state") == "ACTIVE"
    updated = {
        **mount,
        "state": "ACTIVE",
        "activated_by": activated_by,
        "activated_at": activated_at,
        "policy_decision": decision_payload,
    }
    mounts[index] = updated
    _write_mounts(mounts, cfg)
    return {
        "status": "SKILL_REAUTHORIZED" if was_active else "SKILL_ACTIVATED",
        "mount": updated,
        "policy_decision": decision_payload,
    }


def execute_skill(
    skill_id: str,
    arguments: dict[str, Any] | None = None,
    *,
    cfg: Settings = settings,
    executed_by: str = "operator",
) -> dict[str, Any]:
    arguments = arguments or {}
    if not isinstance(arguments, dict):
        raise SkillRuntimeError("ERROR_SKILL_ARGUMENTS_NOT_OBJECT", "Skill arguments must be a JSON object.")
    skill = SKILL_REGISTRY.get(skill_id)
    if skill is None:
        raise SkillRuntimeError("ERROR_SKILL_NOT_REGISTERED", skill_id)
    mounts = _load_mounts(cfg)
    matches = [item for item in mounts if item.get("skill_id") == skill_id]
    if not matches or matches[0].get("state") != "ACTIVE":
        raise SkillRuntimeError("ERROR_SKILL_NOT_ACTIVE", skill_id)
    mount = matches[0]
    if mount.get("digest") != skill.digest:
        raise SkillRuntimeError("ERROR_SKILL_DIGEST_MISMATCH", skill_id)
    policy_decision = mount.get("policy_decision")
    if not isinstance(policy_decision, dict) or policy_decision.get("status") != "AUTHORIZED":
        raise SkillRuntimeError("ERROR_SKILL_POLICY_NOT_AUTHORIZED", skill_id)

    started_at = utc_now()
    execution_id = stable_id(
        "skillexec",
        skill_id,
        skill.version,
        skill.digest,
        json.dumps(arguments, sort_keys=True),
        started_at,
    )
    try:
        result = skill.handler(arguments, cfg)
        status = "PASS"
        error = None
    except Exception as exc:
        result = None
        status = "FAIL"
        error = {"type": type(exc).__name__, "detail": str(exc)}
    finished_at = utc_now()
    event = {
        "execution_id": execution_id,
        "skill_id": skill_id,
        "skill_version": skill.version,
        "skill_digest": skill.digest,
        "runtime": skill.runtime,
        "executed_by": executed_by,
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status,
        "arguments": arguments,
        "result": result,
        "error": error,
        "policy_decision": policy_decision,
    }
    _append_execution(event, cfg)
    if status != "PASS":
        raise SkillRuntimeError("ERROR_SKILL_EXECUTION_FAILED", json.dumps(error, sort_keys=True))
    return {"status": "SKILL_EXECUTED", "execution": event, "result": result}


def list_skill_executions(cfg: Settings = settings) -> dict[str, Any]:
    return {"status": "SKILL_EXECUTIONS_OK", "executions": _load_executions(cfg)}
