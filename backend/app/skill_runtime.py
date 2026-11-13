from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Callable

from .artifact_identity import ArtifactIdentityError, ArtifactRecord, create_artifact_record
from .capability_policy import RUNTIME_CAPABILITY, CapabilityPolicy, evaluate_capabilities
from .config import Settings, settings
from .distribution_policy import DistributionPolicyError, require_distribution_runtime_authorized
from .execution_router import ExecutionRouterError, execute_routed, select_execution_route
from .runtime_identity import build_runtime_identity
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


def _local_worker_health_skill(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    del arguments
    cfg.ensure_dirs()
    sensitive_keys = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "MUCARO_AI_OPERATOR_TOKEN")
    return {
        "status": "LOCAL_WORKER_HEALTH_OK",
        "pid": os.getpid(),
        "project_root": str(cfg.project_root),
        "sensitive_environment_present": [key for key in sensitive_keys if os.getenv(key)],
    }


def _exact_head_certification_skill(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    del cfg
    required_fields = (
        "baseline_sha",
        "candidate_sha",
        "observed_head_sha",
        "observed_remote_sha",
        "observed_pr_head_sha",
        "worktree_clean",
        "required_gates",
        "passed_gates",
        "manifest_sha256",
        "evidence_sha256",
    )
    reasons: list[str] = []
    missing_fields = sorted(field for field in required_fields if field not in arguments)
    if missing_fields:
        reasons.append("missing required fields: " + ",".join(missing_fields))

    def valid_hex(value: Any, length: int) -> bool:
        return isinstance(value, str) and len(value) == length and all(
            ch in "0123456789abcdefABCDEF" for ch in value
        )

    def normalized_sha(value: Any, length: int) -> str | None:
        return value.lower() if valid_hex(value, length) else None

    baseline_raw = arguments.get("baseline_sha")
    candidate_raw = arguments.get("candidate_sha")
    observed_raw = {
        "head": arguments.get("observed_head_sha"),
        "remote": arguments.get("observed_remote_sha"),
        "pr_head": arguments.get("observed_pr_head_sha"),
    }

    baseline_sha = normalized_sha(baseline_raw, 40)
    candidate_sha = normalized_sha(candidate_raw, 40)
    observed = {name: normalized_sha(value, 40) for name, value in observed_raw.items()}

    if baseline_sha is None:
        reasons.append("baseline_sha must be a nonempty 40-character hexadecimal commit SHA")
    if candidate_sha is None:
        reasons.append("candidate_sha must be a nonempty 40-character hexadecimal commit SHA")
    invalid_observed = sorted(name for name, value in observed.items() if value is None)
    if invalid_observed:
        reasons.append("observed commit SHA invalid or missing: " + ",".join(invalid_observed))
    if candidate_sha is not None and not invalid_observed:
        mismatches = sorted(name for name, value in observed.items() if value != candidate_sha)
        if mismatches:
            reasons.append("candidate SHA mismatch: " + ",".join(mismatches))

    if arguments.get("worktree_clean") is not True:
        reasons.append("worktree is not explicitly clean")

    required_gates = arguments.get("required_gates")
    passed_gates = arguments.get("passed_gates")
    if (
        not isinstance(required_gates, list)
        or not required_gates
        or not all(isinstance(item, str) and item.strip() for item in required_gates)
    ):
        reasons.append("required_gates must be a nonempty string list")
        required_gates = []
    else:
        required_gates = [item.strip() for item in required_gates]
    if (
        not isinstance(passed_gates, list)
        or not all(isinstance(item, str) and item.strip() for item in passed_gates)
    ):
        reasons.append("passed_gates must be a string list")
        passed_gates = []
    else:
        passed_gates = [item.strip() for item in passed_gates]

    missing_gates = sorted(set(required_gates) - set(passed_gates))
    if missing_gates:
        reasons.append("required gates not passed: " + ",".join(missing_gates))

    unresolved = arguments.get("unresolved_states", [])
    if not isinstance(unresolved, list):
        reasons.append("unresolved_states must be a list")
        unresolved = ["INVALID_UNRESOLVED_STATES"]
    unresolved = [str(item).strip() for item in unresolved if str(item).strip()]
    if unresolved:
        reasons.append("unresolved certification residue: " + ",".join(sorted(unresolved)))

    superseded_raw = arguments.get("superseded_shas", [])
    superseded: list[str] = []
    if not isinstance(superseded_raw, list):
        reasons.append("superseded_shas must be a list")
    else:
        invalid_superseded = []
        for item in superseded_raw:
            normalized = normalized_sha(item, 40)
            if normalized is None:
                invalid_superseded.append(str(item))
            else:
                superseded.append(normalized)
        if invalid_superseded:
            reasons.append("superseded_shas contains invalid SHA values")
    if candidate_sha is not None and candidate_sha in superseded:
        reasons.append("candidate SHA is explicitly superseded")

    digests: dict[str, str | None] = {}
    for field in ("manifest_sha256", "evidence_sha256"):
        value = arguments.get(field)
        normalized = normalized_sha(value, 64)
        digests[field] = normalized
        if normalized is None:
            reasons.append(f"{field} must be a nonempty 64-character hexadecimal SHA-256")

    identity_match = (
        candidate_sha is not None
        and baseline_sha is not None
        and not invalid_observed
        and all(value == candidate_sha for value in observed.values())
    )
    passed = not reasons
    return {
        "status": "EXACT_HEAD_CERTIFICATION_PASS" if passed else "EXACT_HEAD_CERTIFICATION_OPEN",
        "certification_state": "PASS" if passed else "OPEN",
        "promotable": passed,
        "fail_closed": True,
        "baseline_sha": baseline_sha,
        "candidate_sha": candidate_sha,
        "identity_match": identity_match,
        "required_gates": sorted(set(required_gates)),
        "passed_gates": sorted(set(passed_gates)),
        "missing_gates": missing_gates,
        "unresolved_states": sorted(unresolved),
        "manifest_sha256": digests["manifest_sha256"],
        "evidence_sha256": digests["evidence_sha256"],
        "reasons": reasons,
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
    "mucaro/exact-head-certification-audit": SkillDefinition(
        skill_id="mucaro/exact-head-certification-audit",
        version="1.0.0",
        description="Fail-closed exact-head certification evaluator for frozen evidence and promotion gates.",
        runtime="IN_PROCESS",
        capabilities=(),
        input_schema="mucaro.exact-head-certification-input/1.0",
        output_schema="mucaro.exact-head-certification-output/1.0",
        handler=_exact_head_certification_skill,
    ),
    "mucaro/local-worker-health": SkillDefinition(
        skill_id="mucaro/local-worker-health",
        version="1.0.0",
        description="Return bounded local-worker process and environment health evidence.",
        runtime="LOCAL_WORKER",
        capabilities=(),
        input_schema="mucaro.skill-input/1.0",
        output_schema="mucaro.local-worker-health/1.0",
        handler=_local_worker_health_skill,
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


def _canonical_payload(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SkillRuntimeError("ERROR_SKILL_ARTIFACT_SERIALIZATION", str(exc)) from exc


def _artifact_payload(record: ArtifactRecord) -> dict[str, Any]:
    return record.as_dict()


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
    try:
        require_distribution_runtime_authorized(
            distribution_channel=cfg.distribution_channel,
            runtime=skill.runtime,
            granted_capabilities=decision.granted,
            skill_id=skill.skill_id,
        )
    except DistributionPolicyError as exc:
        raise SkillRuntimeError(exc.status, exc.detail) from exc

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

    declared = set(skill.capabilities) | {RUNTIME_CAPABILITY[skill.runtime]}
    recheck = evaluate_capabilities(
        policy=CapabilityPolicy.build(
            policy_id=str(policy_decision.get("policy_id", "stored-activation-grant")),
            allowed=set(policy_decision.get("granted", [])),
        ),
        declared_capabilities=declared,
        requested_capabilities=declared,
    )
    if not recheck.authorized:
        raise SkillRuntimeError("ERROR_SKILL_POLICY_STALE", "; ".join(recheck.reasons) or skill_id)
    try:
        require_distribution_runtime_authorized(
            distribution_channel=cfg.distribution_channel,
            runtime=skill.runtime,
            granted_capabilities=recheck.granted,
            skill_id=skill.skill_id,
        )
    except DistributionPolicyError as exc:
        raise SkillRuntimeError(exc.status, exc.detail) from exc

    try:
        route = select_execution_route(skill.runtime, policy_decision)
    except ExecutionRouterError as exc:
        raise SkillRuntimeError(exc.status, exc.detail) from exc
    runtime_identity = build_runtime_identity(
        project_root=cfg.project_root,
        runtime=route,
        skill_id=skill.skill_id,
        skill_version=skill.version,
        skill_digest=skill.digest,
    )
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
        input_artifact = create_artifact_record(
            processed_dir=cfg.processed_dir,
            artifact_type="SOURCE",
            payload=_canonical_payload(arguments),
            logical_identity=f"skill_input:{skill_id}:{execution_id}",
            producer="skill_runtime",
            producer_version="1.0.0",
            schema_id=skill.input_schema,
            source_path_or_uri=f"skill://{skill_id}/input",
        )
    except ArtifactIdentityError as exc:
        raise SkillRuntimeError("ERROR_SKILL_INPUT_ARTIFACT", str(exc)) from exc
    artifact_lineage: dict[str, Any] = {"input": _artifact_payload(input_artifact), "output": None, "failure_receipt": None}
    try:
        routed = execute_routed(
            runtime=route,
            skill_id=skill_id,
            handler=skill.handler,
            arguments=arguments,
            cfg=cfg,
            policy_decision=policy_decision,
        )
        result = routed["result"]
        adapter_receipt = routed["adapter_receipt"]
        try:
            output_artifact = create_artifact_record(
                processed_dir=cfg.processed_dir,
                artifact_type="DERIVED",
                payload=_canonical_payload(result),
                logical_identity=f"skill_output:{skill_id}:{execution_id}",
                producer="skill_runtime",
                producer_version="1.0.0",
                parent_artifact_ids=[input_artifact.artifact_id],
                schema_id=skill.output_schema,
                source_path_or_uri=f"skill://{skill_id}/output",
            )
        except ArtifactIdentityError as exc:
            raise SkillRuntimeError("ERROR_SKILL_OUTPUT_ARTIFACT", str(exc)) from exc
        artifact_lineage["output"] = _artifact_payload(output_artifact)
        status = "PASS"
        error = None
    except Exception as exc:
        result = None
        adapter_receipt = None
        status = "FAIL"
        error = {"type": type(exc).__name__, "detail": str(exc)}
        try:
            failure_artifact = create_artifact_record(
                processed_dir=cfg.processed_dir,
                artifact_type="RECEIPT",
                payload=_canonical_payload(error),
                logical_identity=f"skill_failure:{skill_id}:{execution_id}",
                producer="skill_runtime",
                producer_version="1.0.0",
                parent_artifact_ids=[input_artifact.artifact_id],
                schema_id="mucaro-skill-execution-error/1.0",
                source_path_or_uri=f"skill://{skill_id}/failure",
            )
            artifact_lineage["failure_receipt"] = _artifact_payload(failure_artifact)
        except ArtifactIdentityError as artifact_exc:
            error["artifact_error"] = str(artifact_exc)
    finished_at = utc_now()
    event = {
        "execution_id": execution_id,
        "skill_id": skill_id,
        "skill_version": skill.version,
        "skill_digest": skill.digest,
        "runtime": skill.runtime,
        "execution_route": route,
        "runtime_identity": runtime_identity,
        "adapter_receipt": adapter_receipt,
        "executed_by": executed_by,
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status,
        "arguments": arguments,
        "result": result,
        "error": error,
        "policy_decision": policy_decision,
        "artifact_lineage": artifact_lineage,
    }
    _append_execution(event, cfg)
    if status != "PASS":
        raise SkillRuntimeError("ERROR_SKILL_EXECUTION_FAILED", json.dumps(error, sort_keys=True))
    return {"status": "SKILL_EXECUTED", "execution": event, "result": result}


def list_skill_executions(cfg: Settings = settings) -> dict[str, Any]:
    return {"status": "SKILL_EXECUTIONS_OK", "executions": _load_executions(cfg)}
