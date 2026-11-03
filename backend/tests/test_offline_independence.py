from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from backend.app.offline_independence import (
    OFFLINE_INDEPENDENCE_RECEIPT_SCHEMA,
    STATUS_BLOCKED,
    STATUS_CERTIFIED,
    OfflineIndependenceError,
    build_offline_independence_receipt,
)


ROOT = Path(__file__).resolve().parents[2]
HEAD = "a" * 40
ARTIFACT = "b" * 64
REQUIRED_CAPABILITIES = [
    "ingest.files",
    "retrieve.local",
    "answer.cited",
    "gis.vector_raster",
    "memory",
    "exports",
]
REQUIRED_GATES = [
    "artifact_verified",
    "install_completed_without_public_network",
    "first_launch_completed_without_public_network",
    "representative_offline_workflow_passed",
    "local_state_persisted_after_restart",
    "reinstall_preserved_local_state",
    "mandatory_runtime_dependencies_self_contained",
    "host_developer_tools_absent_or_unneeded",
    "public_network_isolation_proven",
    "unexpected_egress_absent",
    "remote_model_negative_control_failed_closed",
    "web_network_negative_control_failed_closed",
    "loopback_only_local_service",
    "source_identity_bound",
    "post_launch_bundle_immutable",
]


def contract() -> dict:
    return {
        "schema_version": "mucaro-runtime-independence/1.0",
        "goal": "ZERO_MANDATORY_EXTERNAL_RUNTIME_WORK_EXCEPT_EXPLICIT_NETWORK_OPERATIONS",
        "certification_profile": {
            "required_offline_capabilities": list(REQUIRED_CAPABILITIES),
            "required_external_evidence_gates": list(REQUIRED_GATES),
        },
    }


def manifest(*, unavailable: set[str] | None = None) -> dict:
    unavailable = unavailable or set()
    rows = []
    for capability in REQUIRED_CAPABILITIES:
        available = capability not in unavailable
        rows.append(
            {
                "capability": capability,
                "state_now": "AVAILABLE_LOCALLY" if available else "LOCAL_DEPENDENCY_MISSING",
                "runtime_network_required": False,
                "operator_external_setup_required": False,
                "release_packaging_residue": not available,
                "resolution_policy": "BUNDLED_ONLY",
            }
        )
    # Optional missing capability must not contaminate the core denominator.
    rows.append(
        {
            "capability": "ocr.image",
            "state_now": "LOCAL_DEPENDENCY_MISSING",
            "runtime_network_required": False,
            "operator_external_setup_required": False,
            "release_packaging_residue": True,
            "resolution_policy": "BUNDLED_ONLY",
        }
    )
    return {
        "status": "OFFLINE_MANIFEST_OK",
        "arithmetic_closes": True,
        "independence": {
            "network_exceptions": ["model:anthropic", "tool:web.fetch", "tool:web.search", "tool:web.render"]
        },
        "capabilities": rows,
    }


def evidence(**overrides) -> dict:
    result = {gate: {"passed": True, "source": f"fixture:{gate}"} for gate in REQUIRED_GATES}
    result.update(overrides)
    return result


def test_complete_evidence_emits_offline_independent() -> None:
    receipt = build_offline_independence_receipt(
        contract=contract(),
        offline_manifest=manifest(),
        evidence=evidence(),
        platform="macos-arm64",
        head_sha=HEAD,
        artifact_sha256=ARTIFACT,
    )

    assert receipt["schema_version"] == OFFLINE_INDEPENDENCE_RECEIPT_SCHEMA
    assert receipt["status"] == STATUS_CERTIFIED
    assert receipt["blocking"] == {
        "failed_capabilities": [],
        "failed_external_evidence_gates": [],
        "missing_external_evidence_gates": [],
    }
    assert receipt["counts"]["capabilities_passed"] == len(REQUIRED_CAPABILITIES)
    assert receipt["counts"]["external_evidence_gates_passed"] == len(REQUIRED_GATES)
    assert len(receipt["receipt_sha256"]) == 64


def test_missing_external_fact_fails_closed() -> None:
    rows = evidence()
    rows.pop("public_network_isolation_proven")
    receipt = build_offline_independence_receipt(
        contract=contract(),
        offline_manifest=manifest(),
        evidence=rows,
        platform="macos-arm64",
        head_sha=HEAD,
        artifact_sha256=ARTIFACT,
    )

    assert receipt["status"] == STATUS_BLOCKED
    assert receipt["blocking"]["missing_external_evidence_gates"] == ["public_network_isolation_proven"]
    assert "public_network_isolation_proven" in receipt["blocking"]["failed_external_evidence_gates"]


def test_failed_network_negative_control_blocks_claim() -> None:
    receipt = build_offline_independence_receipt(
        contract=contract(),
        offline_manifest=manifest(),
        evidence=evidence(
            web_network_negative_control_failed_closed={"passed": False, "observed": "request succeeded"}
        ),
        platform="macos-arm64",
        head_sha=HEAD,
        artifact_sha256=ARTIFACT,
    )

    assert receipt["status"] == STATUS_BLOCKED
    assert receipt["blocking"]["failed_external_evidence_gates"] == ["web_network_negative_control_failed_closed"]


