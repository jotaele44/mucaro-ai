from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.app.contracts import validate_contract
from backend.app.permission_engine import ALLOW, DENY, PermissionEngineError, PermissionRequest, evaluate, require
from backend.app.permission_leases import (
    PermissionLeaseError,
    consume_permission_lease,
    create_permission_lease,
    list_permission_leases,
    revoke_permission_lease,
)
from backend.tests.test_api import make_settings


@pytest.fixture()
def cfg(tmp_path: Path):
    settings = make_settings(tmp_path)
    settings.ensure_dirs()
    return settings


def test_lease_payload_is_contract_valid_and_bound_to_run(cfg) -> None:
    lease = create_permission_lease("run-1", "subprocess.execute", "agent-1", cfg=cfg)
    validate_contract("permission_lease", lease)
    assert lease["run_id"] == "run-1"
    assert lease["state"] == "ACTIVE"
    assert list_permission_leases(cfg, run_id="run-1")[0]["effective_state"] == "ACTIVE"


def test_lease_authorizes_only_matching_execution_and_is_consumed_by_require(cfg) -> None:
    lease = create_permission_lease("run-1", "subprocess.execute", "agent-1", cfg=cfg)
    request = PermissionRequest(
        capability="subprocess.execute",
        subject="agent-1",
        origin="agent",
        run_id="run-1",
        lease_id=lease["lease_id"],
    )
    assert evaluate(request, cfg=cfg).decision == ALLOW
    require(request, cfg=cfg)
    with pytest.raises(PermissionEngineError) as exhausted:
        require(request, cfg=cfg)
    assert exhausted.value.decision.decision == DENY
    assert "EXHAUSTED" in exhausted.value.detail


def test_lease_binding_mismatch_fails_closed_before_consumption(cfg) -> None:
    lease = create_permission_lease("run-1", "subprocess.execute", "agent-1", cfg=cfg)
    mismatched = PermissionRequest(
        capability="subprocess.execute",
        subject="agent-2",
        origin="agent",
        run_id="run-1",
        lease_id=lease["lease_id"],
    )
    decision = evaluate(mismatched, cfg=cfg)
    assert decision.decision == DENY
    assert "identity" in decision.reasons[0]
    assert list_permission_leases(cfg)[0]["uses"] == 0


def test_revoked_lease_cannot_authorize_execution(cfg) -> None:
    lease = create_permission_lease("run-1", "filesystem.write", "agent-1", target=str(cfg.project_root / "x"), cfg=cfg)
    revoke_permission_lease(lease["lease_id"], cfg)
    request = PermissionRequest(
        capability="filesystem.write",
        subject="agent-1",
        target=str(cfg.project_root / "x"),
        origin="agent",
        run_id="run-1",
        lease_id=lease["lease_id"],
    )
    decision = evaluate(request, cfg=cfg)
    assert decision.decision == DENY
    assert "REVOKED" in decision.reasons[0]


def test_expiration_is_persisted_when_consumption_is_rejected(cfg) -> None:
    lease = create_permission_lease("run-1", "subprocess.execute", "agent-1", cfg=cfg)
    with __import__("sqlite3").connect(cfg.sqlite_path) as conn:
        payload = json.loads(conn.execute("SELECT payload_json FROM permission_leases WHERE lease_id = ?", (lease["lease_id"],)).fetchone()[0])
        payload["expires_at"] = "2000-01-01T00:00:00Z"
        conn.execute("UPDATE permission_leases SET payload_json = ? WHERE lease_id = ?", (json.dumps(payload), lease["lease_id"]))
    with pytest.raises(PermissionLeaseError) as expired:
        consume_permission_lease(
            lease["lease_id"], run_id="run-1", capability="subprocess.execute", subject="agent-1", cfg=cfg
        )
    assert expired.value.status == "ERROR_LEASE_EXPIRED"
    stored = list_permission_leases(cfg)[0]
    assert stored["state"] == "EXPIRED"
    assert stored["effective_state"] == "EXPIRED"


def test_atomic_consumption_allows_exactly_max_uses_under_concurrency(cfg) -> None:
    lease = create_permission_lease("run-1", "subprocess.execute", "agent-1", max_uses=2, cfg=cfg)

    def consume() -> str:
        try:
            consume_permission_lease(
                lease["lease_id"], run_id="run-1", capability="subprocess.execute", subject="agent-1", cfg=cfg
            )
            return "success"
        except PermissionLeaseError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(lambda _: consume(), range(6)))
    assert outcomes.count("success") == 2
    assert outcomes.count("rejected") == 4
    final = list_permission_leases(cfg)[0]
    assert final["uses"] == 2
    assert final["effective_state"] == "EXHAUSTED"
