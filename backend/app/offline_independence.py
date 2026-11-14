from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

OFFLINE_INDEPENDENCE_RECEIPT_SCHEMA = "mucaro-offline-independence-receipt/1.0"
STATUS_CERTIFIED = "OFFLINE_INDEPENDENT"
STATUS_BLOCKED = "OFFLINE_INDEPENDENCE_BLOCKED"
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class OfflineIndependenceError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _load_object(path: str | Path, *, status: str) -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OfflineIndependenceError(status, str(target)) from exc
    if not isinstance(payload, dict):
        raise OfflineIndependenceError(status, str(target))
    return payload


def load_runtime_independence_contract(path: str | Path) -> dict[str, Any]:
    payload = _load_object(path, status="ERROR_OFFLINE_INDEPENDENCE_CONTRACT")
    if payload.get("schema_version") != "mucaro-runtime-independence/1.0":
        raise OfflineIndependenceError(
            "ERROR_OFFLINE_INDEPENDENCE_CONTRACT",
            "runtime independence contract schema mismatch",
        )
    return payload


def _evidence_gate(row: Any) -> tuple[bool, dict[str, Any]]:
    if isinstance(row, bool):
        return row, {"passed": row}
    if isinstance(row, dict) and isinstance(row.get("passed"), bool):
        return bool(row["passed"]), dict(row)
    return False, {"passed": False, "reason": "EVIDENCE_MISSING_OR_INVALID"}


