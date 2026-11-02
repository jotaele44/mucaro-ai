from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable

from jsonschema import Draft202012Validator, SchemaError

from .artifact_identity import ArtifactIdentityError, ArtifactRecord, create_artifact_record
from .capability_policy import RUNTIME_CAPABILITY, CapabilityPolicy, evaluate_capabilities
from .config import Settings, settings
from .contracts import ContractViolation, validate_contract
from .distribution_policy import DistributionPolicyError, require_distribution_runtime_authorized
from .execution_router import ExecutionRouterError, execute_routed, select_execution_route
from .gis_raster_skill import RASTER_ARGUMENTS_SCHEMA, RASTER_EXECUTION_POLICY, analyze_raster_data
from .gis_skill import GIS_ARGUMENTS_SCHEMA, GIS_EXECUTION_POLICY, analyze_spatial_data
from .ocr_skill import OCR_ARGUMENTS_SCHEMA, OCR_EXECUTION_POLICY, analyze_document
from .web_research import RESEARCH_ARGUMENTS_SCHEMA, RESEARCH_POLICY, compare_captures
from .runtime_identity import build_runtime_identity
from .utils import file_lock, read_json, stable_id, update_json, utc_now, write_json

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
    arguments_schema: dict[str, Any] | None = None
    execution_policy: dict[str, Any] | None = None

    @property
    def digest(self) -> str:
        identity = {
                "skill_id": self.skill_id,
                "version": self.version,
                "description": self.description,
                "runtime": self.runtime,
                "capabilities": sorted(self.capabilities),
                "input_schema": self.input_schema,
                "output_schema": self.output_schema,
            }
        # Omit absent extensions to conserve existing mounted skill identities.
        if self.arguments_schema is not None:
            identity["arguments_schema"] = self.arguments_schema
        if self.execution_policy is not None:
            identity["execution_policy"] = self.execution_policy
        canonical = json.dumps(
            identity,
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
            "arguments_schema": self.arguments_schema,
            "execution_policy": self.execution_policy,
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


SKILL_REGISTRY: dict[str, SkillDefinition] = {
    "mucaro/web-research": SkillDefinition(
        skill_id="mucaro/web-research", version="1.0.0",
        description="Compare two explicit frozen web observations, inspect exact passage citations, and save a source-bound synthesis. Offline excerpts are the default; model synthesis is explicit and never falls back silently. No URLs are fetched.",
        runtime="IN_PROCESS", capabilities=("filesystem.read", "filesystem.write", "model.invoke"),
        input_schema="mucaro.web-research-input/1.0", output_schema="mucaro.web-research-result/1.0",
        handler=compare_captures, arguments_schema=RESEARCH_ARGUMENTS_SCHEMA, execution_policy=RESEARCH_POLICY,
    ),
    "mucaro/gis-raster": SkillDefinition(
        skill_id="mucaro/gis-raster", version="1.0.0",
        description="Inspect, sample, clip, resample, derive terrain/hillshade, condition depressions, route D8 flow, and analyze vector zones over bounded local GeoTIFFs. Preserves NODATA, source bytes, unresolved states, and saved artifact lineage.",
        runtime="IN_PROCESS", capabilities=("filesystem.read", "filesystem.write"),
        input_schema="mucaro.gis-raster-input/1.0", output_schema="mucaro.gis-raster-result/1.0",
        handler=analyze_raster_data, arguments_schema=RASTER_ARGUMENTS_SCHEMA, execution_policy=RASTER_EXECUTION_POLICY,
    ),
    "mucaro/gis-analyze": SkillDefinition(
        skill_id="mucaro/gis-analyze", version="1.0.0",
        description="Inspect, measure, transform, repair, compare, overlay, or spatially join bounded local GeoJSON layers. Preserves input bytes, CRS, warnings, and artifact lineage; pair operations require unique explicit feature IDs.",
        runtime="IN_PROCESS", capabilities=("filesystem.read", "filesystem.write"),
        input_schema="mucaro.gis-skill-input/1.0", output_schema="mucaro.gis-skill-result/1.0",
        handler=analyze_spatial_data, arguments_schema=GIS_ARGUMENTS_SCHEMA, execution_policy=GIS_EXECUTION_POLICY,
    ),
    "mucaro/ocr-analyze": SkillDefinition(
        skill_id="mucaro/ocr-analyze", version="1.0.0",
        description="Extract text from a local image or PDF with source hashes, confidence warnings, page lineage, and saved reports. Requires directory access and local OCR engines.",
        runtime="IN_PROCESS", capabilities=("filesystem.read", "filesystem.write", "subprocess.execute"),
        input_schema="mucaro.ocr-skill-input/1.0", output_schema="mucaro.ocr-skill-result/1.0",
        handler=analyze_document, arguments_schema=OCR_ARGUMENTS_SCHEMA, execution_policy=OCR_EXECUTION_POLICY,
    ),
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
    if skill.arguments_schema is not None:
        try:
            Draft202012Validator.check_schema(skill.arguments_schema)
        except SchemaError as exc:
            raise SkillRuntimeError("ERROR_SKILL_SCHEMA_INVALID", skill.skill_id) from exc


def validate_skill_arguments(skill_id: str, arguments: dict[str, Any]) -> None:
    skill = SKILL_REGISTRY.get(skill_id)
    if skill is None:
        raise SkillRuntimeError("ERROR_SKILL_NOT_REGISTERED", skill_id)
    _validate_definition(skill)
    try:
        json.dumps(arguments, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SkillRuntimeError("ERROR_SKILL_ARGUMENTS_INVALID", "Arguments must contain valid JSON values and finite numbers.") from exc
    if skill.arguments_schema is not None:
        errors = list(Draft202012Validator(skill.arguments_schema).iter_errors(arguments))
        if errors:
            raise SkillRuntimeError("ERROR_SKILL_ARGUMENTS_INVALID", "; ".join(f"{error.json_path}: {error.message}" for error in errors[:5]))


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
    update_json(_executions_path(cfg), [], lambda events: events.append(event))
    return event


def _serialize_mount_operation(operation: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    @wraps(operation)
    def locked(*args: Any, **kwargs: Any) -> dict[str, Any]:
        cfg = kwargs.get("cfg", settings)
        with file_lock(_mounts_path(cfg)):
            return operation(*args, **kwargs)
    return locked


def _matching_mounts(mounts: list[dict[str, Any]], skill_id: str) -> list[tuple[int, dict[str, Any]]]:
    matches = [(index, mount) for index, mount in enumerate(mounts) if mount.get("skill_id") == skill_id]
    if len(matches) > 1:
        raise SkillRuntimeError("ERROR_SKILL_MOUNT_AMBIGUOUS", skill_id)
    return matches


def _lifecycle_event(mount: dict[str, Any], state: str, actor: str, at: str) -> dict[str, Any]:
    return {
        "sequence": int(mount.get("lifecycle_sequence", 0)) + 1,
        "from_state": mount["state"],
        "to_state": state,
        "actor": actor,
        "at": at,
        "previous_policy_decision": mount.get("policy_decision"),
    }


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


@_serialize_mount_operation
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
    existing = _matching_mounts(mounts, skill_id)
    if existing:
        current = existing[0][1]
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
        "lifecycle_sequence": 0,
        "lifecycle_events": [],
    }
    mounts.append(mount)
    _write_mounts(mounts, cfg)
    return {"status": "SKILL_MOUNTED", "mount": mount}


@_serialize_mount_operation
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
    matches = _matching_mounts(mounts, skill_id)
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
    event = _lifecycle_event(mount, "ACTIVE", activated_by, activated_at)
    updated = {
        **mount,
        "state": "ACTIVE",
        "activated_by": activated_by,
        "activated_at": activated_at,
        "policy_decision": decision_payload,
        "lifecycle_sequence": event["sequence"],
        "lifecycle_events": [*mount.get("lifecycle_events", []), event],
    }
    mounts[index] = updated
    _write_mounts(mounts, cfg)
    return {
        "status": "SKILL_REAUTHORIZED" if was_active else "SKILL_ACTIVATED",
        "mount": updated,
        "policy_decision": decision_payload,
    }


@_serialize_mount_operation
def deactivate_skill(
    skill_id: str,
    *,
    cfg: Settings = settings,
    deactivated_by: str = "operator",
) -> dict[str, Any]:
    """Revoke future admissions, retaining history and any already-admitted work.

    Revocation deliberately does not require a current registry definition or digest:
    an operator must also be able to revoke an obsolete or removed skill mount.
    """
    mounts = _load_mounts(cfg)
    matches = _matching_mounts(mounts, skill_id)
    if not matches:
        raise SkillRuntimeError("ERROR_SKILL_NOT_MOUNTED", skill_id)
    index, mount = matches[0]
    if mount.get("state") == "INACTIVE" and mount.get("policy_decision") is None:
        return {"status": "SKILL_ALREADY_INACTIVE", "mount": mount}
    at = utc_now()
    event = _lifecycle_event(mount, "INACTIVE", deactivated_by, at)
    updated = {
        **mount,
        "state": "INACTIVE",
        "policy_decision": None,
        "deactivated_at": at,
        "deactivated_by": deactivated_by,
        "lifecycle_sequence": event["sequence"],
        "lifecycle_events": [*mount.get("lifecycle_events", []), event],
    }
    mounts[index] = updated
    _write_mounts(mounts, cfg)
    return {"status": "SKILL_DEACTIVATED", "mount": updated}


def execute_skill(
    skill_id: str,
    arguments: dict[str, Any] | None = None,
    *,
    cfg: Settings = settings,
    executed_by: str = "operator",
) -> dict[str, Any]:
    arguments = {} if arguments is None else arguments
    if not isinstance(arguments, dict):
        raise SkillRuntimeError("ERROR_SKILL_ARGUMENTS_NOT_OBJECT", "Skill arguments must be a JSON object.")
    skill = SKILL_REGISTRY.get(skill_id)
    if skill is None:
        raise SkillRuntimeError("ERROR_SKILL_NOT_REGISTERED", skill_id)
    validate_skill_arguments(skill_id, arguments)
    # This lock is the admission boundary shared with activation and revocation.
    # The handler runs outside it so revocation can block later invocations promptly.
    with file_lock(_mounts_path(cfg)):
        matches = _matching_mounts(_load_mounts(cfg), skill_id)
        if not matches or matches[0][1].get("state") != "ACTIVE":
            raise SkillRuntimeError("ERROR_SKILL_NOT_ACTIVE", skill_id)
        mount = matches[0][1]
        admitted_at = utc_now()
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
        artifact_ids = result.get("artifact_ids", [])
        if not isinstance(artifact_ids, list) or len(artifact_ids) > 32 or any(not isinstance(item, str) for item in artifact_ids):
            raise SkillRuntimeError("ERROR_SKILL_RESULT_ARTIFACTS_INVALID", "Result artifact references must be a bounded list of IDs.")
        try:
            output_artifact = create_artifact_record(
                processed_dir=cfg.processed_dir,
                artifact_type="DERIVED",
                payload=_canonical_payload(result),
                logical_identity=f"skill_output:{skill_id}:{execution_id}",
                producer="skill_runtime",
                producer_version="1.0.0",
                parent_artifact_ids=[input_artifact.artifact_id, *artifact_ids],
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
        error = {"status": getattr(exc, "status", "ERROR_SKILL_HANDLER_FAILED"), "type": type(exc).__name__, "detail": str(exc)}
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
    output_artifacts = [
        artifact
        for artifact in (artifact_lineage.get("output"), artifact_lineage.get("failure_receipt"))
        if artifact is not None
    ]
    execution_contract = {
        "schema": "execution-contract/1.0",
        "execution_id": execution_id,
        "skill_id": skill.skill_id,
        "skill_version": skill.version,
        "skill_digest": skill.digest,
        "runtime": route,
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "executed_by": executed_by,
        "input_artifacts": [artifact_lineage["input"]],
        "output_artifacts": output_artifacts,
        "error": error,
    }
    try:
        validate_contract("execution_contract", execution_contract)
    except ContractViolation as exc:
        raise SkillRuntimeError("ERROR_SKILL_EXECUTION_CONTRACT", "; ".join(exc.errors)) from exc
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
        "execution_contract": execution_contract,
        "policy_decision": policy_decision,
        "activation_binding": {
            "mount_id": mount["mount_id"],
            "lifecycle_sequence": mount.get("lifecycle_sequence", 0),
            "admitted_at": admitted_at,
        },
        "artifact_lineage": artifact_lineage,
    }
    _append_execution(event, cfg)
    if status != "PASS":
        failure_status = error["status"] if error["status"] != "ERROR_SKILL_HANDLER_FAILED" else "ERROR_SKILL_EXECUTION_FAILED"
        raise SkillRuntimeError(failure_status, json.dumps(error, sort_keys=True))
    return {"status": "SKILL_EXECUTED", "execution": event, "result": result}


def list_skill_executions(cfg: Settings = settings) -> dict[str, Any]:
    return {"status": "SKILL_EXECUTIONS_OK", "executions": _load_executions(cfg)}
