"""TGT-035: source acquisition and permission changes land in one hash-chained stream without private content."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.contracts import ContractViolation, validate_contract
from backend.app.governance_audit import (
    GovernanceAuditError,
    governance_audit_path,
    load_governance_events,
    record_governance_event,
    verify_governance_audit_chain,
)
from backend.app.ingestion import delete_source, ingest_file
from backend.app.model_settings import save_model_settings
from backend.app.permission_leases import grant_permission_lease, revoke_permission_lease
from backend.app.permissions import grant_directory, revoke_directory
from backend.app.web_browsing import WebFetchPolicy, fetch_web_snapshot
from backend.tests.test_web_browsing import _serve_fixture

PRIVATE_TEXT = "the quarterly figures are confidential 7731"
FAKE_KEY = "sk-test-" + "z" * 32


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


@pytest.fixture()
def cfg(tmp_path: Path) -> Settings:
    return make_settings(tmp_path / "workspace")


def _events(cfg: Settings, kind: str) -> list[dict]:
    return [event for event in load_governance_events(cfg) if event["event"] == kind]


def test_ingest_and_delete_are_recorded_with_identity_and_hash_but_not_text(cfg: Settings, tmp_path: Path) -> None:
    note = tmp_path / "note.txt"
    note.write_text(PRIVATE_TEXT + "\n", encoding="utf-8")
    source = ingest_file(note, cfg)["source"]

    [ingested] = _events(cfg, "SOURCE_INGESTED")
    assert ingested["details"]["source_id"] == source["source_id"]
    assert ingested["details"]["source_sha256"] == source["source_sha256"]
    assert ingested["details"]["source_kind"] == "LOCAL_FILE"
    assert ingested["details"]["chunk_count"] == source["chunk_count"] >= 1

    delete_source(source["source_id"], cfg)
    [deleted] = _events(cfg, "SOURCE_DELETED")
    assert deleted["details"]["source_id"] == source["source_id"]
    assert deleted["details"]["removed_chunks"] >= 1

    raw = governance_audit_path(cfg).read_text(encoding="utf-8")
    assert PRIVATE_TEXT not in raw and "confidential" not in raw
    assert verify_governance_audit_chain(cfg)["status"] == "GOVERNANCE_AUDIT_CHAIN_OK"


def test_folder_grants_and_revocations_are_recorded(cfg: Settings, tmp_path: Path) -> None:
    folder = tmp_path / "shared"
    folder.mkdir()
    grant = grant_directory(folder, cfg, granted_by="api", reason="project files")
    revoke_directory(folder, cfg)
    revoke_directory(folder, cfg)  # nothing left to revoke: not recorded as a change

    [granted] = _events(cfg, "DIRECTORY_GRANTED")
    assert granted["actor"] == "api"
    assert granted["details"] == {"canonical_root": grant["canonical_root"], "reason_given": True}
    [revoked] = _events(cfg, "DIRECTORY_REVOKED")
    assert revoked["details"]["canonical_root"] == grant["canonical_root"]


def test_lease_grant_and_revocation_are_in_the_same_stream(cfg: Settings) -> None:
    lease = grant_permission_lease(
        run_id="run-1",
        scope_type="EXACT_TOOLS",
        tool_names=["source_library.status"],
        allow_mutating=False,
        allow_network=False,
        granted_by="operator",
        cfg=cfg,
    )["lease"]
    revoke_permission_lease(lease["lease_id"], revoked_by="operator", cfg=cfg)

    kinds = [event["event"] for event in load_governance_events(cfg)]
    assert kinds == ["LEASE_GRANTED", "LEASE_REVOKED"]
    assert {event["details"]["grant_sha256"] for event in load_governance_events(cfg)} == {lease["grant_sha256"]}


def test_model_settings_changes_name_the_fields_and_never_the_key(cfg: Settings) -> None:
    save_model_settings({"remote": {"base_url": "https://api.anthropic.com", "model": "claude-x", "api_key": FAKE_KEY}}, cfg)
    save_model_settings({"remote": {"api_key": FAKE_KEY}}, cfg)  # unchanged: not recorded
    save_model_settings({"remote": {"api_key": ""}}, cfg)

    first, second = _events(cfg, "MODEL_SETTINGS_CHANGED")
    fields = {(change["field"], change.get("change")) for change in first["details"]["changes"]}
    assert ("base_url", None) in fields and ("model", None) in fields and ("credential", "SET") in fields
    assert second["details"]["changes"] == [{"section": "remote", "field": "credential", "change": "CLEARED"}]
    assert FAKE_KEY not in governance_audit_path(cfg).read_text(encoding="utf-8")


def test_web_snapshot_is_recorded_with_host_only(cfg: Settings) -> None:
    server, url = _serve_fixture()
    try:
        result = fetch_web_snapshot(url + "?session=abc123", cfg=cfg, policy=WebFetchPolicy(timeout_seconds=5, max_bytes=1000, allow_private_hosts=True))
    finally:
        server.shutdown()

    [persisted] = _events(cfg, "WEB_SNAPSHOT_PERSISTED")
    assert persisted["details"]["snapshot_sha256"] == result["snapshot_sha256"]
    assert persisted["details"]["host"] == "127.0.0.1"
    raw = governance_audit_path(cfg).read_text(encoding="utf-8")
    assert "session=abc123" not in raw and "/fixture.html" not in raw


@pytest.mark.parametrize(
    ("tamper", "reason"),
    [
        (lambda events: events[0]["details"].update(canonical_root="/elsewhere"), "EVENT_HASH_MISMATCH"),
        (lambda events: events.pop(0), "PREVIOUS_HASH_MISMATCH"),
        (lambda events: events.reverse(), "PREVIOUS_HASH_MISMATCH"),
        (lambda events: events[1].pop("event_sha256"), "UNHASHED_EVENT"),
    ],
)
def test_edited_removed_or_reordered_events_break_the_chain(cfg: Settings, tmp_path: Path, tamper, reason: str) -> None:
    for name in ("a", "b", "c"):
        folder = tmp_path / name
        folder.mkdir()
        grant_directory(folder, cfg, granted_by="api")
    assert verify_governance_audit_chain(cfg) == {
        "status": "GOVERNANCE_AUDIT_CHAIN_OK",
        "event_count": 3,
        "head_sha256": load_governance_events(cfg)[-1]["event_sha256"],
    }

    path = governance_audit_path(cfg)
    events = json.loads(path.read_text(encoding="utf-8"))
    tamper(events)
    path.write_text(json.dumps(events), encoding="utf-8")
    result = verify_governance_audit_chain(cfg)
    assert result["status"] == "GOVERNANCE_AUDIT_CHAIN_BROKEN"
    assert result["reason"] == reason


def test_private_fields_and_unknown_events_are_refused(cfg: Settings) -> None:
    with pytest.raises(GovernanceAuditError) as leaked:
        record_governance_event("MODEL_SETTINGS_CHANGED", "user", {"changes": [{"api_key": FAKE_KEY}]}, cfg)
    assert leaked.value.status == "ERROR_GOVERNANCE_EVENT_PRIVATE_FIELD"
    with pytest.raises(GovernanceAuditError) as unknown:
        record_governance_event("SOMETHING_ELSE", "user", {}, cfg)
    assert unknown.value.status == "ERROR_GOVERNANCE_EVENT_UNKNOWN"
    assert not governance_audit_path(cfg).exists()

    good = record_governance_event("DIRECTORY_REVOKED", "user", {"canonical_root": "/x", "removed": 1}, cfg)
    with pytest.raises(ContractViolation):
        validate_contract("governance_audit_event", {**good, "event": "SOURCE_RENAMED"})
    with pytest.raises(ContractViolation):
        validate_contract("governance_audit_event", {**good, "extra": "field"})


def test_audit_route_returns_events_and_every_chain(cfg: Settings, tmp_path: Path) -> None:
    from backend.app.main import create_app

    folder = tmp_path / "routes"
    folder.mkdir()
    client = TestClient(create_app(cfg))
    assert client.post("/permissions/directories/grant", json={"path": str(folder)}).status_code == 200
    payload = client.get("/audit").json()
    assert payload["status"] == "GOVERNANCE_AUDIT_OK"
    assert [event["event"] for event in payload["events"]] == ["DIRECTORY_GRANTED"]
    assert payload["chains"]["governance"]["status"] == "GOVERNANCE_AUDIT_CHAIN_OK"
    assert payload["chains"]["tools"]["status"] == "TOOL_AUDIT_CHAIN_OK"
    assert payload["chains"]["permission_leases"]["status"].endswith("_OK")
    assert client.get("/audit", params={"event": "SOURCE_INGESTED"}).json()["events"] == []