def _receipt_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_offline_independence_receipt(
    *,
    contract: Mapping[str, Any],
    offline_manifest: Mapping[str, Any],
    evidence: Mapping[str, Any],
    platform: str,
    head_sha: str,
    artifact_sha256: str,
    declared_capabilities: list[str] | None = None,
) -> dict[str, Any]:
    """Evaluate a prepared release without inventing external evidence.

    This function does not attempt to prove that networking was disabled or that
    a machine was clean. Those facts must come from the external harness under
    named evidence gates. Missing or malformed evidence fails closed.
    """

    if contract.get("schema_version") != "mucaro-runtime-independence/1.0":
        raise OfflineIndependenceError("ERROR_OFFLINE_INDEPENDENCE_CONTRACT", "contract schema mismatch")
    if not isinstance(platform, str) or not platform.strip():
        raise OfflineIndependenceError("ERROR_OFFLINE_INDEPENDENCE_PLATFORM", "platform is required")
    if not _SHA40.fullmatch(head_sha):
        raise OfflineIndependenceError("ERROR_OFFLINE_INDEPENDENCE_HEAD", "head_sha must be lowercase 40-character SHA")
    if not _SHA256.fullmatch(artifact_sha256):
        raise OfflineIndependenceError(
            "ERROR_OFFLINE_INDEPENDENCE_ARTIFACT_SHA256",
            "artifact_sha256 must be lowercase 64-character SHA-256",
        )

    profile = contract.get("certification_profile")
    if not isinstance(profile, dict):
        raise OfflineIndependenceError(
            "ERROR_OFFLINE_INDEPENDENCE_CONTRACT",
            "certification_profile is required",
        )
    core_capabilities = profile.get("required_offline_capabilities")
    required_gates = profile.get("required_external_evidence_gates")
    if (
        not isinstance(core_capabilities, list)
        or not core_capabilities
        or not all(isinstance(value, str) and value for value in core_capabilities)
    ):
        raise OfflineIndependenceError(
            "ERROR_OFFLINE_INDEPENDENCE_CONTRACT",
            "required_offline_capabilities must be a non-empty string list",
        )
    if len(core_capabilities) != len(set(core_capabilities)):
        raise OfflineIndependenceError(
            "ERROR_OFFLINE_INDEPENDENCE_CONTRACT",
            "required_offline_capabilities must be unique",
        )
    if (
        not isinstance(required_gates, list)
        or not required_gates
        or not all(isinstance(value, str) and value for value in required_gates)
    ):
        raise OfflineIndependenceError(
            "ERROR_OFFLINE_INDEPENDENCE_CONTRACT",
            "required_external_evidence_gates must be a non-empty string list",
        )
    if len(required_gates) != len(set(required_gates)):
        raise OfflineIndependenceError(
            "ERROR_OFFLINE_INDEPENDENCE_CONTRACT",
            "required_external_evidence_gates must be unique",
        )

    required_capabilities = list(core_capabilities)
    if declared_capabilities is not None:
        if not all(isinstance(value, str) and value for value in declared_capabilities):
            raise OfflineIndependenceError(
                "ERROR_OFFLINE_INDEPENDENCE_SCOPE",
                "declared capabilities must be non-empty strings",
            )
        required_capabilities.extend(value for value in declared_capabilities if value not in required_capabilities)

    capability_rows = offline_manifest.get("capabilities")
    if not isinstance(capability_rows, list) or offline_manifest.get("arithmetic_closes") is not True:
        raise OfflineIndependenceError(
            "ERROR_OFFLINE_INDEPENDENCE_MANIFEST",
            "offline capability manifest must be complete and arithmetically closed",
        )
    indexed: dict[str, list[dict[str, Any]]] = {}
    for row in capability_rows:
        if isinstance(row, dict) and isinstance(row.get("capability"), str):
            indexed.setdefault(row["capability"], []).append(row)

    capability_results: list[dict[str, Any]] = []
    for capability in required_capabilities:
        rows = indexed.get(capability, [])
        if len(rows) != 1:
            capability_results.append(
                {
                    "capability": capability,
                    "passed": False,
                    "state_now": "MISSING_OR_DUPLICATED",
                    "operator_external_setup_required": None,
                }
            )
            continue
        row = rows[0]
        passed = (
            row.get("state_now") == "AVAILABLE_LOCALLY"
            and row.get("runtime_network_required") is False
            and row.get("operator_external_setup_required") is False
        )
        capability_results.append(
            {
                "capability": capability,
                "passed": passed,
                "state_now": row.get("state_now"),
                "runtime_network_required": row.get("runtime_network_required"),
                "operator_external_setup_required": row.get("operator_external_setup_required"),
                "release_packaging_residue": row.get("release_packaging_residue"),
                "resolution_policy": row.get("resolution_policy"),
            }
        )

    gate_results: dict[str, dict[str, Any]] = {}
    for gate in required_gates:
        passed, row = _evidence_gate(evidence.get(gate))
        row["passed"] = passed
        gate_results[gate] = row

    missing_evidence = sorted(
        gate for gate, row in gate_results.items() if row.get("reason") == "EVIDENCE_MISSING_OR_INVALID"
    )
    failed_evidence = sorted(gate for gate, row in gate_results.items() if row.get("passed") is not True)
    failed_capabilities = sorted(row["capability"] for row in capability_results if not row["passed"])

    manifest_independence = offline_manifest.get("independence")
    network_exceptions = []
    if isinstance(manifest_independence, dict) and isinstance(manifest_independence.get("network_exceptions"), list):
        network_exceptions = sorted(str(value) for value in manifest_independence["network_exceptions"])

    all_pass = not failed_evidence and not failed_capabilities
    receipt: dict[str, Any] = {
        "schema_version": OFFLINE_INDEPENDENCE_RECEIPT_SCHEMA,
        "status": STATUS_CERTIFIED if all_pass else STATUS_BLOCKED,
        "platform": platform.strip(),
        "head_sha": head_sha,
        "artifact_sha256": artifact_sha256,
        "goal": contract.get("goal"),
        "scope": {
            "required_offline_capabilities": list(required_capabilities),
            "required_external_evidence_gates": list(required_gates),
            "explicit_network_exceptions": network_exceptions,
        },
        "capability_results": capability_results,
        "evidence": gate_results,
        "counts": {
            "required_capabilities": len(capability_results),
            "capabilities_passed": sum(1 for row in capability_results if row["passed"]),
            "required_external_evidence_gates": len(gate_results),
            "external_evidence_gates_passed": sum(1 for row in gate_results.values() if row["passed"]),
        },
        "blocking": {
            "failed_capabilities": failed_capabilities,
            "failed_external_evidence_gates": failed_evidence,
            "missing_external_evidence_gates": missing_evidence,
        },
        "invariants": {
            "offline_manifest_arithmetic_closed": True,
            "required_capabilities_unique": len(required_capabilities) == len(set(required_capabilities)),
            "required_gates_unique": len(required_gates) == len(set(required_gates)),
            "no_failed_required_capabilities": not failed_capabilities,
            "no_failed_required_external_evidence": not failed_evidence,
        },
        "basis": (
            "Deterministic evaluation of a bound runtime-independence contract, an offline capability manifest, "
            "and externally supplied clean-machine/network-isolation evidence."
        ),
        "limitations": [
            "This evaluator does not itself disable public networking or prove host cleanliness.",
            "Optional capabilities outside the declared release scope do not block the core independence claim.",
            "Explicit API/fetch/search/render network capabilities are exceptions only when the operator "
            "authorizes them.",
        ],
    }
    unsigned = dict(receipt)
    receipt["receipt_sha256"] = _receipt_hash(unsigned)
    return receipt


def write_offline_independence_receipt(receipt: Mapping[str, Any], path: str | Path) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dict(receipt), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target
