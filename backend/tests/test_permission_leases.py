from __future__ import annotations

import json
from pathlib import Path

import pytest

import backend.app.permission_leases as permission_leases
from backend.app.config import Settings
from backend.app.permission_leases import (
    PermissionLeaseError,
    authorize_tool_call_with_lease,
    grant_permission_lease,
    list_permission_leases,
    matching_permission_leases,
    revoke_permission_lease,
    verify_permission_lease_audit_chain,
)
from backend.app.tools import TOOL_REGISTRY, ToolDefinition, ToolRuntimeError, arguments_digest, list_tool_calls, request_tool_call


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


def tool(name: str, *, mutates: bool = False, network: str = "none") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="permission lease fixture",
        mutates_state=mutates,
        requires_approval=True,
        network=network,
        handler=lambda arguments, cfg: {"status": "OK"},
        autopilot_success_statuses=("OK",),
    )


def pending(name: str, *, run_id: str, cfg: Settings, arguments=None) -> dict:
    return request_tool_call(
        name,
        dict(arguments or {}),
        run_id=run_id,
        requested_by="autopilot",
        reason=f"fixture:{name}",
        cfg=cfg,
    )["tool_call"]


def test_exact_mutating_lease_approves_only_the_bound_run_call(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    monkeypatch.setitem(TOOL_REGISTRY, "test.mutate", tool("test.mutate", mutates=True))
    lease = grant_permission_lease(
        run_id="run-1",
        scope_type="EXACT_TOOLS",
        tool_names=["test.mutate"],
        allow_mutating=True,
        granted_by="operator",
        max_uses=2,
        cfg=cfg,
    )["lease"]
    call = pending("test.mutate", run_id="run-1", cfg=cfg, arguments={"value": 1})

    matches = matching_permission_leases(call["tool_call_id"], cfg=cfg)
    assert matches["matching_lease_ids"] == [lease["lease_id"]]
    approved = authorize_tool_call_with_lease(call["tool_call_id"], cfg=cfg)

    assert approved["status"] == "TOOL_CALL_APPROVED_BY_LEASE"
    assert approved["tool_call"]["status"] == "APPROVED"
    assert approved["tool_call"]["approved_by"] == f"lease:{lease['lease_id']}"
    stored = list_permission_leases(run_id="run-1", cfg=cfg)["leases"][0]
    assert stored["used_call_ids"] == [call["tool_call_id"]]
    assert stored["effective_state"] == "ACTIVE"
    assert verify_permission_lease_audit_chain(cfg)["status"] == "PERMISSION_LEASE_AUDIT_OK"

    other = pending("test.mutate", run_id="run-2", cfg=cfg)
    assert matching_permission_leases(other["tool_call_id"], cfg=cfg)["match_count"] == 0


def test_mutation_and_network_require_independent_lease_flags(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    monkeypatch.setitem(TOOL_REGISTRY, "test.remote_mutate", tool("test.remote_mutate", mutates=True, network="remote"))
    call = pending("test.remote_mutate", run_id="run-1", cfg=cfg)

    grant_permission_lease(
        run_id="run-1",
        scope_type="EXACT_TOOLS",
        tool_names=["test.remote_mutate"],
        allow_mutating=True,
        allow_network=False,
        granted_by="operator",
        cfg=cfg,
    )
    assert matching_permission_leases(call["tool_call_id"], cfg=cfg)["match_count"] == 0

    grant_permission_lease(
        run_id="run-1",
        scope_type="EXACT_TOOLS",
        tool_names=["test.remote_mutate"],
        allow_mutating=False,
        allow_network=True,
        granted_by="operator",
        cfg=cfg,
    )
    assert matching_permission_leases(call["tool_call_id"], cfg=cfg)["match_count"] == 0

    grant_permission_lease(
        run_id="run-1",
        scope_type="EXACT_TOOLS",
        tool_names=["test.remote_mutate"],
        allow_mutating=True,
        allow_network=True,
        granted_by="operator",
        cfg=cfg,
    )
    assert matching_permission_leases(call["tool_call_id"], cfg=cfg)["match_count"] == 1


def test_workspace_binding_is_exact_after_path_canonicalization(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    monkeypatch.setitem(TOOL_REGISTRY, "test.workspace", tool("test.workspace"))
    allowed = tmp_path / "allowed"
    other = tmp_path / "other"
    allowed.mkdir()
    other.mkdir()

    grant_permission_lease(
        run_id="run-1",
        scope_type="EXACT_TOOLS",
        tool_names=["test.workspace"],
        workspace_root=str(allowed),
        granted_by="operator",
        cfg=cfg,
    )
    wrong = pending("test.workspace", run_id="run-1", cfg=cfg, arguments={"repo_root": str(other)})
    assert matching_permission_leases(wrong["tool_call_id"], cfg=cfg)["match_count"] == 0

    right = pending("test.workspace", run_id="run-1", cfg=cfg, arguments={"repo_root": str(allowed / ".")})
    assert matching_permission_leases(right["tool_call_id"], cfg=cfg)["match_count"] == 1


def test_overlapping_leases_fail_closed_instead_of_arbitrary_selection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    # A prefix lease only reaches read-only local inspection tools, so the overlap uses a real one.
    first = grant_permission_lease(
        run_id="run-1",
        scope_type="EXACT_TOOLS",
        tool_names=["coding.repo_status"],
        granted_by="operator-a",
        cfg=cfg,
    )["lease"]
    second = grant_permission_lease(
        run_id="run-1",
        scope_type="TOOL_PREFIXES",
        tool_prefixes=["coding.repo_"],
        granted_by="operator-b",
        cfg=cfg,
    )["lease"]
    assert first["lease_id"] != second["lease_id"]
    call = pending("coding.repo_status", run_id="run-1", cfg=cfg)
    matches = matching_permission_leases(call["tool_call_id"], cfg=cfg)
    assert set(matches["matching_lease_ids"]) == {first["lease_id"], second["lease_id"]}

    with pytest.raises(PermissionLeaseError) as exc:
        authorize_tool_call_with_lease(call["tool_call_id"], cfg=cfg)
    assert exc.value.status == "ERROR_PERMISSION_LEASE_AMBIGUOUS"
    assert list_tool_calls(cfg)["tool_calls"][0]["status"] == "PENDING_APPROVAL"


def test_revoke_is_idempotent_and_revoked_lease_no_longer_matches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    monkeypatch.setitem(TOOL_REGISTRY, "test.read", tool("test.read"))
    lease = grant_permission_lease(
        run_id="run-1",
        scope_type="EXACT_TOOLS",
        tool_names=["test.read"],
        granted_by="operator",
        cfg=cfg,
    )["lease"]
    first = revoke_permission_lease(lease["lease_id"], revoked_by="operator", cfg=cfg)
    second = revoke_permission_lease(lease["lease_id"], revoked_by="operator", cfg=cfg)

    assert first["changed"] is True
    assert second["changed"] is False
    call = pending("test.read", run_id="run-1", cfg=cfg)
    assert matching_permission_leases(call["tool_call_id"], cfg=cfg)["match_count"] == 0
    assert verify_permission_lease_audit_chain(cfg)["status"] == "PERMISSION_LEASE_AUDIT_OK"


def test_use_ceiling_is_enforced_after_reservation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    monkeypatch.setitem(TOOL_REGISTRY, "test.read", tool("test.read"))
    lease = grant_permission_lease(
        run_id="run-1",
        scope_type="EXACT_TOOLS",
        tool_names=["test.read"],
        granted_by="operator",
        max_uses=1,
        cfg=cfg,
    )["lease"]
    first = pending("test.read", run_id="run-1", cfg=cfg)
    authorize_tool_call_with_lease(first["tool_call_id"], cfg=cfg)

    stored = list_permission_leases(run_id="run-1", cfg=cfg)["leases"][0]
    assert stored["lease_id"] == lease["lease_id"]
    assert stored["effective_state"] == "EXHAUSTED"
    second = pending("test.read", run_id="run-1", cfg=cfg)
    assert matching_permission_leases(second["tool_call_id"], cfg=cfg)["match_count"] == 0


def test_tampering_used_call_ids_is_detected_against_audit_chain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    monkeypatch.setitem(TOOL_REGISTRY, "test.read", tool("test.read"))
    grant_permission_lease(
        run_id="run-1",
        scope_type="EXACT_TOOLS",
        tool_names=["test.read"],
        granted_by="operator",
        cfg=cfg,
    )
    call = pending("test.read", run_id="run-1", cfg=cfg)
    authorize_tool_call_with_lease(call["tool_call_id"], cfg=cfg)

    path = cfg.processed_dir / "permission_leases.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    rows[0]["used_call_ids"] = []
    path.write_text(json.dumps(rows), encoding="utf-8")

    with pytest.raises(PermissionLeaseError) as exc:
        list_permission_leases(cfg=cfg)
    assert exc.value.status == "ERROR_PERMISSION_LEASE_USAGE_AUDIT_MISMATCH"


def test_run_read_only_never_authorizes_mutation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    monkeypatch.setitem(TOOL_REGISTRY, "test.read", tool("test.read"))
    monkeypatch.setitem(TOOL_REGISTRY, "test.write", tool("test.write", mutates=True))
    grant_permission_lease(
        run_id="run-1",
        scope_type="RUN_READ_ONLY",
        granted_by="operator",
        allow_network=False,
        cfg=cfg,
    )
    read = pending("product.audit", run_id="run-1", cfg=cfg)
    unlisted = pending("test.read", run_id="run-1", cfg=cfg)
    write = pending("test.write", run_id="run-1", cfg=cfg)

    assert matching_permission_leases(read["tool_call_id"], cfg=cfg)["match_count"] == 1
    # Non-mutating is not enough: a tool outside the read-only local inspection set is never covered implicitly.
    assert matching_permission_leases(unlisted["tool_call_id"], cfg=cfg)["match_count"] == 0
    assert matching_permission_leases(write["tool_call_id"], cfg=cfg)["match_count"] == 0


def test_argument_hash_binding_matches_only_the_exact_argument_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = make_settings(tmp_path)
    monkeypatch.setitem(TOOL_REGISTRY, "test.mutate", tool("test.mutate", mutates=True))
    approved_arguments = {"value": 1, "target": "alpha"}
    digest = arguments_digest(approved_arguments)
    lease = grant_permission_lease(
        run_id="run-1",
        scope_type="EXACT_TOOLS",
        tool_names=["test.mutate"],
        arguments_sha256=digest,
        allow_mutating=True,
        granted_by="operator",
        max_uses=2,
        cfg=cfg,
    )["lease"]
    assert lease["arguments_sha256"] == digest

    exact = pending("test.mutate", run_id="run-1", cfg=cfg, arguments=approved_arguments)
    changed = pending("test.mutate", run_id="run-1", cfg=cfg, arguments={"value": 2, "target": "alpha"})

    assert matching_permission_leases(exact["tool_call_id"], cfg=cfg)["matching_lease_ids"] == [lease["lease_id"]]
    assert matching_permission_leases(changed["tool_call_id"], cfg=cfg)["match_count"] == 0


def test_invalid_argument_hash_binding_fails_before_lease_persistence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = make_settings(tmp_path)
    monkeypatch.setitem(TOOL_REGISTRY, "test.read", tool("test.read"))

    with pytest.raises(PermissionLeaseError) as exc:
        grant_permission_lease(
            run_id="run-1",
            scope_type="EXACT_TOOLS",
            tool_names=["test.read"],
            arguments_sha256="not-a-sha",
            granted_by="operator",
            cfg=cfg,
        )
    assert exc.value.status == "ERROR_PERMISSION_LEASE_ARGUMENT_HASH"
    assert not (cfg.processed_dir / "permission_leases.json").exists()


def test_grant_audit_failure_rolls_back_lease_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = make_settings(tmp_path)
    monkeypatch.setitem(TOOL_REGISTRY, "test.read", tool("test.read"))

    def fail_audit(event, settings):
        raise OSError("audit storage unavailable")

    monkeypatch.setattr(permission_leases, "_append_audit", fail_audit)

    with pytest.raises(OSError):
        grant_permission_lease(
            run_id="run-1",
            scope_type="EXACT_TOOLS",
            tool_names=["test.read"],
            granted_by="operator",
            cfg=cfg,
        )

    lease_path = cfg.processed_dir / "permission_leases.json"
    assert json.loads(lease_path.read_text(encoding="utf-8")) == []
    call = pending("test.read", run_id="run-1", cfg=cfg)
    assert matching_permission_leases(call["tool_call_id"], cfg=cfg)["match_count"] == 0


def test_failed_downstream_approval_releases_reserved_use(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = make_settings(tmp_path)
    monkeypatch.setitem(TOOL_REGISTRY, "test.read", tool("test.read"))
    lease = grant_permission_lease(
        run_id="run-1",
        scope_type="EXACT_TOOLS",
        tool_names=["test.read"],
        granted_by="operator",
        max_uses=1,
        cfg=cfg,
    )["lease"]
    call = pending("test.read", run_id="run-1", cfg=cfg)

    def fail_approval(*args, **kwargs):
        raise ToolRuntimeError("ERROR_TOOL_DECISION_STALE", "simulated stale approval")

    monkeypatch.setattr(permission_leases, "approve_tool_call", fail_approval)

    with pytest.raises(PermissionLeaseError) as exc:
        authorize_tool_call_with_lease(call["tool_call_id"], cfg=cfg)
    assert exc.value.status == "ERROR_TOOL_DECISION_STALE"

    stored = list_permission_leases(run_id="run-1", cfg=cfg)["leases"][0]
    assert stored["lease_id"] == lease["lease_id"]
    assert stored["used_call_ids"] == []
    assert stored["effective_state"] == "ACTIVE"

    audit_path = cfg.processed_dir / "permission_lease_audit.json"
    events = json.loads(audit_path.read_text(encoding="utf-8"))
    call_events = [
        row["event"]
        for row in events
        if row.get("tool_call_id") == call["tool_call_id"]
    ]
    assert call_events == [
        "LEASE_USE_RESERVED",
        "LEASE_USE_RELEASED",
        "LEASE_APPROVAL_FAILED",
    ]
    assert verify_permission_lease_audit_chain(cfg)["status"] == "PERMISSION_LEASE_AUDIT_OK"


def test_reservation_audit_failure_rolls_back_use_ceiling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = make_settings(tmp_path)
    monkeypatch.setitem(TOOL_REGISTRY, "test.read", tool("test.read"))
    lease = grant_permission_lease(
        run_id="run-1",
        scope_type="EXACT_TOOLS",
        tool_names=["test.read"],
        granted_by="operator",
        max_uses=1,
        cfg=cfg,
    )["lease"]
    call = pending("test.read", run_id="run-1", cfg=cfg)

    original_append = permission_leases._append_audit

    def fail_reservation(event, settings):
        if event.get("event") == "LEASE_USE_RESERVED":
            raise OSError("audit storage unavailable")
        return original_append(event, settings)

    monkeypatch.setattr(permission_leases, "_append_audit", fail_reservation)

    with pytest.raises(OSError):
        authorize_tool_call_with_lease(call["tool_call_id"], cfg=cfg)

    stored = list_permission_leases(run_id="run-1", cfg=cfg)["leases"][0]
    assert stored["lease_id"] == lease["lease_id"]
    assert stored["used_call_ids"] == []
    assert stored["effective_state"] == "ACTIVE"
    assert list_tool_calls(cfg)["tool_calls"][0]["status"] == "PENDING_APPROVAL"
    assert verify_permission_lease_audit_chain(cfg)["status"] == "PERMISSION_LEASE_AUDIT_OK"


COMMAND_EXECUTING_TOOLS = ("coding.exec", "coding.regression_run")


@pytest.mark.parametrize("tool_name", COMMAND_EXECUTING_TOOLS)
def test_lease_does_not_extend_to_command_execution_it_does_not_name(tmp_path: Path, tool_name: str) -> None:
    cfg = make_settings(tmp_path)
    # Both command tools are registered non-mutating and local, so a broad lease is the only thing in the way.
    assert TOOL_REGISTRY[tool_name].mutates_state is False and TOOL_REGISTRY[tool_name].network == "none"
    grant_permission_lease(run_id="run-1", scope_type="RUN_READ_ONLY", granted_by="operator", cfg=cfg)
    grant_permission_lease(
        run_id="run-1",
        scope_type="TOOL_PREFIXES",
        tool_prefixes=["coding."],
        allow_mutating=True,
        allow_network=True,
        granted_by="operator",
        cfg=cfg,
    )
    grant_permission_lease(
        run_id="run-1",
        scope_type="EXACT_TOOLS",
        tool_names=["coding.repo_status", "coding.read_file"],
        allow_mutating=True,
        allow_network=True,
        granted_by="operator",
        cfg=cfg,
    )
    call = pending(tool_name, run_id="run-1", cfg=cfg, arguments={"command": ["true"]})

    assert matching_permission_leases(call["tool_call_id"], cfg=cfg)["match_count"] == 0
    with pytest.raises(PermissionLeaseError) as exc:
        authorize_tool_call_with_lease(call["tool_call_id"], cfg=cfg)
    assert exc.value.status == "ERROR_PERMISSION_LEASE_NO_MATCH"
    assert list_tool_calls(cfg)["tool_calls"][0]["status"] == "PENDING_APPROVAL"


def test_exact_lease_covers_only_the_tools_it_names(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    monkeypatch.setitem(TOOL_REGISTRY, "test.named", tool("test.named", mutates=True))
    monkeypatch.setitem(TOOL_REGISTRY, "test.other", tool("test.other", mutates=True))
    lease = grant_permission_lease(
        run_id="run-1",
        scope_type="EXACT_TOOLS",
        tool_names=["test.named"],
        allow_mutating=True,
        granted_by="operator",
        cfg=cfg,
    )["lease"]
    named = pending("test.named", run_id="run-1", cfg=cfg)
    other = pending("test.other", run_id="run-1", cfg=cfg)
    other_run = pending("test.named", run_id="run-2", cfg=cfg)

    assert matching_permission_leases(named["tool_call_id"], cfg=cfg)["matching_lease_ids"] == [lease["lease_id"]]
    assert matching_permission_leases(other["tool_call_id"], cfg=cfg)["match_count"] == 0
    assert matching_permission_leases(other_run["tool_call_id"], cfg=cfg)["match_count"] == 0


def test_lease_expiry_is_enforced_and_unbounded_leases_are_not_grantable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = make_settings(tmp_path)
    monkeypatch.setitem(TOOL_REGISTRY, "test.mutate", tool("test.mutate", mutates=True))
    for bad_ttl in (None, 0, -5, permission_leases.MAX_TTL_SECONDS + 1, True):
        with pytest.raises(PermissionLeaseError) as exc:
            grant_permission_lease(
                run_id="run-1",
                scope_type="EXACT_TOOLS",
                tool_names=["test.mutate"],
                allow_mutating=True,
                granted_by="operator",
                ttl_seconds=bad_ttl,
                cfg=cfg,
            )
        assert exc.value.status == "ERROR_PERMISSION_LEASE_TTL"

    lease = grant_permission_lease(
        run_id="run-1",
        scope_type="EXACT_TOOLS",
        tool_names=["test.mutate"],
        allow_mutating=True,
        granted_by="operator",
        ttl_seconds=60,
        cfg=cfg,
    )["lease"]
    assert lease["expires_at"] is not None
    call = pending("test.mutate", run_id="run-1", cfg=cfg)
    assert matching_permission_leases(call["tool_call_id"], cfg=cfg)["match_count"] == 1

    real_datetime = permission_leases.datetime

    class LaterDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime.now(tz) + permission_leases.timedelta(seconds=61)

    monkeypatch.setattr(permission_leases, "datetime", LaterDatetime)

    assert matching_permission_leases(call["tool_call_id"], cfg=cfg)["match_count"] == 0
    with pytest.raises(PermissionLeaseError) as expired:
        authorize_tool_call_with_lease(call["tool_call_id"], cfg=cfg)
    assert expired.value.status == "ERROR_PERMISSION_LEASE_NO_MATCH"
    assert list_tool_calls(cfg)["tool_calls"][0]["status"] == "PENDING_APPROVAL"