def test_required_capability_residue_blocks_but_optional_residue_does_not() -> None:
    blocked = build_offline_independence_receipt(
        contract=contract(),
        offline_manifest=manifest(unavailable={"gis.vector_raster"}),
        evidence=evidence(),
        platform="macos-arm64",
        head_sha=HEAD,
        artifact_sha256=ARTIFACT,
    )
    assert blocked["status"] == STATUS_BLOCKED
    assert blocked["blocking"]["failed_capabilities"] == ["gis.vector_raster"]

    passed = build_offline_independence_receipt(
        contract=contract(),
        offline_manifest=manifest(),
        evidence=evidence(),
        platform="macos-arm64",
        head_sha=HEAD,
        artifact_sha256=ARTIFACT,
    )
    assert passed["status"] == STATUS_CERTIFIED
    assert all(row["capability"] != "ocr.image" for row in passed["capability_results"])


def test_platform_capability_adoption_extends_frozen_core_scope() -> None:
    extended_manifest = manifest()
    extended_manifest["capabilities"].append(
        {
            "capability": "ocr.image",
            "state_now": "AVAILABLE_LOCALLY",
            "runtime_network_required": False,
            "operator_external_setup_required": False,
            "release_packaging_residue": False,
            "resolution_policy": "BUNDLED_ONLY",
        }
    )
    # Remove the optional missing row with the same identity first.
    extended_manifest["capabilities"] = [
        row
        for row in extended_manifest["capabilities"]
        if row["capability"] != "ocr.image" or row["state_now"] == "AVAILABLE_LOCALLY"
    ]
    receipt = build_offline_independence_receipt(
        contract=contract(),
        offline_manifest=extended_manifest,
        evidence=evidence(),
        platform="macos-arm64",
        head_sha=HEAD,
        artifact_sha256=ARTIFACT,
        declared_capabilities=["ocr.image"],
    )

    assert receipt["status"] == STATUS_CERTIFIED
    assert receipt["scope"]["required_offline_capabilities"] == [*REQUIRED_CAPABILITIES, "ocr.image"]
    assert receipt["counts"]["required_capabilities"] == len(REQUIRED_CAPABILITIES) + 1


def test_platform_scope_cannot_replace_core_denominator() -> None:
    receipt = build_offline_independence_receipt(
        contract=contract(),
        offline_manifest=manifest(),
        evidence=evidence(),
        platform="macos-arm64",
        head_sha=HEAD,
        artifact_sha256=ARTIFACT,
        declared_capabilities=["ocr.image"],
    )

    assert receipt["status"] == STATUS_BLOCKED
    assert receipt["counts"]["required_capabilities"] == len(REQUIRED_CAPABILITIES) + 1
    assert receipt["blocking"]["failed_capabilities"] == ["ocr.image"]


def test_duplicate_or_missing_required_capability_fails_closed() -> None:
    duplicate = manifest()
    duplicate["capabilities"].append(dict(duplicate["capabilities"][0]))
    receipt = build_offline_independence_receipt(
        contract=contract(),
        offline_manifest=duplicate,
        evidence=evidence(),
        platform="macos-arm64",
        head_sha=HEAD,
        artifact_sha256=ARTIFACT,
    )
    assert receipt["status"] == STATUS_BLOCKED
    assert receipt["blocking"]["failed_capabilities"] == ["ingest.files"]


def test_invalid_manifest_or_identity_is_rejected() -> None:
    bad_manifest = manifest()
    bad_manifest["arithmetic_closes"] = False
    with pytest.raises(OfflineIndependenceError) as exc:
        build_offline_independence_receipt(
            contract=contract(),
            offline_manifest=bad_manifest,
            evidence=evidence(),
            platform="macos-arm64",
            head_sha=HEAD,
            artifact_sha256=ARTIFACT,
        )
    assert exc.value.status == "ERROR_OFFLINE_INDEPENDENCE_MANIFEST"

    with pytest.raises(OfflineIndependenceError) as exc:
        build_offline_independence_receipt(
            contract=contract(),
            offline_manifest=manifest(),
            evidence=evidence(),
            platform="macos-arm64",
            head_sha="ABC",
            artifact_sha256=ARTIFACT,
        )
    assert exc.value.status == "ERROR_OFFLINE_INDEPENDENCE_HEAD"


def test_external_evidence_template_and_receipt_match_schemas() -> None:
    evidence_schema = json.loads(
        (ROOT / "schemas" / "offline_independence_external_evidence.schema.json").read_text(encoding="utf-8")
    )
    receipt_schema = json.loads(
        (ROOT / "schemas" / "offline_independence_receipt.schema.json").read_text(encoding="utf-8")
    )
    template = json.loads(
        (ROOT / "_architecture" / "offline_independence_evidence_template_v1.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(evidence_schema).validate(template)

    receipt = build_offline_independence_receipt(
        contract=contract(),
        offline_manifest=manifest(),
        evidence=evidence(),
        platform="macos-arm64",
        head_sha=HEAD,
        artifact_sha256=ARTIFACT,
    )
    Draft202012Validator(receipt_schema).validate(receipt)


def test_receipt_hash_is_deterministic() -> None:
    kwargs = dict(
        contract=contract(),
        offline_manifest=manifest(),
        evidence=evidence(),
        platform="macos-arm64",
        head_sha=HEAD,
        artifact_sha256=ARTIFACT,
    )
    first = build_offline_independence_receipt(**kwargs)
    second = build_offline_independence_receipt(**kwargs)
    assert first == second
