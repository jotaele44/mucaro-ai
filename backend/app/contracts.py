"""Common governed contracts (TGT-016, INV-005).

One registry maps each canonical contract family to the JSON Schemas that
define it. Producers call ``validate_contract`` at the boundary where a record
is persisted or exported, so a subsystem cannot silently emit a record that
violates the shared contract: violations fail closed with ``ContractViolation``.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

import jsonschema

from .config import find_project_root

SCHEMA_DIR_NAME = "schemas"
# The schemas shipped with this code. Use it for records whose producers are application code: MUCARO_AI_ROOT can
# point find_project_root() at a data folder that has no schemas/ of its own.
APPLICATION_ROOT = Path(__file__).resolve().parents[2]

# record kind -> schema filename
RECORD_SCHEMAS: dict[str, str] = {
    "source_manifest": "source_manifest.schema.json",
    "provenance_record": "provenance_record.schema.json",
    "directory_grant": "directory_grant.schema.json",
    "capability_policy": "capability_policy.schema.json",
    "permission_decision": "permission_decision.schema.json",
    "tool_call": "tool_call.schema.json",
    "tool_audit_event": "tool_audit_event.schema.json",
    "governance_audit_event": "governance_audit_event.schema.json",
    "execution_contract": "execution_contract.schema.json",
    "run_manifest": "run_manifest.schema.json",
    "agent_checkpoint": "agent_checkpoint.schema.json",
    "agent_task_dag": "agent_task_dag.schema.json",
    "autopilot_state": "autopilot_state.schema.json",
    "permission_lease": "permission_lease.schema.json",
    "runtime_identity": "runtime_identity.schema.json",
    "artifact_identity": "artifact_identity.schema.json",
    "citation_record": "citation_record.schema.json",
    "answer_trace": "answer_trace.schema.json",
    "memory_item": "memory_item.schema.json",
    "memory_selection": "memory_selection.schema.json",
    "model_route_decision": "model_route_decision.schema.json",
    "model_runtime": "model_runtime.schema.json",
    "model_failover": "model_failover.schema.json",
    "release_receipt": "release_receipt.schema.json",
    "release_scope_manifest": "release_scope_manifest.schema.json",
    "release_acceptance_ledger": "release_acceptance_ledger.schema.json",
    "offline_independence_receipt": "offline_independence_receipt.schema.json",
}

# TGT-016 canonical contract family -> record kinds that implement it.
CONTRACT_FAMILIES: dict[str, tuple[str, ...]] = {
    "SOURCE": ("source_manifest",),
    "PERMISSION": (
        "directory_grant", "capability_policy", "permission_decision", "permission_lease", "tool_call", "tool_audit_event",
    ),
    "EXECUTION": (
        "execution_contract", "run_manifest", "agent_checkpoint", "agent_task_dag", "autopilot_state", "tool_call",
    ),
    "PROVENANCE": ("provenance_record", "runtime_identity", "governance_audit_event"),
    "ARTIFACT": ("artifact_identity",),
    "CITATION": ("citation_record", "answer_trace"),
    "MEMORY": ("memory_item", "memory_selection"),
    "MODEL_ROUTING": ("model_route_decision", "model_runtime", "model_failover"),
    "CERTIFICATION": (
        "release_receipt", "release_scope_manifest", "release_acceptance_ledger", "offline_independence_receipt",
    ),
}

# Schemas in schemas/ that are deliberately not part of the shared registry, each with the reason. Anything else in
# schemas/ must be registered above, so a new record type cannot quietly bypass the common contracts (INV-005).
DOMAIN_LOCAL_SCHEMAS: dict[str, str] = {
    "container_binding.schema.json": "not loaded by any producer; documents a shape only",
    "context_state.schema.json": "not loaded by any producer; documents a shape only",
    "context_usage.schema.json": "per-run token accounting shown in the UI; validated in context_usage",
    "document_chunk.schema.json": "not loaded by any producer; documents a shape only",
    "execution_gate.schema.json": "preflight gate rows; read by scripts/preflight.py, not a persisted record",
    "federation_skill.schema.json": "not loaded by any producer; documents a shape only",
    "flagpole_inventory.schema.json": "governance inventory file; validated by the flagpole inventory CLI",
    "local_worker_execution.schema.json": "not loaded by any producer; documents a shape only",
    "next_vector_proposal.schema.json": "UI next-step proposal; validated in next_vector",
    "offline_independence_external_evidence.schema.json": "operator-supplied input to the offline independence receipt",
    "prompt_template.schema.json": "not loaded by any producer; documents a shape only",
    "release_blocker_ledger.schema.json": "release governance ledger; validated by release_scope",
    "release_contradiction_ledger.schema.json": "release governance ledger; validated by release_scope",
    "release_dag.schema.json": "release governance ledger; validated by release_scope",
    "release_scope_delta_ledger.schema.json": "release governance ledger; validated by release_scope",
    "release_state_snapshot.schema.json": "release governance snapshot; validated by release_scope",
    "repo_capability.schema.json": "not loaded by any producer; documents a shape only",
    "retrieval_result.schema.json": "not loaded by any producer; documents a shape only",
    "self_build_receipt.schema.json": "not loaded by any producer; documents a shape only",
    "skill_mount.schema.json": "not loaded by any producer; documents a shape only",
    "video_cli_parity.schema.json": "video CLI parity governance matrix; validated in video_cli_parity",
    "video_vector_relay.schema.json": "video vector relay governance record; validated in video_vector_relay",
}


class ContractViolation(ValueError):
    def __init__(self, kind: str, errors: list[str]) -> None:
        super().__init__(f"ERROR_CONTRACT_VIOLATION:{kind}:{'; '.join(errors[:5])}")
        self.status = "ERROR_CONTRACT_VIOLATION"
        self.kind = kind
        self.errors = errors


def _schema_dir(root: Path | None = None) -> Path:
    return (root or find_project_root()) / SCHEMA_DIR_NAME


@cache
def _validator(kind: str, schema_dir: str) -> jsonschema.protocols.Validator:
    if kind not in RECORD_SCHEMAS:
        raise KeyError(f"unknown contract record kind: {kind}")
    schema = json.loads((Path(schema_dir) / RECORD_SCHEMAS[kind]).read_text(encoding="utf-8"))
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)
    return cls(schema)


def contract_validator(kind: str, *, root: Path | None = None) -> jsonschema.protocols.Validator:
    """The shared validator for a registered record kind, for callers that report errors in their own format."""
    return _validator(kind, str(_schema_dir(root)))


def registered_kind_for_schema(schema_name: str) -> str | None:
    return next((kind for kind, name in RECORD_SCHEMAS.items() if name == schema_name), None)


def contract_errors(kind: str, payload: Any, *, root: Path | None = None) -> list[str]:
    validator = _validator(kind, str(_schema_dir(root)))
    errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path))
    return [f"{'/'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}" for error in errors]


def validate_contract(kind: str, payload: Any, *, root: Path | None = None) -> Any:
    """Return payload unchanged if it satisfies the contract; raise ContractViolation otherwise."""
    errors = contract_errors(kind, payload, root=root)
    if errors:
        raise ContractViolation(kind, errors)
    return payload


def list_contracts(root: Path | None = None) -> dict[str, Any]:
    schema_dir = _schema_dir(root)
    families: list[dict[str, Any]] = []
    for family, kinds in CONTRACT_FAMILIES.items():
        families.append(
            {
                "family": family,
                "record_kinds": [
                    {
                        "kind": kind,
                        "schema": f"{SCHEMA_DIR_NAME}/{RECORD_SCHEMAS[kind]}",
                        "schema_present": (schema_dir / RECORD_SCHEMAS[kind]).is_file(),
                    }
                    for kind in kinds
                ],
            }
        )
    complete = all(row["schema_present"] for family in families for row in family["record_kinds"])
    return {"status": "CONTRACTS_OK" if complete else "CONTRACTS_INCOMPLETE", "families": families}
