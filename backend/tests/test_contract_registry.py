"""TGT-016 / INV-005: every governed record goes through the one shared contract registry.

Ratchets: every schema in schemas/ is either registered or declared domain-local with a reason, and a registered
schema is loaded only by the registry, so a subsystem cannot keep a private validator for a shared contract.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from backend.app.agent_checkpoints import CheckpointError, create_checkpoint, get_checkpoint
from backend.app.agent_task_dag import build_task_dag
from backend.app.autopilot import build_autopilot_state
from backend.app.config import Settings
from backend.app.contracts import (
    CONTRACT_FAMILIES,
    DOMAIN_LOCAL_SCHEMAS,
    RECORD_SCHEMAS,
    ContractViolation,
    validate_contract,
)
from backend.app.offline_independence import (
    OfflineIndependenceError,
    build_offline_independence_receipt,
    write_offline_independence_receipt,
)
from backend.app.permission_leases import grant_permission_lease
from backend.app.tools import TOOL_REGISTRY, ToolDefinition
from backend.tests.test_offline_independence import ARTIFACT, HEAD, contract, evidence, manifest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "backend" / "app"
NEWLY_SHARED = {
    "agent_checkpoint": "EXECUTION",
    "agent_task_dag": "EXECUTION",
    "autopilot_state": "EXECUTION",
    "permission_lease": "PERMISSION",
    "offline_independence_receipt": "CERTIFICATION",
}


def make_settings(root: Path) -> Settings:
    cfg = Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )
    cfg.ensure_dirs()
    return cfg


def test_every_schema_is_registered_or_declared_domain_local() -> None:
    on_disk = {path.name for path in (ROOT / "schemas").glob("*.schema.json")}
    registered = set(RECORD_SCHEMAS.values())
    domain_local = set(DOMAIN_LOCAL_SCHEMAS)
    assert on_disk - registered - domain_local == set(), "a new schema must be registered or declared domain-local"
    assert registered & domain_local == set()
    assert (registered | domain_local) - on_disk == set(), "stale registry or domain-local entry"
    assert all(reason.strip() for reason in DOMAIN_LOCAL_SCHEMAS.values())


def test_registered_schemas_are_loaded_only_through_the_registry() -> None:
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        if path.name == "contracts.py":
            continue
        text = path.read_text(encoding="utf-8")
        for schema_name in RECORD_SCHEMAS.values():
            if schema_name not in text:
                continue
            # release_scope names the ledger files it reads, and checks registered kinds with the registry's validator.
            if path.name == "release_scope.py" and "contract_validator(registered" in text:
                continue
            offenders.append(f"{path.relative_to(ROOT)}:{schema_name}")
    assert offenders == []


def test_newly_shared_records_belong_to_their_contract_family() -> None:
    for kind, family in NEWLY_SHARED.items():
        assert kind in RECORD_SCHEMAS
        assert kind in CONTRACT_FAMILIES[family]


def _real_records(tmp_path: Path) -> dict[str, dict]:
    cfg = make_settings(tmp_path)
    TOOL_REGISTRY["registry.read"] = ToolDefinition(
        "registry.read", "lease fixture", False, True, lambda a, c: {"status": "OK"}, autopilot_success_statuses=("OK",)
    )
    try:
        lease = grant_permission_lease(
            run_id="run-1", scope_type="EXACT_TOOLS", tool_names=["registry.read"], granted_by="operator", cfg=cfg
        )["lease"]
    finally:
        TOOL_REGISTRY.pop("registry.read", None)
    return {
        "agent_checkpoint": create_checkpoint(
            session_id="s", run_id="r", objective="registry", source_sha="a" * 40, state="EXECUTING", cfg=cfg
        ),
        "agent_task_dag": build_task_dag(
            [{"id": "inspect", "title": "Inspect"}, {"id": "edit", "title": "Edit", "depends_on": ["inspect"]}],
            created_at="2026-11-20T00:00:00Z",
        ),
        "autopilot_state": build_autopilot_state(),
        "permission_lease": lease,
        "offline_independence_receipt": build_offline_independence_receipt(
            contract=contract(), offline_manifest=manifest(), evidence=evidence(),
            platform="macos-arm64", head_sha=HEAD, artifact_sha256=ARTIFACT,
        ),
    }


def test_real_records_satisfy_the_shared_contracts_and_corruption_is_refused(tmp_path: Path) -> None:
    for kind, record in _real_records(tmp_path).items():
        assert validate_contract(kind, record) is record, kind
        broken = copy.deepcopy(record)
        broken["schema_version"] = "not-a-version"
        with pytest.raises(ContractViolation) as exc:
            validate_contract(kind, broken)
        assert exc.value.kind == kind


def test_producers_use_the_shipped_schemas_even_when_the_root_points_at_a_data_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_only = tmp_path / "data-only-root"
    data_only.mkdir()
    monkeypatch.setenv("MUCARO_AI_ROOT", str(data_only))
    cfg = make_settings(tmp_path / "workspace")
    created = create_checkpoint(session_id="s", run_id="r", objective="root", source_sha="a" * 40, state="EXECUTING", cfg=cfg)
    assert get_checkpoint(created["checkpoint_id"], cfg) == created


def test_a_checkpoint_that_breaks_the_shared_contract_keeps_its_own_error(tmp_path: Path) -> None:
    import json
    import sqlite3

    cfg = make_settings(tmp_path)
    created = create_checkpoint(session_id="s", run_id="r", objective="x", source_sha="a" * 40, state="EXECUTING", cfg=cfg)
    with sqlite3.connect(cfg.sqlite_path) as conn:
        conn.execute(
            "UPDATE agent_checkpoints SET payload_json = ? WHERE checkpoint_id = ?",
            (json.dumps({**created, "state": "NOT_A_STATE"}), created["checkpoint_id"]),
        )
    with pytest.raises(CheckpointError) as exc:
        get_checkpoint(created["checkpoint_id"], cfg)
    assert exc.value.status == "ERROR_CHECKPOINT_SCHEMA"
    assert exc.value.detail.startswith("state:")


def test_an_offline_receipt_that_breaks_the_contract_is_never_written(tmp_path: Path) -> None:
    receipt = build_offline_independence_receipt(
        contract=contract(), offline_manifest=manifest(), evidence=evidence(),
        platform="macos-arm64", head_sha=HEAD, artifact_sha256=ARTIFACT,
    )
    target = tmp_path / "receipt.json"
    with pytest.raises(OfflineIndependenceError) as exc:
        write_offline_independence_receipt({**receipt, "status": "TRUST_ME"}, target)
    assert exc.value.status == "ERROR_OFFLINE_INDEPENDENCE_RECEIPT_SCHEMA"
    assert not target.exists()
    assert write_offline_independence_receipt(receipt, target).exists()
